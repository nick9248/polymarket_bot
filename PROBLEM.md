# Copy-Trade Execution Bug

## Expected Behaviour
When a new trade is detected from a tracked wallet, `execute_copy_trade()` should
submit a mirrored limit order on the Polymarket CLOB and return `True`.

## Actual Behaviour
Every call to `execute_copy_trade()` fails with a CLOB 404 on the `/tick-size` endpoint
and returns `False`. No orders are ever placed, even though:
- Geo check passes (Spain VPS confirmed ES)
- Balance check passes ($36 USDC available)
- Token ID appears to be resolved

## Evidence

### Log pattern (every trade, every market):
```
GET https://clob.polymarket.com/balance-allowance ... 200 OK
GET https://clob.polymarket.com/tick-size?token_id=<ID> ... 404 Not Found
WARNING: CLOB market not found (404) — market already closed for trading: <market title>
```

### Same token_id returned for completely different markets:
The gamma-API lookup `GET /markets?condition_id=<id>` was returning token_id
`53135072462907880191400140706440867753044989936304433583131786753949599718775`
for ALL markets (ETH March 27, ETH March 25, Solana $100, Solana $80, etc.).
This is impossible — each market has a unique token_id.

### Root cause identified (Phase 1):
The Polymarket `/trades` API response includes an `asset` field which IS the CLOB
token_id directly. The code was ignoring it and instead querying the gamma API with
`condition_id`, which was returning a stale/wrong market.

### Fix attempted (not yet verified):
Changed `execute_copy_trade()` to use `trade.asset` directly instead of calling
`get_market_token_id()`. Deployed to VPS but `/test` result not yet confirmed.

## Data Flow
```
/trades API response
  └── trade.asset = "43105432..." ← correct CLOB token_id (THIS FIELD)
  └── trade.condition_id = "0x5aeb..." ← was used to query gamma API (WRONG)

gamma-api.polymarket.com/markets?condition_id=<id>
  └── returns clobTokenIds → was wrong/stale for all markets

clob.polymarket.com/tick-size?token_id=<id>
  └── 404 if token_id is wrong or market is closed for new orders
```

## Open Questions
1. Does `trade.asset` actually match what the CLOB expects in `OrderArgs(token_id=...)`?
2. Are there markets in coinman2's history where the CLOB IS open (price 0.15–0.85 AND
   market still accepting orders)?
3. Is it possible that coinman2 exclusively trades AMM markets (not CLOB), making
   copy-trading via CLOB structurally impossible for his positions?

## What Needs to Happen
- Verify `trade.asset` fix works by re-running `/test` and checking VPS logs
- If still 404: log the actual `asset` value being sent and manually verify it against
  the CLOB tick-size endpoint
- If all markets are genuinely closed: accept structural limitation and find a trader
  who uses CLOB markets with mid-range prices
