# Error Analysis & Pattern Log

This document records observed failure patterns, loss events, and hypotheses about
structural risks in the yield farming strategy. Each pattern has:

1. **Observation** — what happened and when
2. **Hypothesis** — the suspected root cause or structural risk
3. **Data** — historical evidence gathered so far
4. **Decision** — Pending / Testing / Accepted (code change) / Rejected

The methodology is: document → gather data → test hypothesis → decide.
We do **not** add code guards based on a single loss event.

---

## Pattern 001 — Correlated Asset Losses

**Status:** Pending — data gathering required

### Observation

**Date:** 2026-03-29, 22:55 UTC (6:45–7:00 PM ET)

The bot simultaneously bought "Down" on both Bitcoin and Ethereum in the same
15-minute window:

| # | Asset | Side | Signal | Fill | Cost | Result |
|---|-------|------|--------|------|------|--------|
| 1 | ETH Up or Down – 6:45PM-7PM ET | Down | 0.9750 | 0.9750 | $4.70 | **LOST –$4.70** |
| 2 | BTC Up or Down – 6:45PM-7PM ET | Down | 0.9550 | 0.9550 | $4.65 | **LOST –$4.65** |

Both markets resolved at 23:02 UTC. Combined loss: **$9.35 in a single event.**
This triggered the drawdown risk guard at 60.9% (session start: $15.34).

### Hypothesis

BTC and ETH are highly correlated (correlation ~0.85–0.95 on short timeframes).
Buying the same direction on both in the same 15-minute window is effectively
**one directional bet, not two**. A single macro event (e.g. crypto pump) causes
both to resolve against the predicted direction simultaneously, multiplying the loss.

The strategy's edge comes from liquidity premium (buying near-certain outcomes at a
small discount to $1). But at 95–97.5% confidence, the strategy is still implicitly
taking a 2.5–5% directional risk per position. When two correlated positions are open
simultaneously, that risk effectively doubles in its P&L impact.

### Data Gathered

From the yield_trades DB (as of 2026-03-30), all cases where multiple crypto assets
were traded in the same time window:

| Date | Window | Assets | Directions | Outcome |
|------|--------|--------|------------|---------|
| 2026-03-29 08:00 ET | 8AM slot | SOL + ETH | Both Down | Both WON |
| 2026-03-29 18:45 ET | 6:45PM-7PM | BTC + ETH | Both Down | Both LOST |

**Sample size: 2 correlated pairs. Too small to draw conclusions.**

### Analysis Questions

Before deciding on a code change, answer these:

1. **How often does the bot trade correlated pairs simultaneously?**
   - Query: trades with overlapping time windows, same outcome direction
   - Need at least 30 correlated pairs for meaningful statistics

2. **When correlated pairs occur, do they always resolve the same direction?**
   - If correlation is 95%+ on 15-min windows: blocking one removes little alpha
   - If it's lower: the cases of same-direction resolution are the dangerous ones

3. **What is the marginal expected value of the second correlated trade?**
   - If BTC Down is already bought, does adding ETH Down provide independent EV?
   - Or does it just double exposure to the same underlying risk factor?

4. **Is the 6:45PM ET window specifically high-risk for correlated moves?**
   - US market close (4PM ET) triggers large crypto moves, and effects often lag
   - Could be time-of-day risk, not purely correlation risk

### Testing Plan

1. Pull all yield_trades history and identify all same-window pairs
2. Calculate how many resolved in the same direction vs opposite directions
3. Calculate the P&L impact of the second correlated trade in each case
4. Compare: "trading both" vs "trading only the first found in the window"
5. Forward-test: run bot in dry-run for 2 weeks, log all correlated pairs, track outcomes

### Decision

**PENDING.** Do not add correlation blocking until testing is complete.
Estimated minimum data: 30 correlated pair events.

---

## Pattern 002 — Lifecycle Tracking Failures (Stuck Trades)

**Status:** Partially fixed (2026-03-30)

### Observation

Multiple trades get stuck in `submitted` or `filled` status long after the market
closes. Root causes identified:

1. **Manual redemption before bot polling**: User redeems winning positions via the
   Polymarket UI before the bot's `poll_lifecycle()` checks. The position disappears
   from the open positions API. The bot has nothing to match against and the trade
   stays `submitted`/`filled` forever.

2. **API lag**: Very short-lived markets (5–15 min) can resolve before the next
   5-second poll cycle sees them in the positions API.

3. **conditionId / outcome mismatch**: If the outcome string from the DB doesn't
   match the positions API exactly, the lookup key fails.

### Affected Trades (2026-03-29)

| ID | Market | Final Status (at bot) |
|----|--------|-----------------------|
| 9894 | XRP 8:45AM-9AM | stuck `filled` → eventually `error` |
| 9898 | Bitcoin 9:30AM-9:45AM | stuck `filled` |
| 9912 | Solana 11AM | stuck `submitted` |
| 9958 | Solana 6:15PM-6:30PM | stuck `submitted` |

### Fix Applied

- Reduced `_STUCK_HOURS` from 24h to 4h (markets close in ≤30 min; 4h is generous)
- Distinguish handling by current status:
  - `submitted` after 4h → mark `expired` (order never filled, pnl=0)
  - `filled` after 4h, position gone → mark `error` with note "redeemed externally"
- Improved Telegram alerts to differentiate the two cases

### Remaining Risk

- The 4h window still means up to 4h of P&L uncertainty for manually redeemed wins
- A complete fix would query the CLOB order status API to confirm fills, and estimate
  outcome from on-chain data — deferred for future work
