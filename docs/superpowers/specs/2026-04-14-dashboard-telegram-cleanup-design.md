# Design: Dashboard Enhancement, Telegram Menu, Code Cleanup
**Date:** 2026-04-14  
**Status:** Approved

---

## Scope

Six parallel work streams:

1. Code cleaning and CLAUDE.md alignment check
2. Documentation update and cleanup
3. `error_analysis.md` — 4 new patterns
4. Commit and push all changes
5. Monitoring dashboard enhancements (Option A — Enhanced 3-Page)
6. Telegram persistent keyboard + command list

---

## 1. Code Cleaning & CLAUDE.md Alignment

Audit all service and core files against the CLAUDE.md checklist:
- Layered architecture: Core → Service → main.py
- No business logic in main.py
- Each function does one thing
- Code in the right layer
- Naming conventions (descriptive, no abbreviations, leading `_` for private)

Files to audit: `main.py`, `service/yield_farming_service.py`, `service/monitor_service.py`, `service/copy_trade_service.py`, `service/telegram_service.py`, `service/db_service.py`, `core/database/repository.py`.

---

## 2. Documentation Cleanup

- Review and update `documentation/fix_versions.md` and `documentation/optimization_ideas.md`
- Ensure they reflect current state (direction filter, Option B, stop-loss retry, pnl fix)
- No new files — update existing docs only

---

## 3. error_analysis.md — 4 New Patterns

**Pattern 005 — Stop-loss CLOB Collateral Failure**  
Status: Fixed. Root cause: `get_balance_allowance()` returns total proxy wallet balance, not CLOB internal collateral. Pre-check always passed, sell failed at API. Fix: retry loop — attempt full shares, catch 400 "not enough balance", reduce by 1, retry.

**Pattern 006 — Inflated P&L from Polymarket `cashPnl` Field**  
Status: Fixed. Root cause: Polymarket's `cashPnl` uses market's inception `initialValue` not our fill price — returns grossly inflated figures for trades entered near resolution. Fix: calculate pnl as `shares × $1.00 − cost_usd` using our own DB data.

**Pattern 007 — Hourly Market Entry Timing Risk (Option B)**  
Status: Fixed. Root cause: hourly markets entered at 8-15 min to close had 3× higher loss rate than short-window markets. Fix: `_SHORT_WINDOW_RE` regex detects market type; hourly markets capped at ≤3 min to close.

**Pattern 008 — Irreducible Binary-Flip Losses**  
Status: Acknowledged, no fix possible. Pattern: market priced at 96-97% right up to 1 min before expiry, then binary flip at resolution (e.g. trade 10672 XRP 4AM ET). curPrice is high until the market resolves — neither entry guard nor stop-loss can detect this. Accepted as structural risk of the yield farming strategy.

---

## 5. Monitoring Dashboard — Option A: Enhanced 3-Page

### Architecture
- Keep existing 3-page structure (index.html, analytics.html, health.html)
- Add auto-refresh every 5s via `setInterval` + `fetch` on all pages
- All new data served by new/extended API endpoints in `monitoring/app.py`
- No WebSocket — polling is sufficient at 5s interval

### New API Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/guards/status` | Live guard state: direction, current RV value, hourly cap, stop-loss threshold, RV blocked duration |
| `GET /api/trades/active` | Open/filled trades with minutes_remaining computed server-side |
| `GET /api/stats/streak` | Current win streak, best streak, stop-loss savings total |
| `GET /api/analytics/market-types` | Short-window vs hourly breakdown: count, win rate, avg pnl, hourly skips |
| `GET /api/analytics/guard-stats` | Per-guard fire counts since bot start (from yield_trades + heartbeat) |

### Overview Page Changes (index.html)

**New sections added above existing trade table:**

1. **Guard Status Panel** — row of pills, one per guard. Green when clear, amber when blocking. RV pill shows live value (e.g. `RV: 0.38 / 0.50 ✓`). Fetches `/api/guards/status` every 5s.

2. **Active Positions Monitor** — card showing all submitted/filled trades. Each row: market title, status, minutes remaining (counted down live), fill price. Amber highlight when curPrice approaches stop-loss threshold. Fetches `/api/trades/active` every 5s.

3. **Win Streak + Stop-loss Savings** — two new stat cards inserted into existing stats row. Win streak with 🔥 emoji + best streak. Stop-loss savings: total USD recovered vs full-loss alternative. Fetches `/api/stats/streak`.

4. **Live Trade Feed** — existing paginated table replaced with auto-updating feed. New trades prepended at top without page reload. Color-coded rows: green=won, amber=stopped, red=lost, grey=expired. Fetches `/api/trades?limit=20` every 5s.

