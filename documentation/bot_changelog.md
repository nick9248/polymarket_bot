# Bot Configuration Changelog & Performance Windows

This document is the single source of truth for what changed, when, and how to
measure performance after each change. When checking "how has the bot done since
the last change", look up the last epoch below and compare only from its start date.

---

## How to read this document

Each **epoch** starts the moment a config or code change is deployed to the VPS.
Performance comparisons must be scoped to a single epoch. Do not mix metrics across
epochs — different filters produce structurally different trade populations.

**Quick lookup** — to check "since the last change":
- Find the last epoch with a Start timestamp
- Query `yield_trades WHERE submitted_at >= '<start>'`

---

## Epoch 0 — Copy-trade mode (pre yield farming)

**Period:** Project start → 2026-03-26  
**Mode:** copy-trade daemon (stingo43 leaderboard)  
**Yield trades:** N/A  
**Notes:** Bot tracked top-trader wallets and mirrored trades. No yield farming.

---

## Epoch 1 — Yield farming launched (all direction filter off)

**Period:** 2026-03-26 → 2026-04-05  
**Key params:**
- threshold = 0.95
- window = 15 min (service default, no CLI override)
- direction = both (Up + Down)
- No session filter, no two-phase filter, no ceiling guard

**Losses (5 total — all "Down" bets):**

| Date (UTC) | Market | Direction | Signal | Loss |
|------------|--------|-----------|--------|------|
| 2026-03-29 23:02 | ETH | Down | 0.975 | −$4.70 |
| 2026-03-29 23:02 | BTC | Down | 0.955 | −$4.65 |
| 2026-03-31 19:02 | XRP | Down | 0.965 | −$4.75 |
| 2026-04-01 01:04 | BTC | Down | 0.966 | −$4.78 |
| 2026-04-02 04:03 | DOGE | Down | 0.975 | −$4.90 |

All losses in a bullish macro period (late Mar – early Apr 2026). Correlation:
BTC/ETH/XRP all moved together. Not random — directional macro exposure.

**Win rate:** ~94% overall (100% on Up bets, ~84% on Down bets)  
**Capital events:** Balance hit $0.82 (~Apr 3). Risk guard triggered on Mar 27 and Mar 29.

---

## Epoch 2 — Two-phase filter + session filter + ceiling 0.98→0.985 (2026-04-05)

**Period:** 2026-04-05 → 2026-04-06  
**Key changes:**
- Session filter added: block 00:00–13:30 UTC (outside 9:30AM–8PM ET)
- Two-phase filter: detect at T>5 min, execute only if also ≥ threshold at T≤5 min
- Ceiling raised: 0.98 → 0.985

**Effect:** Near-zero execution. 15-min markets only reach 0.95 in final 2–3 min
(always at phase-2 time, never cached at phase 1). Combined with session filter,
almost nothing executed.

---

## Epoch 3 — Two-phase filter removed, session filter disabled, ceiling 0.989 (2026-04-06)

**Start:** 2026-04-06 16:07 UTC (balance refunded to $30.84)  
**Key params:**
- threshold = 0.95
- window = 5 min ← CLI default was 5 (bug — intended 10 or 15)
- direction = both (Up + Down)
- `_MAX_CLOB_PRICE = 0.989` (ceiling guard)
- Session filter disabled (`_SESSION_START_UTC_MINUTES = 0`)
- Two-phase filter: removed
- `_MAX_MINS_HOURLY = 3.0` (hourly markets: enter only at ≤3 min to close)
- `_MAX_REALIZED_VOL = 0.50` (skip if BTC 30-min RV > 0.50)
- Correlation guard active: 1 trade per 5-min close window per session
- YIELD_BALANCE_FLOOR = 5 (halt if USDC < $5)
- YIELD_MAX_CONSECUTIVE_LOSSES = 3
- YIELD_MAX_DRAWDOWN_PCT = 10

**Performance (Apr 8–15, resolved trades only):**

