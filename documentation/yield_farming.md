# Yield Farming Bot — Documentation

## What It Is

A fully automated trading strategy that exploits a mechanical inefficiency in Polymarket's short-duration binary markets. When a binary market is within minutes of closing and one outcome is trading at ≥ 0.95 on the CLOB, the outcome is near-certain. Buying it at 0.95–0.98 and collecting $1.00 at settlement produces a 2–5% return per trade with minimal directional risk.

No prediction is required. The strategy is purely mechanical: find near-certain outcomes, buy them, wait for resolution.

---

## Market Type: Up/Down Price-Direction Markets

The bot exclusively targets **Up/Down markets** — Polymarket's short-duration crypto and asset price direction markets.

**Format:** `"Bitcoin Up or Down - March 28, 5PM ET"` / `"Solana Up or Down - March 28, 5:45PM-6:00PM ET"`

**Why these markets:**
- Price direction is typically locked in by the last few minutes (the price has already moved up or down)
- The "losing" outcome drops to near-zero, the "winning" outcome rises to near-one
- Short duration (5–15 minute windows) means fast settlement and high trade throughput
- Covers Bitcoin, Ethereum, Solana, XRP, Dogecoin, BNB, and any asset Polymarket adds in this format

**Filter logic:** `"up or down" in title.lower()` — catches all assets automatically without a ticker list.

**What it excludes:** sports, politics, ETF flows, multi-outcome markets, longer-duration markets.

---

## Execution Flow

Each cycle (every 5 seconds):

```
1. Query Gamma API for markets closing in next 5 minutes
2. Filter: "up or down" in title + outcome price ≥ 0.95 + close_time > now (client-side, Gamma ignores end_date_min)
3. For each candidate: fetch live CLOB price via /markets/{condition_id}
4. Re-check CLOB price ≥ 0.95 (Gamma prices lag — CLOB is authoritative)
5. Risk guard: check balance floor, consecutive losses, session drawdown
6. Submit BUY limit order on the CLOB at current price
7. Record to yield_trades DB table (success or failure)
8. Send Telegram alert on success
9. Monitor lifecycle: submitted → filled → won/lost → settled
```

---

## Order Sizing

```
budget_usd = max($1.00 minimum, current_balance × 1%)
shares     = max(market_minimum_order_size, floor(budget_usd / price), ceil($1.00 / price))
```

- **1% of balance per trade** — conservative, preserves capital
- **$1.00 notional floor** — CLOB rejects orders below $1
- **Market minimum order size** — fetched from CLOB `/markets/{condition_id}` (typically 5 shares)
- **$6.00 hard cap** — if minimum order cost exceeds $6, skip rather than deploy unintended capital
- The higher of all three constraints wins

**Example at $20 balance:**
- 1% = $0.20 → floored to $1.00
- Min shares = 5, price = $0.97 → cost = $4.85 → within $6 cap → order placed

---

## Price Validity Rules

| Condition | Action |
|---|---|
| CLOB price < 0.95 | Skip — below threshold |
| CLOB price ≥ 0.99 | Skip — CLOB rejects orders at 0.99+ |
| Slippage > 10% vs signal | Skip — stale or fast-moving market |
| Order book empty | Skip — market already closed for trading |

The valid buy range is **0.95–0.989**.

---

## Risk Guard — Circuit Breakers

Three independent circuit breakers checked before every trade. All three must pass. First failure halts trading and sends a Telegram alert.

| Breaker | Default | Trigger |
|---|---|---|
| Balance floor | $5 | `current_balance < $5` |
| Consecutive losses | 3 | Last 3 DB rows all `status='lost'` |
| Session drawdown | 10% | `(start_balance - current_balance) / start_balance > 10%` |

Configured via `.env`:
```
YIELD_BALANCE_FLOOR=5
YIELD_MAX_CONSECUTIVE_LOSSES=3
YIELD_MAX_DRAWDOWN_PCT=10
```

**Known gap:** `session_start_balance` is re-read from the CLOB API on each bot restart, which resets the drawdown circuit breaker. A restart after a large loss effectively bypasses it.

---

## Trade Lifecycle

```
submitted  →  filled  →  won / lost  →  (settled_at set after 30min)
                                         ↓
                          error  (CLOB rejected, order book empty, price invalid)
```

`monitor_service.py` polls the Polymarket positions and closed-positions APIs each cycle to advance statuses:

1. **submitted → filled**: token appears in `/positions?user=<wallet>`
2. **filled → won**: token in `/closed-positions` with `currentValue > 0` after market end time
3. **filled → lost**: token in `/closed-positions` with `currentValue = 0` after market end time
4. **settled_at**: set 30 minutes after `resolved_at` (conservative settlement buffer)
5. **error**: CLOB rejection, empty order book, invalid price, or trade stuck > 24h

---

## Deduplication

A module-level set `_executed_token_ids` prevents re-entry into the same market within a session. The token ID is added **unconditionally** — on both success and failure — immediately before the success/failure branch. This prevents failed trades (e.g. price just crossed 0.99) from being retried every 5 seconds until the market closes.

The set resets on bot restart.

---

## When Markets Are Available

Up/Down markets run during specific Polymarket sessions tied to US and global market hours. Outside these windows, the bot runs but finds nothing and logs `"no qualifying opportunities found"` each cycle. This is normal.

Typical sessions observed:
- Pre-market: ~7–9 AM ET (5-minute intervals)
- Market hours: ~9:30 AM – 4 PM ET (5-minute intervals)
- After-hours: occasional sessions (e.g. 5–6 PM ET)

The bot does not need to be stopped between sessions — it polls continuously and picks up the next session automatically.

---

## Key Files

| File | Purpose |
|---|---|
| `service/yield_farming_service.py` | Scan, filter, submit, record |
| `service/copy_trade_service.py` | CLOB order execution, order sizing |
| `service/risk_guard_service.py` | Circuit breaker checks |
| `service/monitor_service.py` | Lifecycle polling, daily summary |
| `core/models/yield_opportunity.py` | YieldOpportunity dataclass |
| `core/database/repository.py` | yield_trades SQL (insert, status update, lifecycle queries) |

---

## Phase 1 Parameters

| Parameter | Value |
|---|---|
| Starting budget | $50 |
| Trade size | max($1, balance × 1%) |
| Hard cap per trade | $6 |
| Price threshold | 0.95 |
| Scan window | 5 minutes before close |
| Balance floor | $5 |
| Drawdown halt | 10% |
| Consecutive loss halt | 3 |
| Polling interval | 5 seconds |
