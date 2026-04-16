# Optimization Ideas — Yield Farming Strategy

Tracks proposed improvements, their evidence base, and implementation status.
Use this file to decide what to work on next and to remember why a decision was made.

---

## Priority List

| Priority | Action | Evidence | Status |
|---|---|---|---|
| P0 | DVOL guard (skip >55, caution 50–55) | All 3 post-fix losses at DVOL 50.9–53.8 | **DONE — Apr 8, 2026** |
| P0 | Down threshold bump (>= 0.97) | 72% of losses were Down bets | **DROPPED** — direction bias is period-specific (see note) |
| P1 | Hour blackout: skip ET 10–12 and 21–24 | +0.34% WR improvement in 90-day backtest, 13% fewer trades | Not started |
| P1 | Hard max: skip if minutes_to_close > 10 | Losses avg 9.4 min vs 6.7 min for wins | Not started |
| P2 | Monitor 16:00–17:00 ET | 5 losses in 90-day dataset — don't filter yet, watch | Monitoring |

---

## Implemented

### P0: DVOL Guard — Apr 8, 2026

**File:** `service/yield_farming_service.py` (in `run_yield_farming_cycle`)

**Logic:**
- DVOL < 50: trade normally (79% of the year historically)
- DVOL 50–55: raise threshold to 0.975, skip if minutes_to_close > 7 (15% of the year)
- DVOL > 55: skip entire cycle (only 5.6% of the year)

**Evidence:**
- All 3 post-fix losses occurred with DVOL in the 50–55 range (50.9, 51.5, 52.3)
- 90-day backtest: 98.5% WR at DVOL <= 50 vs 86.4% at DVOL > 50
- 12-month Deribit hourly data (8,641 bars): < 50 = 79.3%, 50–55 = 15.1%, > 55 = 5.6%

**Important note on thresholds:**
The 79.3% / 15.1% / 5.6% split is from Apr 2025 – Apr 2026 (calm market period).
In high-volatility years (2021, 2022), DVOL regularly exceeded 70–100. The absolute thresholds
(50/55) should be reviewed quarterly as market regimes shift. The IV percentile we log per trade
(btc_iv_percentile) is regime-aware and provides a longer-term signal.

---

## Dropped

### P0: Down Threshold Bump

**Original idea:** Require signal_price >= 0.97 for markets with "Down" in the title.

**Why dropped:** The 72% Down bias in losses is period-specific, not structural.
In Mar–Apr 2026, crypto was trending upward after a sell-off, so "Down" bets failed more.
In a bear market, "Up" bets would fail at the same rate. A direction-based filter would
be overfitting to a specific macro regime and would flip its logic in the next trend.
The DVOL guard is the durable, regime-independent filter.

---

## Not Started

### P1: Hour Blackout

Skip markets closing during these ET windows:
- **10:00–12:00 ET** (midday gap, erratic moves)
- **21:00–24:00 ET** (low liquidity, overnight drift)

Evidence: 90-day historical dataset shows elevated loss rate in these windows.
Reduces trade count by ~13% but improves win rate by ~0.34%.

To implement: add to `scan_opportunities()` after the `_is_trading_session()` check.
Need to re-enable session filtering (currently `_SESSION_START_UTC_MINUTES = 0`).

### P1: Hard Maximum Window

Skip any opportunity where `minutes_to_close > 10`.

Evidence: Losing trades averaged 9.4 minutes to close; winning trades averaged 6.7 minutes.
The 10–15 minute range has the highest residual price risk.

To implement: add check in `scan_opportunities()` candidates loop or in `run_yield_farming_cycle()`.

### P2: Monitor 16:00–17:00 ET

5 losses observed in this window across the 90-day dataset. Not enough to filter yet —
could be coincidence given hundreds of trades in this window. Track and re-evaluate
when more post-fix data accumulates (target: 30+ losses total for statistical significance).