| Date | Wins | Losses | Stopped | Win rate |
|------|------|--------|---------|----------|
| Apr 8 | 79 | 0 | 0 | 100% |
| Apr 9 | 71 | 3 | 0 | 95.9% |
| Apr 10 | 46 | 5 | 0 | 90.2% |
| Apr 11 | 70 | 1 | 0 | 98.6% |
| Apr 12 | 49 | 2 | 0 | 96.1% |
| Apr 13 | 44 | 1 | 1 | 97.8% |
| Apr 14 | 32 | 0 | 4 | 100%* |
| Apr 15 | 23 | 0 | 0 | 100% (partial day) |

*Apr 14 "stopped" = risk guard blocked entries after seeing rapid fills at 0.28–0.50
despite 0.95+ signals (last-minute orderbook collapse, not the consecutive-loss guard).

**Apr 9–13 loss direction breakdown:**
- Down losses: 7 (BTC Down, SOL Down, BNB Down, DOGE Down, ETH Down ×2, XRP Down)
- Up losses: 5 (XRP Up, ETH Up, XRP Up, BNB Up, BTC Up)

Direction bias from Epoch 1 is GONE — losses now distributed across both directions.
Macro bullish period ended; losses are now market-specific volatility events.

**Overall win rate Epoch 3:** 496 won / 513 resolved = **96.7%** (need 97% to break even)

**Balance trajectory:**
- Start: $30.84 (Apr 6)
- Peak: ~$133+ (Apr 15)
- At halt Apr 8 08:02 UTC: $4.58 (balance floor — won positions not yet redeemed)
- After redemptions: rebuilt to $112–$133

**Incident — 0.98 bug (2026-04-15 13:37 UTC):**
Commit `e9e5f7c` accidentally set `_MAX_CLOB_PRICE = 0.98` (should be 0.989).
Markets at $0.98–$0.989 are primary entry points; at $0.975 and below, orderbooks
are empty. Caused ~4.75 hours of zero trading (13:37–18:23 UTC Apr 15).

**Fix:** 0.989 restored at 2026-04-15 18:23 UTC (process 2298539 → 2319761).

---

## Epoch 4 — Window fixed to 10 min, db_service session_start_time fix (2026-04-16)

**Start:** 2026-04-16 06:32 UTC (process 2371691)  
**Key params:**
- threshold = 0.95
- **window = 10 min** ← FIXED (was erroneously 5 in CLI default)
- direction = up only (`YIELD_DIRECTION_FILTER = "up"` in .env)
- `_MAX_CLOB_PRICE = 0.989`
- `_MAX_MINS_HOURLY = 3.0`
- `_MAX_REALIZED_VOL = 0.50`
- Session filter: disabled
- Two-phase filter: removed
- Correlation guard: active
- YIELD_BALANCE_FLOOR = 5
- YIELD_MAX_CONSECUTIVE_LOSSES = 3
- YIELD_MAX_DRAWDOWN_PCT = 10

**What changed from Epoch 3:**
- main.py `--window` default: 5 → 10 min (more markets in scan window)
- service/db_service.py and core/database/repository.py synced with local
  (adds `session_start_time` param to `update_bot_heartbeat`)

**Session start balance:** $126.05 USDC  

**Performance:** Tracking from 2026-04-16 06:32 UTC.  
→ Run: `SELECT * FROM yield_trades WHERE submitted_at >= '2026-04-16 06:32:00';`

---

## Epoch 5 — Telegram fixes, direction both, /balance /summary /trades commands (2026-04-16)

**Start:** 2026-04-16 09:53 UTC (process 2401944)  
**Key params:** Same as Epoch 4 except:
- **direction = both** (Up + Down) ← changed from "up"

**What changed from Epoch 4:**
- `YIELD_DIRECTION_FILTER = "both"` in .env — Down bets re-enabled
- Telegram `send_message()` now logs at INFO level (was DEBUG — invisible in journalctl)
- `P&L` → `P&amp;L` HTML escape fixed in won/lost/stop-loss/summary alerts
- `/balance`, `/summary`, `/trades` command handlers added to main.py
- `send_risk_guard_reset` and `send_stop_loss_triggered` restored to telegram_service.py
  (were missing from VPS version)
- VPS telegram_service.py keyboard + command registration synced to local

**Session start balance:** $116.22 USDC (fresh session — DB heartbeat was 33h old, outside 12h restore window)

