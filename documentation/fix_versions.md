# Fix Versions — Yield Farming Strategy

Track strategy changes by deploy date. Use these dates to filter DB queries when evaluating performance.

---

## v2 — April 7, 2026 (deployed 17:48 UTC+2)

**File changed:** `service/yield_farming_service.py`

### What changed

1. **Live orderbook pricing** *(critical fix)*
   - Before: used the CLOB `/markets/{condition_id}` `price` field (last-trade price, often stale near close)
   - After: fetches real best-ask from CLOB `/book?token_id=` endpoint
   - Impact: the stale last-trade price could show $0.96 while the actual ask was already $0.999 — causing wasted attempts and bad fills. Now we see the real price before committing.

2. **Correlation guard**
   - Added per-session deduplication by `close_time` window
   - If BTC Down and ETH Down both close at the same time, only the first is traded
   - Prevents double-exposure on the same macro move (which was responsible for the Mar 29 double-loss)

3. **Scan window extended:** 10 min → 15 min (more opportunity surface)

4. **CLOB ceiling raised:** 0.980 → 0.989 (finer-grained rejection near lock-in)

5. **Richer DB logging per trade:** `gamma_clob_spread`, `minutes_to_close`, `btc_dvol`, `btc_iv_percentile`

### Performance before this fix (v1 baseline)
- Period: strategy launch → Apr 7, 2026
- Losses: 5 confirmed
  - Mar 29: ETH Down (−$4.70) + BTC Down (−$4.65) — same close window, double-loss
  - Mar 31: XRP Down (−$4.75)
  - Apr 1: BTC Down (−$4.78)
  - Apr 2: DOGE Down (−$4.90)
- Balance drained to ~$4.58 (bankrupt relative to $5 floor)

### Performance after this fix (v2 baseline)
- Measure from: `created_at >= '2026-04-07 15:48:00'` (UTC)
- Result so far: 0 losses, ~25 wins (Apr 7 evening + Apr 8 early morning)

---

## v1 — Strategy launch (March 2026)

Initial yield farming implementation. Key characteristics:
- Used stale CLOB last-trade price (no orderbook check)
- No correlation guard (could take multiple correlated positions in same window)
- 10-minute scan window
- 0.98 CLOB ceiling

**Do not include v1 data in win-rate analysis** — the pricing bug inflated entry risk materially.
