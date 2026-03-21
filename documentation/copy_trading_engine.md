# Polymarket Copy-Trading Engine

This document details the architecture, safety guards, and functional logic of the copy-trading integration. The engine automatically mirrors new trades detected on a target wallet (e.g. `coinman2`) by placing orders from our proxy wallet at the current live market price.

---

## 1. Authentication & Wallet Architecture

Polymarket accounts have **two distinct addresses** that serve different roles:

| Env variable | Address | Role |
|---|---|---|
| `poly_private_key` | `0xEF5703...` | EOA signer — signs orders cryptographically |
| `poly_address` | `0xEF5703...` | Same as signer — **not used for trading** |
| `poly_funder_address` | `0xCE4514...` | Proxy wallet — holds USDC, recorded as `maker` on every order |

The CLOB operates on **EIP-712 signed orders** with three relevant fields:
- `maker` — the address whose balance is debited (must be `poly_funder_address`)
- `signer` — the address whose private key signs the order (must be `poly_private_key`)
- `signatureType` — must be `1` (POLY_PROXY) when `maker ≠ signer`

**`signature_type=1` with explicit `funder` is the only valid configuration for this account.**

### Why other modes fail

| Mode | Result |
|---|---|
| `signature_type=0` (EOA direct) | Rejected: "not enough balance / allowance" — the EOA holds $0 USDC on-chain; all USDC is in the proxy pool |
| `signature_type=1`, no funder | Rejected: "invalid signature" — `maker == signer` is invalid for POLY_PROXY |
| `signature_type=1` + `funder=poly_funder_address` | **Correct** — `maker=proxy`, `signer=EOA`, balance verified ✓ |

### Balance query
The `get_balance_allowance()` endpoint is queried with `signature_type=1` (no explicit funder). It returns the real tradeable USDC balance for this account regardless of the funder argument.

---

## 2. Order Sizing & Placement Strategy

### Order price — use current market price, not the signal price
When a signal is detected, the copied trader's entry price is **already stale** by the time we submit an order. Using it as a limit price results in unfilled limit orders sitting below market.

The engine fetches the current **best ask** (for BUY) or **best bid** (for SELL) from the live order book and submits a taker order at that price. This guarantees immediate fill.

### Order size — CLOB per-market minimum
Polymarket enforces a `minimum_order_size` per market (in shares, not USD). This varies:
- ETH daily price markets: **5 shares minimum**
- Other markets: may be 2 shares or higher

The engine calls `client.get_market(condition_id)` to retrieve the exact minimum for each market and always uses that as the order size. This is the smallest valid order the CLOB accepts.

### Effective cost
`order_cost = minimum_order_size × current_ask_price`

Examples at minimum=5:
- Market at $0.30 → 5 × $0.30 = **$1.50**
- Market at $0.40 → 5 × $0.40 = **$2.00**
- Market at $0.76 → 5 × $0.76 = **$3.80**

---

## 3. Execution Guardrails

### A. Geo-location check
`utility/geo.py` verifies the VPS IP is in Spain (ES) via ipinfo.io before any order is submitted. Controlled by `CHECK_GEO_IP=True` in `.env`. Set to `False` on the VPS when geo is already confirmed to reduce latency.

### B. Near-expiry filter (current price)
Markets with `current_price > 0.85` or `current_price < 0.15` are skipped. The filter uses the **current order book price**, not the historical signal price. This correctly catches markets that have nearly resolved since the signal was generated.

### C. Slippage guard
If `|current_price − signal_price| / signal_price > 10%`, the trade is skipped.

With 5-second polling the gap is typically < 2%. A larger gap indicates the trade was detected late or the market moved unusually fast, meaning we would be entering at a significantly worse price than the signal.

### D. Genesis block protection
On the first run for a new target wallet, all historical trades are seeded into the database silently — no alerts sent, no orders placed. This prevents the bot from retroactively executing hundreds of past trades on startup.

### E. CLOB token ID — use `trade.asset` directly
The Polymarket `/trades` API returns an `asset` field which is the exact CLOB `token_id`. **Always use `trade.asset` directly.** Do not use `get_market_token_id()` (gamma API lookup) — it returns stale or wrong token IDs.

---

## 4. Validator (`validator_service.py`)

After each copy-trade cycle, the validator fetches our own wallet's recent trades from the Data API to confirm executions landed on-chain — without relying on local memory.

**Critical:** the validator must query `poly_funder_address` (`0xCE4514...`), **not** `poly_address` (`0xEF5703...`). Every order has `maker = poly_funder_address`, so the Data API records trades under that address. Querying `poly_address` returns zero results because the EOA is only the signer, never the maker.

---

## 5. Copy-Trade Signal Flow

```
coinman2 makes a trade
        │
        ▼
trades_service.fetch_user_trades()       ← Data API /v1/trades?user=coinman2
        │
        ▼ trade.asset = CLOB token_id (use directly)
        │ trade.condition_id             (used for get_market() call only)
        │
        ▼
DB deduplication (transaction_hash)
        │
        ▼ new trade only
        │
        ▼
copy_trade_service.execute_copy_trade(trade)
  1. Geo check (Spain)
  2. _get_current_market_price() → order book best ask
  3. Slippage check: |current − signal| / signal ≤ 10%
  4. Near-expiry check: 0.15 ≤ current_price ≤ 0.85
  5. _get_min_order_size() → client.get_market(condition_id)
  6. Balance check: balance ≥ min_size × current_price
  7. client.create_order() + client.post_order()  [signature_type=1, funder=proxy]
        │
        ▼
validator_service.validate_own_trades()
  → fetch_user_trades(poly_funder_address)   ← confirms execution on-chain
```
