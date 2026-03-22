# Analyzer Enrichment Design
**Date:** 2026-03-22
**Branch:** feat/behaviour_analyzing
**Scope:** `core/api/polymarket_client.py`, `analysis/analyzer.py`, `scripts/inspect_trader.py`
**Will also update:** `utility/endpoints.py` (two new URL constants).
**No changes to:** `strategy.py`, `main.py`, DB layer, any service.

---

## Problem

The current `analyzer.py` evaluates traders using only leaderboard-level RoV (`pnl / vol * 100`). This metric is unreliable for two reasons:

1. Leaderboard `pnl` mixes realized and unrealized P&L — an open position that hasn't resolved yet inflates the number.
2. A single outsized win on low volume produces a high RoV that doesn't reflect consistent skill.

There is no per-wallet quality signal beyond bot detection. The `inspect_trader.py` script cannot accept a wallet address directly — only a username.

---

## Solution

Enrich `Analyzer` with three new static methods that consume data from two new Polymarket API endpoints (`/positions`, `/closed-positions`, `/activity`). All computation is in-memory; nothing is persisted.

Update `inspect_trader.py` to call these methods and print a unified quality block, and accept either a username or a `0x`-prefixed wallet address as input.

---

## URL Constants — `utility/endpoints.py`

Two new constants added alongside existing ones:
```python
POSITIONS        = f"{DATA_API_BASE_URL}/positions"
CLOSED_POSITIONS = f"{DATA_API_BASE_URL}/closed-positions"
```
`ACTIVITY` already exists. No other changes to this file.

---

## API Layer — `core/api/polymarket_client.py`

Three new functions (raw HTTP only, no business logic):

### `get_user_positions(wallet: str) -> list[dict]`
- `GET /v1/positions?user=<wallet>&limit=500`
- Returns open positions with: `size`, `avgPrice`, `initialValue`, `currentValue`, `cashPnl`, `percentPnl`, `totalBought`, `realizedPnl`, `curPrice`, `redeemable`, `mergeable`, `conditionId`, `title`, `outcome`, `endDate`

### `get_user_closed_positions(wallet: str, max_results: int = 500) -> list[dict]`
- Paginates `GET /v1/closed-positions?user=<wallet>&limit=50&offset=N&sortBy=TIMESTAMP&sortDirection=DESC`
- Stops when a page returns fewer than 50 items or `max_results` is reached
- **Error policy:** any HTTP error mid-pagination raises immediately (same as all other client functions — do not return a partial result silently)
- Returns closed positions with: `realizedPnl`, `avgPrice`, `totalBought`, `curPrice`, `conditionId`, `title`, `outcome`, `timestamp`
- **`totalBought` unit:** shares (verified against live API — multiply by `avgPrice` to get USDC invested)

### `get_user_activity(wallet: str, limit: int = 500) -> list[dict]`
- `GET /v1/activity?user=<wallet>&limit=<limit>`
- Returns mixed event types: `TRADE`, `REDEEM`, `SPLIT`, `MERGE`
- Each event has: `type`, `usdcSize`, `conditionId`, `title`, `timestamp`

---

## Analysis Layer — `analysis/analyzer.py`

Three new static methods on the existing `Analyzer` class.

### `analyze_closed_positions(closed: list[dict]) -> dict`

Computes realized performance from `/closed-positions` data.

**Inputs:** raw list of dicts from `get_user_closed_positions()`
**Returns:**
```python
{
    "closed_position_count": int,
    "confidence_tier": str,        # "insufficient" | "low" | "moderate" | "high"
    "realized_win_rate": float,    # wins / total  (0.0–1.0)
    "total_realized_pnl": float,   # sum of realizedPnl (USDC)
    "total_invested_closed": float,# sum of totalBought * avgPrice (USDC)
    "realized_rov": float,         # total_realized_pnl / total_invested_closed * 100
    "avg_roi_per_position": float, # mean of per-position ROI% (skew-sensitive)
    "median_roi_per_position": float, # median of per-position ROI% (honest)
}
```

**Confidence tiers:**
- `"insufficient"` — fewer than 5 closed positions
- `"low"` — 5–14
- `"moderate"` — 15–49
- `"high"` — 50+

**Win definition:** `realizedPnl > 0`

**Per-position ROI:** `realizedPnl / (totalBought * avgPrice) * 100` — skips positions where invested amount is zero.

### `analyze_open_positions(open_positions: list[dict]) -> dict`

Computes current exposure from `/positions` data.

**Inputs:** raw list of dicts from `get_user_positions()`
**Returns:**
```python
{
    "open_position_count": int,
    "total_open_exposure": float,  # sum of initialValue (USDC invested)
    "total_unrealized_pnl": float, # sum of cashPnl
    "redeemable_count": int,       # positions where redeemable=True
    "mergeable_count": int,        # positions where mergeable=True (arb signal)
}
```

### `analyze_activity(activity_events: list[dict]) -> dict`

Computes confirmed win and arbitrage signals from `/activity` data.

**Inputs:** raw list of dicts from `get_user_activity()`
**Returns:**
```python
{
    "redeem_count": int,           # confirmed market resolutions cashed out
    "total_redeemed_usdc": float,  # sum of usdcSize for REDEEM events
    "merge_count": int,            # YES+NO merges (arb indicator)
    "split_count": int,            # USDC→shares splits (arb setup indicator)
    "arb_signal": bool,            # True if merge_count > 0
}
```

---

## Script — `scripts/inspect_trader.py`

### Input handling
Accept either:
- A Polymarket **username** (existing behaviour — resolves via leaderboard API by `userName`)
- A `0x`-prefixed **wallet address** (new — resolves via leaderboard API by `user=wallet`)

Detection: `if sys.argv[1].lower().startswith("0x")` → treat as wallet address.

When a wallet address is given, attempt `get_leaderboard(user=wallet)` to retrieve PnL/Vol for display. If the wallet is not on any leaderboard (empty result), display `"N/A"` for those fields. The wallet address itself is used directly for all subsequent API calls.

### New quality block printed after existing bot detection and profile sections

```
── TRADER QUALITY ───────────────────────────────────────────────────────────────
  Closed positions : 127  [high confidence]
  Realized win rate: 58.6%
  Total realized PnL: $649,502
  Realized RoV     : 6.6%
  Avg ROI / position: 26.0%   Median: 0.8%
  Open positions   : 70  |  Exposure: $918,572  |  Unrealized: +$13,636
  REDEEMs          : 3   |  Total cashed out: $2,056
  Arb signal       : No
```

---

## What Does NOT Change

- `analysis/strategy.py` — round-trip reconstruction remains for the profile classification
- `service/` layer — no changes
- `main.py` — no changes
- Database — no changes
- `core/models/` — no new models (raw dicts passed directly to analyzer methods)

---

## Constraints

- `/closed-positions` max page size is 50; pagination is required
- `/positions` supports up to 500 per call; single call is sufficient
- Activity feed supports up to 500 per call; single call is sufficient
- All three new API functions follow the existing pattern: return raw `list[dict]`, raise on HTTP error
