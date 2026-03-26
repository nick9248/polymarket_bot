# Yield Farming Monitoring — Design Spec
Date: 2026-03-26

## Overview

Full monitoring system for the yield farming bot operating with real capital ($50 Phase 1, $10k+ later). Covers trade lifecycle tracking, risk circuit breakers, Telegram push alerts, and a dedicated web dashboard. Completely isolated from the existing copy-trade (stingo43) infrastructure.

---

## Architecture

Approach B: dedicated monitoring layer with clean separation.

```
main.py (--yield-farming mode)
  │
  ├── risk_guard_service.py      ← check before every trade
  ├── yield_farming_service.py   ← scan + submit (writes to yield_trades)
  └── monitor_service.py         ← called every cycle, polls lifecycle, fires alerts
        │
        ├── core/database/repository.py   ← yield_trades table
        ├── telegram_service.py           ← new yield alert functions
        └── Polymarket positions/activity API
```

```
monitoring/app.py  (port 5051, separate process)
  └── reads from yield_trades table via repository.py
```

The existing copy-trade path (`--wallets stingo43`), `trader_trades` table, and `web_page/app.py` (port 5050) are untouched.

---

## 1. Database Schema

New table: `yield_trades` — source of truth for both the bot and the dashboard.

```sql
CREATE TABLE IF NOT EXISTS yield_trades (
    id                    SERIAL PRIMARY KEY,
    token_id              TEXT NOT NULL,
    condition_id          TEXT NOT NULL,
    title                 TEXT,
    outcome               TEXT,
    signal_price          NUMERIC(6,4),
    fill_price            NUMERIC(6,4),
    shares                INTEGER,
    cost_usd              NUMERIC(10,4),
    status                TEXT NOT NULL DEFAULT 'submitted',
    clob_order_id         TEXT,
    submitted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at           TIMESTAMPTZ,
    settled_at            TIMESTAMPTZ,
    pnl_usd               NUMERIC(10,4),
    session_balance_start NUMERIC(10,2),
    balance_before        NUMERIC(10,2)
);
```

**Status flow:** `submitted` → `filled` → `won` | `lost` → (settled_at set when USDC confirmed)

`error` status is written if the CLOB rejects the order after submission (unexpected non-success response).

---

## 2. Risk Guard Service

`service/risk_guard_service.py` — pure decision layer. Single entry point called before every yield trade. No API calls, no side effects.

### Circuit Breakers

All three must pass. First failure halts trading and fires a Telegram alert.

**1. Balance floor**
- Stop if: `current_balance < YIELD_BALANCE_FLOOR`
- Phase 1 default: `$5`

**2. Consecutive losses**
- Stop if: last `YIELD_MAX_CONSECUTIVE_LOSSES` rows in `yield_trades` all have `status='lost'`
- Default: `3`

**3. Session drawdown**
- Stop if: `(session_start_balance - current_balance) / session_start_balance > YIELD_MAX_DRAWDOWN_PCT / 100`
- Phase 1 default: `10%` (= $5 max loss on $50)

### Configuration (`.env`)

```
YIELD_BALANCE_FLOOR=5
YIELD_MAX_CONSECUTIVE_LOSSES=3
YIELD_MAX_DRAWDOWN_PCT=10
```

### Interface

```python
@dataclass
class RiskStatus:
    allowed: bool
    reason: str | None  # None if allowed, human-readable if blocked

def check_risk(current_balance: float, session_start_balance: float) -> RiskStatus:
    """
    Single entry point. Returns immediately on first failing check.
    Reads yield_trades table via db_service for the consecutive loss check
    (read-only, no mutations).
    """
```

---

## 3. Monitor Service

`service/monitor_service.py` — called every cycle after yield trades are submitted. Polls Polymarket APIs to advance trade lifecycle statuses, then fires Telegram alerts for state transitions.

### Lifecycle Polling Logic

For each row in `yield_trades` with `status IN ('submitted', 'filled')`:

1. Call `GET /positions?user=<our_wallet>` — if token found: update `status='filled'`, record `fill_price`
2. If token NOT in open positions and market end time has passed:
   - Call `GET /closed-positions?user=<our_wallet>`
   - If found with `currentValue > 0`: `status='won'`, `pnl_usd = currentValue - cost_usd`, set `resolved_at`
   - If found with `currentValue = 0`: `status='lost'`, `pnl_usd = -cost_usd`, set `resolved_at`
3. Settlement confirmation: if `resolved_at` is set and `NOW() - resolved_at > 30 minutes`: set `settled_at = NOW()`. Polymarket settles promptly; the 30-minute delay is a conservative buffer, not balance-delta verification (which is unreliable due to concurrent balance changes).

Monitor runs at most once per cycle. Trades older than 24h still unresolved are flagged as `error` with a Telegram alert.

### Telegram Alerts

New functions added to `telegram_service.py`:

| Trigger | Alert Content |
|---|---|
| Trade submitted | Market title, outcome, fill price, shares, cost, balance remaining |
| Trade won | Market, outcome, pnl (+$X), win %, running session net P&L |
| Trade lost | Market, outcome, loss (-$X), running session net P&L |
| Risk guard blocked | Which breaker triggered, current values vs thresholds, trading halted |
| Balance warning | Fires when balance < 2× floor. Current balance, trades remaining estimate |
| Trade stuck >24h | trade details, manual review required |
| Daily summary (23:00 UTC) | Trades, win rate, gross/net P&L, ending balance |
| Bot crash/unhandled exception | Exception message, traceback tail |

---

## 4. Monitoring Dashboard

Separate Flask application: `monitoring/app.py` on port **5051**.
Separate static file: `monitoring/index.html`.

### API Endpoints

```
GET /api/status       — bot mode, uptime, risk guard states (all three), last cycle time
GET /api/balance      — current USDC, session start balance, drawdown %, floor warning flag
GET /api/trades       — yield_trades rows, paginated, filterable by status and date
GET /api/pnl/summary  — total trades, won, lost, win rate, gross P&L, net P&L
GET /api/pnl/chart    — [{date, cumulative_pnl}] for chart rendering
GET /api/risk         — per-breaker state: current value, threshold, triggered bool
```

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

Auto-refreshes via `setInterval` polling the Flask API. No WebSockets required.

---

## 5. Integration in main.py (yield farming path)

Each yield farming cycle:

```
1. monitor_service.poll_lifecycle()       ← advance statuses from previous cycles
2. risk_guard_service.check_risk(...)     ← all three breakers
   → if blocked: log + Telegram alert, skip execution, continue sleeping
3. yield_farming_service.run_yield_farming_cycle(...)
   → internally calls execute_yield_trade() which now returns (success: bool, order_id: str | None)
   → on success: run_yield_farming_cycle writes row to yield_trades (status='submitted', clob_order_id set)
   → on failure: run_yield_farming_cycle writes row to yield_trades (status='error', clob_order_id=None)
4. monitor_service.send_daily_summary_if_due()   ← checks if 23:00 UTC passed
```

---

## 6. Deployment

Two systemd services on the Spain VPS:

```
polymarket-bot.service        — main.py --yield-farming (existing service, updated command)
polymarket-monitor.service    — monitoring/app.py (new service, port 5051)
```

The monitor dashboard reads from the same PostgreSQL instance. No additional infrastructure needed.

---

## 7. What This Does NOT Cover (out of scope)

- Closing positions manually (separate to-do item)
- Authentication/password on the dashboard (VPS is already firewalled; low priority for Phase 1)
- Copy-trade monitoring (stingo43 path unchanged)
- Multiple simultaneous yield farming sessions

---

## Phase 1 Parameters Summary

| Parameter | Value |
|---|---|
| Budget | $50 |
| Balance floor | $5 |
| Consecutive loss halt | 3 |
| Drawdown halt | 10% ($5) |
| Trade size | max($1, balance × 1%) |
| Threshold | 0.95 |
| Window | 5 min |
| Dashboard port | 5051 |
| Daily summary time | 23:00 UTC |