- **Rule**: avoid manually redeeming positions via the Polymarket UI when the bot is
  tracking those trades. Redeem only after confirming the bot has marked them `won`.

---

## Pattern 003 — Risk Guard Session Staleness

**Status:** Fixed (2026-03-30)

### Observation

The `session_start_balance` was set once in memory at bot startup and never updated
unless the bot was restarted. After a drawdown guard trigger, the only way to unblock
was to restart the service. Restarting re-fetched the balance, which effectively
bypassed the guard silently.

Additionally, the session_start was never persisted to the DB, so restarting after a
loss would start a fresh session from the (lower) current balance, making the previous
losses invisible to the guard.

### Fix Applied

- `session_start_balance` and `session_start_time` are now persisted in `bot_heartbeat`
- On restart: if a recent session exists in DB (<12h old), it is restored — losses
  are not forgotten across restarts
- `/reset_risk` Telegram command: re-fetches current balance, persists new session
  start, sends confirmation alert
- Dashboard "Reset Risk Guard" button: sets `reset_requested=TRUE` in DB; bot picks
  it up on next cycle
- Midnight auto-reset: when the calendar date advances, automatically starts a new
  session from the current balance. All historical data preserved in DB.

### Auto-Reset and Large Balances

At current size ($5–$25), a daily reset is reasonable. At larger balances ($1k+):

- The 10% drawdown threshold on a daily reset = $100/day allowance at $1k
- Consider tightening `YIELD_MAX_DRAWDOWN_PCT` as balance grows (e.g. 5% at $1k+)
- Consider a weekly session window instead of daily once consistent profits are shown
- All trade data is permanently in the DB regardless of session resets — full audit
  trail always available

---

---

## Pattern 004 — Late-Window Execution Risk

**Status:** Pending — data gathering required

### Observation

**Date:** 2026-03-31, 18:59 UTC (2:59 PM ET)

| Field | Value |
|---|---|
| Trade ID | 10170 |
| Market | XRP Up or Down – March 31, 2:45PM-3:00PM ET |
| Outcome bet | Down @ $0.9650 |
| minutes_to_close | **0.56** (34 seconds before close) |
| gamma_clob_spread | 0.0000 (Gamma and CLOB agreed) |
| btc_dvol | 52.75 |
| btc_iv_percentile | **36.02%** (low-moderate volatility) |
| Result | XRP went Up → **–$4.75 full loss** |

The bot caught this signal with only 34 seconds remaining. The order filled at $0.9650,
then XRP reversed in the final 34 seconds.

### Hypothesis

There may be two separate risk dimensions worth testing:

1. **Time-to-close**: Signals caught very late (< 1 minute to close) may be structurally
   riskier than signals caught earlier. A market still at $0.965 with 34 seconds left
   has by definition resisted reversal all day — but the final seconds are also when
   liquidity drains and a single large order can flip the outcome.

2. **Volatility regime**: This loss occurred at `iv_percentile = 36%`, a relatively
   calm environment. This is counter to the initial hypothesis that losses cluster in
   high-volatility regimes. Alternatively, moderate IV may be the worst case — high
   enough for moves, low enough that the threshold filter doesn't tighten.

### Data Gathered

| Trade ID | minutes_to_close | btc_iv_percentile | Result |
|---|---|---|---|
| 10116 (XRP Up 9AM) | 14.95 | NULL | Won |
| 10170 (XRP Down 2:45PM) | 0.56 | 36.02% | **Lost** |
| 9963 (ETH Down 6:45PM) | ~4.0 (est.) | NULL | Lost |
| 9964 (BTC Down 6:45PM) | ~4.0 (est.) | NULL | Lost |

Note: trades 9963/9964 are a correlated pair (Pattern 001) so they don't count as
independent data points for this pattern.

**Sample size: 1 independent late-window loss. Too small to draw conclusions.**

### Analysis Questions

1. **Does `minutes_to_close < 1` correlate with higher loss rate?**
   - Query: group all resolved trades by `minutes_to_close` buckets, compare win rates

2. **Should there be a minimum time-to-close filter?**
   - e.g. skip if market closes in < 2 minutes — reduces entries but may improve win rate
   - Cost: missed trades that would have won (most of them, given overall win rate)

3. **Is the IV percentile signal useful at all?**
   - This loss at 36% suggests losses are not exclusively a high-IV phenomenon
   - Need 10+ losses with IV data to see if any clustering exists

4. **Interaction effect**: Is the risk highest at *low* IV + *late* window?
   - Low IV → market moves slowly → 96.5% signal feels safe → bot enters late
   - But in final seconds, thin order book means a single trade can flip the price

### Testing Plan

After 2 weeks of data collection (target: 200+ resolved trades with `btc_iv_percentile`
and `minutes_to_close` populated):

1. Query win rate by `minutes_to_close` bucket: `<1`, `1-3`, `3-5`, `5-10`, `>10`
2. Query win rate by `btc_iv_percentile` bucket: `<25`, `25-50`, `50-75`, `>75`
3. Query cross-tabulation: minutes × iv_percentile vs win/loss
4. If `minutes_to_close < 1` shows meaningfully lower win rate: add minimum time filter
5. If `btc_iv_percentile` shows meaningful threshold: implement dynamic threshold

### Decision

**PENDING.** Do not add time-to-close filter or dynamic threshold until data supports it.
Estimated minimum data: 10 losses with `minutes_to_close` and `btc_iv_percentile` logged.

---

*Last updated: 2026-03-31*
