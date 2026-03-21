# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Polymarket copy-trading daemon. It tracks top traders on the Polymarket leaderboard (or a fixed list of wallets), detects new trades, sends Telegram alerts, and mirrors trades on the Polymarket CLOB API. Runs continuously on a Spain VPS (geo-check enforced for CLOB access).

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot (leaderboard mode)
python main.py

# Run in copy-trade mode against specific wallets
python main.py --wallets 0xADDRESS:displayname

# Run tests
pytest

# Run a single test
pytest tests/path/to/test_file.py::test_function_name -v
```

## Architecture

Layered: **Core** → **Service** → **main.py**

```
main.py                    — daemon loop, Telegram command dispatch, cycle orchestration
│
├── service/               — all orchestration; only layer main.py imports
│   ├── db_service.py      — connection lifecycle + repository calls (only DB entry point)
│   ├── copy_trade_service.py — CLOB order submission via py_clob_client
│   ├── telegram_service.py   — send alerts, poll commands (/health, /test)
│   ├── trades_service.py     — fetch + parse trades from Polymarket Data API
│   ├── leaderboard_service.py
│   ├── analysis_service.py
│   └── validator_service.py  — checks our own wallet to verify executions landed
│
├── core/
│   ├── models/            — pure dataclasses (TradeEntry, LeaderboardEntry)
│   ├── api/               — raw HTTP calls only (polymarket_client.py)
│   └── database/          — connection.py (schema init), repository.py (all SQL)
│
├── analysis/
│   ├── analyzer.py        — HFT/bot detection, efficiency ranking
│   └── strategy.py        — position profiling (round-trips, whale detection)
│
└── utility/
    ├── constants.py       — enums (Category, TimePeriod, OrderBy), timeouts
    ├── endpoints.py       — all API URLs in one place
    ├── geo.py             — Spain IP check (CHECK_GEO_IP env flag)
    └── logger.py          — logging initialisation
```

## Key Data Flow

### Copy-trade cycle (5s polling):
1. `trades_service.fetch_user_trades()` → `TradeEntry` list (includes `asset` field = CLOB token_id)
2. Compare `transaction_hash` against DB; new trades only
3. Genesis detection: `db_service.is_wallet_tracked()` — on first run, seed silently without alerting
4. `telegram_service.send_trade_alert()` → Telegram notification
5. `copy_trade_service.execute_copy_trade()` → submit $1.50 limit order on CLOB

### CLOB token resolution — CRITICAL:
The Polymarket `/trades` API returns an `asset` field which is the exact CLOB token ID.
**Always use `trade.asset` directly.** Do NOT use `get_market_token_id()` (gamma API lookup) — it returns wrong/stale token IDs.

### Near-expiry filter:
Markets with `price > 0.85 or price < 0.15` are skipped — CLOB closes order books near resolution (returns 404 on tick-size endpoint).

### CLOB minimum order size:
Each market has a `minimum_order_size` (shares, not USD). Confirmed at 5 shares for ETH price markets. `copy_trade_service` fetches it via `client.get_market(condition_id)` and bumps order size to the minimum if `trade_size_usd / price` falls short. Balance is checked against the actual (bumped) USD amount.

### CLOB authentication — account type:
This account uses **POLY_PROXY (signature_type=1)** with a separate proxy wallet:
- `poly_private_key` = EOA (signer, derives to `poly_address`)
- `poly_funder_address` = proxy/maker contract (holds USDC; this is the `maker` in EIP-712 orders)
- CLOB requires `maker ≠ signer`; orders with `maker == signer` are rejected with "invalid signature"
- type=0 (pure EOA) has $0 on-chain balance — all USDC is in the proxy pool (type=1)

## Database

PostgreSQL. Three tables managed by `core/database/connection.py`:
- `leaderboard_snapshots` — periodic snapshots of top traders
- `trader_trades` — all fetched trades, deduplicated by `transaction_hash`
- `tracked_wallets` — wallets we've seen before (genesis detection gate)

`repository.py` contains all SQL. `db_service.py` manages connections. Never open connections outside `db_service.py`.

## Environment Variables (.env)

| Key | Purpose |
|-----|---------|
| `db_user`, `db_password`, `db_host`, `db_port`, `db_name` | PostgreSQL connection |
| `telegram_bot_key`, `telegram_chat_id` | Telegram bot |
| `poly_private_key` | Polymarket signing key (EOA private key) |
| `poly_address` | Our wallet address (used by validator_service to check executed trades) |
| `poly_funder_address` | **Magic/email wallets only**: proxy contract address (different from derived EOA). Leave unset for MetaMask/direct EOA wallets. |
| `CHECK_GEO_IP` | `True` to enforce Spain IP check; anything else bypasses |

## Deployment (Spain VPS)

The bot runs as a systemd service. Two config files exist because systemd cannot parse python-dotenv format:
- `.env` — parsed by `python-dotenv` at runtime (uses `key = "value"` format)
- `systemd.env` — parsed by systemd `EnvironmentFile=` (requires `KEY=VALUE`, no spaces, no quotes)

```bash
# On VPS (ssh root@spain-vpn)
systemctl status polymarket-bot
journalctl -u polymarket-bot -f          # live logs
systemctl restart polymarket-bot

# Deploy a changed file from local
scp <file> root@spain-vpn:/home/nick/polymarket_bot/<file>
```

## Coding Preferences

- Simple and clear without complexity unless truly needed
- Scalable for future expansion
- Completely modular
- Clear, understandable docstrings

## Code Quality Checklist (MANDATORY)

**Before completing ANY code task, verify:**

1. **Layered Architecture**: Does the code follow Core → Service → main.py flow?
   - `main.py` should NEVER contain business logic or direct API calls
   - Services orchestrate operations using core components
   - Core contains definitions, models, and base methods

2. **Modularity**: Is each class/function doing ONE thing?
   - No monolithic classes with multiple responsibilities
   - Each concern should be a separate function/class, not if/elif chains

3. **Right Layer**: Is the code in the correct layer?
   - API calls → Service layer (or core/api for raw HTTP)
   - Data models → Core layer
   - Business logic → Service layer (NOT main.py)

4. **No Shortcuts**: Even if it works, is it architecturally correct?
   - Quick solutions that violate architecture must be refactored
   - "It works" is not sufficient — it must be clean

## Problem-Solving Approach

When fixing bugs or issues, follow structural thinking — not quick patches:

1. **Understand the flow first**: Trace the data flow and understand WHY the problem exists
2. **Find the root cause**: Don't patch symptoms; if data is wrong, find where it goes wrong in the pipeline
3. **Fix at the right layer**: The fix should be in the component responsible for that logic
4. **Maintain clean architecture**: Don't add external calls or workarounds that bypass the established flow

**Key principle**: If the same data source works correctly elsewhere, the problem is in how this component processes the data, not in the data itself.

## Naming Conventions

- Descriptive names related to the method's purpose
- No abbreviations
- Leading underscores for internal/private methods (Python convention)

## Communication Style

- Always say the truth without sugar coating
- Mention potential problems and risks proactively
- Explain trade-offs clearly
- Be precise — never make assumptions without verifying facts
- No quick fixes — find root cause, implement future-proof solutions
- When investigating: verify with actual data, don't assume

## Mandatory Verification

Before claiming a fix works, check VPS logs:
```bash
ssh root@spain-vpn "journalctl -u polymarket-bot -n 100 --no-pager"
```
`service active` ≠ fix working. Confirm in logs that the specific code path executed correctly.
