# Polymarket Copy-Trading Engine

This document details the architecture, safety guards, and functional logic behind the Polymarket Copy-Trading integration. The engine automatically mirrors trades detected in a target Polymarket proxy wallet (like `coinman2`) by placing proportionally-sized identical orders into the user's proxy wallet.

## 1. Authentication & Signature Types
Polymarket abstract wallets operate via proxies (Gnosis Safe etc.). This engine utilizes the official Python CLOB SDK (`py-clob-client`).

**Credentials required in `.env`:**
- `poly_private_key`: The EOA (Externally Owned Account) private key (e.g. MetaMask / Magic Link signer).
- `poly_address`: The actual **Proxy Wallet** address holding the USDC on Polymarket, set as the `funder` property.

**`signature_type` Configuration**: 
To seamlessly bypass validation errors (like "invalid signature"), the `copy_trade_service.py` is initialized with `signature_type=1` (Magic / Email wallet type), overriding the default `0` (raw EOA).

## 2. Order Sizing & Placement Strategy
When a targeted trader places an order, the system captures their `price` and `side`. 
To ensure capital preservation and predictable allocation, the bot calculates order `size` (which must be given in shares for limit orders) via:
`Shares = 2.0 / price`
This submits a Limit Order at the exact entry price of the copied trader for precisely ~$2 USDC.e limit liability.

**(Minimum Order Constraint Override):**
Polymarket demands a minimum ~$1 execution for orders. The $2 fixed allocation natively guarantees compliance while bypassing the `INVALID_ORDER_MIN_SIZE` rejections on low probability tokens.

## 3. The `Gamma API` Market Token Resolver
Because the `TRADES` API endpoint natively returns the `conditionId` but restricts access to the `clobTokenId` (which is cryptographically required to submit an order), the `polymarket_client.py` uses an internal helper.
- Function: `get_market_token_id(condition_id, outcome_index)`
- Connects to: `gamma-api.polymarket.com/markets?condition_id=...`
- It accesses the `clobTokenIds` array and maps it against the `outcome_index` derived from the alert. 

## 4. Execution Guardrails & Risk Protections

### A. Geolocation Blocker
`utility/geo.py` leverages `ipinfo.io` to ensure network requests originate from Spain (`ES`).
- Trigger: Handled by `.env: CHECK_GEO_IP=True`
- Use-case: Protects against geographic flagging when accidentally booting the bot locally without a VPN. When safely inside the approved VPS, `CHECK_GEO_IP` can be safely turned to `False` to decrease latency.

### B. Genesis Block Protection
When parsing a new target wallet for the very first time, the database `known_hashes` will be entirely empty. To prevent the application from retroactively executing 500 historic trades across their account lifetime, `main.py` incorporates a genesis-burn block. 
- During `Genesis Run`, all historic trades are persisted silently to the database without firing Telegram alerts or submitting limit orders to the exchange.

### C. The Validation Monitor (`validator_service.py`)
To mathematically verify executions, the system incorporates a self-analysis function at the end of `main.py`. If active in Copy-Trading Mode (`--wallets` provided flag), the robot fetches its **own** wallet's `fetch_user_trades` state via the Data API, proving the limit orders successfully resolved on-chain without trusting local memory variables.