### Analytics Page Changes (analytics.html)

**Two new sections appended after existing charts:**

5. **Market Type Breakdown** — two columns (short-window / hourly) + a third for hourly-skipped. Shows: trade count, win rate, avg P&L. Powered by `/api/analytics/market-types`.

6. **Guard Effectiveness Stats** — four counters: Direction blocks, RV blocks, Hourly skips, Stop-loss triggers. Powered by `/api/analytics/guard-stats`.

### Health Page Changes (health.html)

7. **RV Guard Health Check** — new scored check (5 pts): passes if RV guard has not been continuously blocking for >30 min. Fails with detail showing current RV value and how long it's been blocking. To keep max score at 100: Stuck Trades check reduced from 10 pts to 5 pts (still meaningful, just smaller weight).

### Backend: New DB Queries (repository.py + db_service.py)

- `get_win_streak()` — counts consecutive wins from most recent resolved trade backwards
- `get_best_streak()` — max consecutive wins ever
- `get_stop_loss_savings()` — sum of (cost_usd - abs(pnl_usd)) for all stopped trades
- `get_active_trades()` — yield_trades WHERE status IN ('submitted','filled')
- `get_market_type_stats()` — group by market type regex, aggregate win/loss/pnl
- `get_stop_loss_trigger_count()` — COUNT of stopped trades from yield_trades (reliable, no extra storage needed)
- Guard fire counts for direction/RV/hourly skips are not stored in current schema — the guard stats panel shows: stop-loss triggers (from DB), expired trades (proxy for unfilled orders), and current RV from heartbeat. Exact per-guard skip counters deferred — would require new heartbeat columns.

### Guard status data source
The `/api/guards/status` endpoint reads:
- Direction: from env `YIELD_DIRECTION_FILTER`
- RV threshold: from env `YIELD_MAX_REALIZED_VOL`  
- Current RV value: stored in `bot_heartbeat.last_rv_value` (new column) — bot writes it each cycle
- Hourly cap: from env or hardcoded `_MAX_MINS_HOURLY = 3.0`
- Stop-loss threshold: hardcoded `_STOP_LOSS_THRESHOLD = 0.50`
- RV blocked since: stored in `bot_heartbeat.rv_blocked_since` (new column)

Two new columns added to `bot_heartbeat`: `last_rv_value NUMERIC`, `rv_blocked_since TIMESTAMPTZ`.

---

## 6. Telegram Menu

### Persistent Reply Keyboard — 2×3 Grid

Sent with `reply_markup` on bot startup and after each command response.

```
[ 🏥 Health ]  [ 💰 Balance ]  [ 📊 Summary ]
[ 📋 Trades ]  [ 🔄 Reset Risk ]  [ 🧪 Test  ]
```

Reset Risk button styled distinctly (red if possible via Telegram — not possible natively, but can use a ⚠️ prefix).

Implementation: `send_message()` gains an optional `reply_markup` parameter. A new `send_keyboard()` function sends an empty message with the keyboard attached (called once at bot startup). Each command handler response also re-attaches the keyboard.

### BotFather Command Registration

Registered via `setMyCommands` API call at bot startup (idempotent):

```
/health     - Bot uptime, cycle count, DB and geo status
/balance    - Current USDC balance and drawdown  
/summary    - Session P&L, win rate, trade count
/trades     - Last 5 resolved trades with P&L
/reset_risk - Reset risk guard and resume trading
/test       - Run a live order test on the CLOB
```

### New Commands (main.py dispatch)

**`/balance`** — calls `db_service.get_bot_heartbeat()`, formats balance + drawdown, sends via `telegram_service.send_balance_status()`.

**`/summary`** — calls `db_service.get_yield_pnl_summary()`, formats session P&L, win rate, trade count, sends via `telegram_service.send_session_summary()`.

**`/trades`** — calls `db_service.get_yield_trades_page(limit=5)`, formats last 5 trades with status and P&L, sends via `telegram_service.send_recent_trades()`.

---

## Implementation Order

1. Code cleaning + CLAUDE.md audit (no new features, just fixes)
2. error_analysis.md + documentation updates  
3. DB schema additions (2 new columns on bot_heartbeat)
4. New repository queries + db_service wrappers
5. New monitoring API endpoints (monitoring/app.py)
6. Frontend: Overview page enhancements
7. Frontend: Analytics page enhancements  
8. Frontend: Health page enhancement
9. Telegram: keyboard + command registration + 3 new commands
10. Commit and push
