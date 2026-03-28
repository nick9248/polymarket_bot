# Yield Farming Monitoring — Documentation

## Overview

The monitoring system tracks every yield farming trade from submission through settlement, enforces risk circuit breakers, pushes Telegram alerts on state changes, and exposes a web dashboard for live visibility. It is fully isolated from the copy-trade infrastructure.

---

## Architecture

```
main.py (--yield-farming mode)
  │
  ├── risk_guard_service.py      ← checked before every trade
  ├── yield_farming_service.py   ← scan + submit (writes to yield_trades)
  └── monitor_service.py         ← called every cycle: lifecycle + daily summary
        │
        ├── core/database/repository.py   ← yield_trades table
        ├── telegram_service.py           ← yield-specific alert functions
        └── Polymarket /positions + /closed-positions APIs

monitoring/app.py  (port 5051, separate process)
  └── reads yield_trades via repository.py
```

The existing copy-trade path (`--wallets`), `trader_trades` table, and dashboard on port 5050 are untouched.

---

## Database Table: `yield_trades`

Single source of truth for both the bot and the dashboard.

```sql
CREATE TABLE IF NOT EXISTS yield_trades (
    id                    SERIAL PRIMARY KEY,
    token_id              TEXT NOT NULL,
    condition_id          TEXT NOT NULL,
    title                 TEXT,
    outcome               TEXT,
    signal_price          NUMERIC(6,4),    -- Gamma/CLOB price at scan time
    fill_price            NUMERIC(6,4),    -- CLOB price at order submission
    shares                INTEGER,
    cost_usd              NUMERIC(10,4),   -- shares × fill_price
    status                TEXT NOT NULL DEFAULT 'submitted',
    clob_order_id         TEXT,
    submitted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at           TIMESTAMPTZ,     -- set when won/lost determined
    settled_at            TIMESTAMPTZ,     -- set 30min after resolved_at
    pnl_usd               NUMERIC(10,4),   -- positive = win, negative = loss
    session_balance_start NUMERIC(10,2),   -- balance when bot session started
    balance_before        NUMERIC(10,2)    -- balance at time of this trade
);
```

**Status flow:**
```
submitted → filled → won    → (settled_at set after 30min)
                   → lost
         → error   (CLOB rejection, empty order book, stuck > 24h)
```

---

## monitor_service.py

Called every cycle in `main.py` after trade submission. Two responsibilities:

### 1. Lifecycle Polling

For each row with `status IN ('submitted', 'filled')`:

| Step | API Call | Result |
|---|---|---|
| Check open | `GET /positions?user=<wallet>` | If token found → `status='filled'` |
| Check closed (post-expiry) | `GET /closed-positions?user=<wallet>` | `currentValue > 0` → `won`; `= 0` → `lost` |
| Settlement buffer | no API call | 30min after `resolved_at` → set `settled_at` |
| Stuck trade | no API call | `submitted_at` > 24h ago → `error` + Telegram alert |

### 2. Daily Summary

At 23:00 UTC each day, fires one Telegram message with: total trades, win count, loss count, win rate, gross P&L, net P&L, ending balance.

---

## risk_guard_service.py

Pure decision layer — no API calls, no side effects. Called before every trade attempt.

### Interface

```python
@dataclass
class RiskStatus:
    allowed: bool
    reason: str | None  # None if allowed, message if blocked

def check_risk(current_balance: float, session_start_balance: float) -> RiskStatus:
    ...
```

### Circuit Breakers (all three must pass)

**1. Balance floor**
Stop if `current_balance < YIELD_BALANCE_FLOOR` ($5 default).
Prevents trading into zero.

**2. Consecutive losses**
Stop if the last N rows in `yield_trades` all have `status='lost'` (N=3 default).
Signals something is systematically wrong (price model, CLOB behaviour, settlement issue).

**3. Session drawdown**
Stop if `(session_start_balance - current_balance) / session_start_balance > YIELD_MAX_DRAWDOWN_PCT / 100` (10% default).
Hard daily loss limit regardless of balance size.

