# polymarket_bot

A Polymarket automation daemon built in Python. Two operating modes:

- **Yield farming** — scans prediction markets closing within minutes, buys high-confidence outcomes (configurable price threshold, default ≥0.95) and holds until resolution
- **Copy trading** — monitors the Polymarket leaderboard, detects new trades from top traders, and mirrors them on the CLOB in real time

Deployed 24/7 on a Hetzner VPS via systemd. Sends Telegram alerts for every trade and supports live commands (`/health`, `/balance`, `/summary`, `/trades`, `/reset_risk`).

---

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13 |
| Order execution | Polymarket CLOB API (EIP-712, POLY_PROXY auth) |
| Market data | Polymarket Gamma API + Data API |
| Persistence | PostgreSQL |
| Alerts | Telegram Bot API |
| Deployment | systemd on Hetzner VPS |

---

## Architecture

Layered: **Core** (models, raw API) → **Service** (business logic) → **main.py** (daemon loop)

```
main.py                          — daemon loop, Telegram dispatch, cycle orchestration
│
├── service/
│   ├── yield_farming_service.py — scans Gamma API for near-expiry markets
│   ├── copy_trade_service.py    — CLOB order submission via EIP-712
│   ├── risk_guard_service.py    — circuit breakers: daily loss, drawdown, balance floor
│   ├── monitor_service.py       — trade lifecycle, stop-loss, daily P&L summary
│   ├── telegram_service.py      — alerts and command polling
│   └── db_service.py            — connection lifecycle + repository
│
├── core/
│   ├── models/                  — pure dataclasses (TradeEntry, YieldOpportunity, …)
│   ├── api/                     — raw HTTP (polymarket_client.py, dvol_fetcher.py)
│   └── database/                — connection.py (schema init), repository.py (all SQL)
│
└── analysis/                    — leaderboard analysis, strategy profiling, backtests
```

---

## Setup

### 1. Install

```bash
git clone https://github.com/nick9248/polymarket_bot.git
cd polymarket_bot
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` — required keys:

| Key | Purpose |
|-----|---------|
| `db_user`, `db_password`, `db_host`, `db_port`, `db_name` | PostgreSQL |
| `telegram_bot_key`, `telegram_chat_id` | Telegram bot |
| `poly_private_key` | Signing EOA private key |
| `poly_address` | Your wallet address (for trade verification) |
| `poly_funder_address` | Proxy contract address (POLY_PROXY accounts only) |
| `CHECK_GEO_IP` | `True` to enforce regional access check |

### 3. Run

```bash
# Yield farming (recommended)
python main.py --yield-farming

# Custom threshold and scan window
python main.py --yield-farming --threshold 0.97 --window 5

# Dry run — scan and log, no real orders
python main.py --yield-farming --dry-run

# Tests
pytest
pytest tests/unit/
```

---

## Key Design Decisions

### EIP-712 POLY_PROXY authentication

This bot uses POLY_PROXY (`signature_type=1`), which separates the signing EOA from the maker (a proxy contract that holds USDC). The CLOB enforces `maker ≠ signer` — orders where both match are rejected. Both `poly_private_key` (signer) and `poly_funder_address` (maker) are required.

### CLOB token resolution

The Polymarket `/trades` endpoint returns an `asset` field — the exact CLOB token ID. The bot uses `trade.asset` directly and avoids the Gamma API's `get_market_token_id()`, which can return stale token IDs.

### CLOB order sizing

Two minimums apply simultaneously:
- Per-market `minimum_order_size` (in shares)
- CLOB-enforced $1.00 minimum notional — for cheap markets (price < ~$0.20), share count is bumped via `ceil($1 / price)`

The larger of the two wins.

### Risk guard

Three independent circuit breakers halt all trading:
- **Daily loss limit** — net P&L crosses a threshold
- **Drawdown guard** — balance drops more than X% from session start
- **Balance floor** — absolute USDC minimum

Resets automatically at midnight UTC or via Telegram `/reset_risk`.

---

## Deployment

A systemd service file (`polymarket-bot.service`) is included. Two env files are required because systemd cannot parse python-dotenv format:
- `.env` — loaded by `python-dotenv` at runtime
- `systemd.env` — loaded by systemd `EnvironmentFile=` (see `systemd.env.example`)

```bash
# VPS deployment commands
systemctl status polymarket-bot
journalctl -u polymarket-bot -f
systemctl restart polymarket-bot
```

---

## License

MIT