**Performance:** Tracking from 2026-04-16 09:53 UTC.  
→ Run: `SELECT * FROM yield_trades WHERE submitted_at >= '2026-04-16 09:53:00';`

**Apr 16 notable events (Epoch 5):**
- SOL Up loss: signal $0.97, filled $0.965, 1.04 min to close → Type B loss (−$4.85)
- XRP Up stop-loss: signal $0.98, filled $0.485, 9 min left → stop loss fired, saved $3.88 (net −$1.02)
- 10 consecutive wins after the SOL loss, including first Down bet wins (ETH Down, SOL Down, BNB Down)

---

## Current constraints (Epoch 5)

| Constraint | Value | Source |
|------------|-------|--------|
| Signal threshold | ≥ 0.95 Gamma price | CLI `--threshold` |
| Scan window | 10 min look-ahead | CLI `--window` |
| CLOB price ceiling | < 0.989 | `_MAX_CLOB_PRICE` in yield_farming_service.py |
| CLOB price floor | > 0.01 (exchange minimum) | CLOB exchange rule |
| Hourly market entry cap | ≤ 3 min to close | `_MAX_MINS_HOURLY` in yield_farming_service.py |
| Realized vol guard | Skip if BTC 30-min RV > 0.50 | `_MAX_REALIZED_VOL` / `YIELD_MAX_REALIZED_VOL` env |
| DVOL skip | Skip cycle if BTC DVOL > 55 | `_DVOL_SKIP` in yield_farming_service.py |
| DVOL caution | Raise threshold to 0.975, max 7 min if DVOL 50–55 | `_DVOL_CAUTION` in yield_farming_service.py |
| Direction filter | Both Up + Down | `YIELD_DIRECTION_FILTER=both` in .env |
| Correlation guard | 1 trade per close-window per session | `_traded_close_windows` in-memory set |
| No re-entry | Skip token_id already executed this session | `_executed_token_ids` in-memory set |
| Stop-loss | Sell if curPrice < 0.50 AND > 1 min to close | `_STOP_LOSS_THRESHOLD` in monitor_service.py |
| Balance floor | Halt if USDC < $5 | `YIELD_BALANCE_FLOOR=5` in systemd.env |
| Consecutive loss limit | Halt after 3 consecutive losses | `YIELD_MAX_CONSECUTIVE_LOSSES=3` in systemd.env |
| Drawdown limit | Halt if session balance drops > 10% | `YIELD_MAX_DRAWDOWN_PCT=10` in systemd.env |
| Session time filter | Disabled (all hours) | `_SESSION_START_UTC_MINUTES=0` |
| Max trades per cycle | 20 (safety cap) | `_MAX_TRADES_PER_CYCLE` in yield_farming_service.py |
| Min order notional | $1 minimum (CLOB rule) | enforced in copy_trade_service.py |
| Max order size | $6.00 | `_MAX_ORDER_USD` in copy_trade_service.py |

---

## Direction filter — current state

`YIELD_DIRECTION_FILTER = "both"` in `.env` — Up and Down bets both active as of Epoch 5.

Was "up" only from Epoch 1 (except Epoch 1 ran both before we discovered the .env setting).
Switched back to "both" on 2026-04-16 after confirming Apr 9–13 losses were evenly distributed
across directions (7 Down, 5 Up) — the early bullish directional bias from Epoch 1 is gone.

Backtest stats (90-day dataset, 1,885 signals):
- Up-only: 98.7% win rate (153 trades, +$11.55 net)
- Both: 99.2% win rate (1,885 trades)

---

## Performance comparison guide

| "Since when?" | What to query |
|---------------|---------------|
| Since last deploy (Epoch 5) | `submitted_at >= '2026-04-16 09:53:00'` |
| Since Epoch 4 start | `submitted_at >= '2026-04-16 06:32:00'` |
| Since 0.989 fix | `submitted_at >= '2026-04-15 18:23:00'` |
| Since Epoch 3 start | `submitted_at >= '2026-04-06 16:07:00'` |
| All-time yield farming | `submitted_at >= '2026-03-26 00:00:00'` |