### Configuration

```
YIELD_BALANCE_FLOOR=5
YIELD_MAX_CONSECUTIVE_LOSSES=3
YIELD_MAX_DRAWDOWN_PCT=10
```

---

## Telegram Alerts

New functions in `telegram_service.py` for yield-specific events. All alerts go to the same chat as copy-trade alerts.

| Trigger | Content |
|---|---|
| Trade submitted | Market title, outcome, fill price, shares, cost, balance after |
| Trade won | Market, outcome, P&L (+$X), win%, running session net |
| Trade lost | Market, outcome, loss (-$X), running session net |
| Risk guard blocked | Which breaker triggered, current values vs thresholds |
| Balance warning | Fires when `balance < 2 × floor`; current balance, estimated trades remaining |
| Trade stuck > 24h | Trade details, manual review prompt |
| Daily summary | Trades, win rate, gross/net P&L, ending balance |
| Bot crash | Exception message, traceback tail |

---

## Web Dashboard — port 5051

Separate Flask process: `monitoring/app.py`. Auto-refreshes every 10 seconds via `setInterval` polling. No WebSockets required.

### Flask API Endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/status` | Bot mode, uptime, last cycle time |
| `GET /api/balance` | Current USDC, session start balance, drawdown %, floor warning flag |
| `GET /api/trades` | yield_trades rows, paginated, filterable by status and date |
| `GET /api/pnl/summary` | Total trades, won, lost, win rate, gross P&L, net P&L |
| `GET /api/pnl/chart` | `[{date, cumulative_pnl}]` for chart rendering |
| `GET /api/risk` | Per-breaker state: current value, threshold, triggered bool |

### Dashboard Layout

```
┌─────────────────┬──────────────────┬─────────────────┐
│  Bot Status     │  USDC Balance    │  Risk Guards    │
│  Running/Halted │  $49.10 / $50    │  ✅ Floor OK    │
│  Uptime: 2h 14m │  Drawdown: 1.8%  │  ✅ Losses: 0/3 │
│  Mode: Yield    │  Floor: $5       │  ✅ DD: 1.8/10% │
└─────────────────┴──────────────────┴─────────────────┘
┌──────────────────────────────────────────────────────┐
│  Session P&L — Cumulative line chart                 │
│  Net: -$0.90  |  Won: 44  Lost: 3  |  Rate: 93.6%   │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Recent Trades  (auto-refresh every 10s)             │
│  Time | Market | Outcome | Price | Cost | Status|P&L │
└──────────────────────────────────────────────────────┘
```

### Deployment

Two systemd services on the Spain VPS:

```
polymarket-bot.service        — main.py --yield-farming (existing)
polymarket-monitor.service    — monitoring/app.py (port 5051)
```

Access dashboard: `http://<vps-ip>:5051`

---

## Cycle Integration in main.py

Each yield farming cycle runs in this order:

```
1. monitor_service.poll_lifecycle()         ← advance statuses from previous trades
2. risk_guard_service.check_risk(...)       ← all three breakers
   → if blocked: log + Telegram alert, skip execution, sleep
3. yield_farming_service.run_yield_farming_cycle(...)
   → submits orders, writes yield_trades rows
4. monitor_service.send_daily_summary_if_due()   ← checks if 23:00 UTC passed
```

---

## Known Limitations

- **Risk guard reset on restart**: `session_start_balance` is re-read from the CLOB API on startup, resetting the drawdown circuit breaker. A restart after a loss effectively clears the drawdown counter.
- **Dashboard has no authentication**: acceptable for Phase 1 since the VPS is firewalled, but should be added before exposing publicly.
- **Settlement detection**: uses a 30-minute buffer after `resolved_at` rather than verifying the USDC balance delta. Reliable in practice (Polymarket settles promptly) but not cryptographically confirmed.
- **Manual position close**: not supported. Positions held to settlement only.
