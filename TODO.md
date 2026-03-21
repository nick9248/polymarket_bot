# TODO

## 1. Fix price range constraint (WRONG — verified empirically)

**Current behavior:** `copy_trade_service.py` and `validator_service.py` both skip trades
where `price < 0.15 or price > 0.85`. This is incorrect.

**Evidence:**
- We successfully executed a BUY order at $0.015 manually (ETH above $1900, No outcome)
- Official Polymarket CLOB docs state the actual constraint is **0.01–0.99**, not 0.15–0.85
- The original 0.15–0.85 filter was based on observed 404s on specific markets, not an API rule

**Fix:** Replace the `0.15 / 0.85` thresholds with `0.01 / 0.99` everywhere they appear.
Files to update:
- `service/copy_trade_service.py` — near-expiry filter in `execute_copy_trade()`
- `service/validator_service.py` — price filter in `find_missed_trades()`
- `CLAUDE.md` — update the documented near-expiry filter description

---

## 2. Fix silent $1 minimum notional failure

**Current behavior:** `copy_trade_service.py` only checks market `minimum_order_size` (in shares).
For cheap markets (price < ~$0.20/share), the order notional falls below the CLOB's **$1 minimum**
and the order is silently rejected with a 400 error: `"invalid amount for a marketable BUY order, min size: $1"`.

**Evidence:** During manual trade at $0.015, market min was 5 shares ($0.075 total) — CLOB rejected.
Fix was to use 67 shares ($1.005) to meet the $1 floor.

**Fix:** In `execute_copy_trade()`, after computing `order_cost = min_size * current_price`,
bump shares up if `order_cost < 1.0`:
```python
CLOB_MIN_NOTIONAL_USD = 1.0
if order_cost < CLOB_MIN_NOTIONAL_USD:
    min_size = math.ceil(CLOB_MIN_NOTIONAL_USD / current_price)
    order_cost = min_size * current_price
```

---

## 3. Deploy updated validator to VPS

**Current state:** VPS is still running the old `validator_service.py` (log-only version).
The new reconciliation logic (`find_missed_trades`) is on branch `fix/validator_logic_update`
but has NOT been deployed yet.

**Steps:**
1. Merge `fix/validator_logic_update` into `main` (after fixing items 1 and 2 above first,
   since the validator also uses the price filter)
2. `scp service/validator_service.py root@spain-vpn:/home/nick/polymarket_bot/service/`
3. `scp main.py root@spain-vpn:/home/nick/polymarket_bot/`
4. `ssh root@spain-vpn "systemctl restart polymarket-bot"`
5. Verify in logs: `journalctl -u polymarket-bot -n 50 --no-pager`
