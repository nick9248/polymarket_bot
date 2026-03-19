# VPS Setup and Operations

**Date completed**: 2026-03-15
**Status**: Fully operational

---

## 1. Why a VPS?

The local PC was running the data collection daemon but sleeping every night (~23:00–16:00), creating 16–24 hour gaps in data. The result was only **55% data coverage** — 65 gaps totalling 516 missing hours out of ~934 possible.

This directly blocked ML training: the first training window requires **720 consecutive hours** of `hourly_snapshots`. At 55% coverage that target would take months to reach.

A VPS running 24/7 eliminates the sleep gap entirely and gets to 720h in approximately 30 days of continuous uptime.

---

## 2. VPS Details

| Field | Value |
|-------|-------|
| **Provider** | Hetzner Cloud |
| **Plan** | CPX11 AMD |
| **Specs** | 2 vCPU, 2GB RAM, 40GB SSD, 1TB bandwidth |
| **Cost** | €5.94/month |
| **Location** | USA (Ashburn VA) |
| **OS** | Ubuntu 24.04 LTS |
| **IPv4** | VPS_IP_REDACTED |
| **Hostname** | option-trading-vps |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────┐
│                  HETZNER VPS                    │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  option-trading.service (systemd)        │   │
│  │  CollectionDaemon → ProspectiveCollector │   │
│  │  Every 30 minutes, 24/7                  │   │
│  └──────────────────────────────────────────┘   │
│                      ↓                          │
│  ┌──────────────────────────────────────────┐   │
│  │  PostgreSQL (port 5432, localhost only)  │   │
│  │  DB: option_trading  User: nick          │   │
│  └──────────────────────────────────────────┘   │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  Cron: check_vps_health.py (hourly)      │   │
│  │  Writes: logs/vps_health.json            │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
                        ↑
              SSH Tunnel (port 5434)
              sync_from_vps.py
                        ↓
┌─────────────────────────────────────────────────┐
│                  LOCAL PC                       │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  PostgreSQL (port 5433, localhost)       │   │
│  │  DB: option_trading  User: postgres      │   │
│  └──────────────────────────────────────────┘   │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  GUI App (PySide6)                       │   │
│  │  Database tab → Sync from VPS button     │   │
│  │  System Health tab → Check VPS Health    │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

---

## 4. Security Configuration

### User Access
- **Root login**: disabled (`PermitRootLogin no`)
- **Password authentication**: disabled (`PasswordAuthentication no`)
- **Only access**: SSH key authentication as user `nick`

### SSH Key (local PC)
- **Key file**: `C:\Users\Nick\.ssh\option_trading`
- **SSH config**: `C:\Users\Nick\.ssh\config`

```
Host option-server
    HostName VPS_IP_REDACTED
    User nick
    IdentityFile C:\Users\Nick\.ssh\option_trading
    IdentitiesOnly yes
```

Connect with: `ssh option-server`

### Firewall (UFW)
| Port | Protocol | Purpose |
|------|----------|---------|
| 22 | TCP | SSH |
| 51820 | UDP | Reserved (WireGuard, not yet configured) |

### PostgreSQL
- Bound to `localhost` only — no external port exposure
- Accessed exclusively via SSH tunnel

---

## 5. What Was Set Up (Step by Step)

### Step 1: Server Creation
- Purchased CPX11 on Hetzner Cloud
- Added SSH key during creation (important — must be done at creation, not after)
- SSH key name: `option_trading`

### Step 2: Security Hardening
- Created user `nick` with sudo privileges
- Copied SSH key to `nick` user
- Disabled root login and password authentication in `/etc/ssh/sshd_config`
- Enabled UFW firewall with ports 22/tcp and 51820/udp

### Step 3: Software Installation
- Updated system: `apt update && apt upgrade -y`
- Installed PostgreSQL 16: `apt install postgresql postgresql-contrib`
- Installed Python 3.13 via deadsnakes PPA
- Installed build tools: `python3-pip build-essential libpq-dev git`

### Step 4: Database Configuration
- Created PostgreSQL user `nick` with password `DB_PASSWORD_REDACTED`
- Created database `option_trading` owned by `nick`
- Set `listen_addresses = 'localhost'` in `postgresql.conf`
- PostgreSQL set to auto-start on boot

### Step 5: Project Deployment
- Added VPS SSH key to GitHub (key: `/root/.ssh/github_vps`)
- Configured `/root/.ssh/config` for GitHub
- Cloned repo: `git clone git@github.com:nick9248/rsc_option_trading.git`
- Created Python venv: `python3.13 -m venv .venv`
- Installed dependencies: `pip install -r requirements.txt`
- Created `.env` file with DB credentials and API token

### Step 6: Database Migrations
- Ran `migrations/000_base_schema.sql` — creates all base tables
- Ran migrations 001 through 011 in order
- Result: 24 tables created

**Key code change for VPS**: `coding/core/database/config.py` updated to read `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME` from `.env` (previously hardcoded to 5433/postgres for local PC). Defaults preserved for backward compatibility.

### Step 7: Systemd Daemon Service
- Created `/etc/systemd/system/option-trading.service`
- Service runs as user `nick`
- Auto-restarts on crash (RestartSec=30)
- Enabled on boot: `systemctl enable option-trading`

Service file:
```ini
[Unit]
Description=Option Trading Data Collection Daemon
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=nick
WorkingDirectory=/home/nick/option_trading
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/nick/option_trading/.venv/bin/python -m coding.service.data_collection.collection_daemon
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Step 8: Sync Script
- `scripts/sync_from_vps.py` — runs on local PC
- Opens SSH tunnel: local port 5434 → VPS port 5432
- Syncs 12 tables incrementally using watermark columns
- Uses `ON CONFLICT DO NOTHING` for tables with unique constraints
- Also pulls `logs/vps_health.json` from VPS

### Step 9: VPS Health Monitor
- `scripts/check_vps_health.py` — runs on VPS
- Checks 10 health points: daemon, API, DB, and 7 data tables
- Writes structured output to `logs/vps_health.json`
- Cron job runs it every hour: `0 * * * *`

### Step 10: GUI Integration
- **Database tab**: "Sync from VPS" button — runs sync, logs progress
- **System Health tab**: "Check VPS Health" button — reads local `logs/vps_health.json`

---

## 6. What the Daemon Collects

Every 30 minutes, the `ProspectiveCollector` runs for BTC and ETH:

| Step | Data | Table |
|------|------|-------|
| 1 | Recent trades (last 1000) | `historical_trades` |
| 2 | Book summary (all instruments) | `snapshots` |
| 3 | On-chain analysis (max pain, OI, GEX/DEX) | `onchain_analysis_snapshots`, `max_pain`, `open_interest`, `volume`, `gex_dex`, `levels` |
| 4 | DVOL (volatility index) | `volatility_index_history` |
| 5 | Funding rate | `funding_rate_history` |
| 6 | OHLCV daily candle | `ohlcv_history` |
| 7 | Hourly aggregation (post-collect) | `hourly_snapshots` |

---

## 7. Synced Tables

The sync script pulls these 12 tables from VPS to local PC:

| Table | Watermark Column | Conflict Handling |
|-------|-----------------|-------------------|
| `historical_trades` | `captured_at` | `(trade_id, trade_timestamp)` |
| `snapshots` | `captured_at` | none |
| `hourly_snapshots` | `snapshot_hour` | `(instrument_name, snapshot_hour)` |
| `onchain_analysis_snapshots` | `snapshot_hour` | `(snapshot_hour, currency, expiration)` |
| `funding_rate_history` | `date` | `(instrument_name, timestamp)` |
| `volatility_index_history` | `date` | `(index_name, timestamp)` |
| `ohlcv_history` | `date` | `(instrument_name, timestamp)` |
| `max_pain` | `captured_at` | none |
| `open_interest` | `captured_at` | none |
| `volume` | `captured_at` | none |
| `gex_dex` | `captured_at` | none |
| `levels` | `captured_at` | none |

---

## 8. Health Check Thresholds

| Check | Threshold | Reason |
|-------|-----------|--------|
| `historical_trades` | 35 min | Collected every 30min |
| `snapshots` | 35 min | Collected every 30min |
| `hourly_snapshots` | 70 min | Aggregated after each cycle |
| `onchain_snapshots` | 70 min | Runs per cycle, allow full buffer |
| `funding_rate_history` | 35 min | Collected every 30min |
| `dvol_history` | 70 min | Runs per cycle, allow full buffer |
| `ohlcv_history` | 25 hours | Daily candle |

---

## 9. Key Files

| File | Location | Purpose |
|------|----------|---------|
| Daemon entry point | `coding/service/data_collection/collection_daemon.py` | Main daemon |
| Collector | `coding/service/data_collection/prospective_collector.py` | Data collection logic |
| DB config | `coding/core/database/config.py` | Reads from .env |
| Base schema | `migrations/000_base_schema.sql` | All base tables for fresh DB |
| Health check | `scripts/check_vps_health.py` | VPS health monitor |
| Sync script | `scripts/sync_from_vps.py` | VPS → local sync |
| VPS .env | `/home/nick/option_trading/.env` | DB credentials + API token |

---

## 10. VPS .env Contents

```env
DB_PASSWORD=DB_PASSWORD_REDACTED
DB_HOST=localhost
DB_PORT=5432
DB_NAME=option_trading
DB_USER=nick
crypto_quant_token="..."
```

---

## 11. ML Training Progress

The VPS was set up specifically to build the ML training dataset:

| Metric | Value |
|--------|-------|
| Target | 720h of `hourly_snapshots` |
| Before VPS | ~418h (55% coverage) |
| Gap cause | PC sleeping 16-24h/night |
| Expected time to target | ~30 days from VPS start |
| Full walk-forward requirement | 1,224h (720h train + 168h test × 3 folds) |

---

## 12. Daily Operations Checklist

### When you turn on your PC

1. Open the GUI app
2. Go to **Database tab**
3. Click **Sync from VPS**
4. Watch the log — expect "Synced X rows in ~15s"

### To check VPS health

1. Click **Sync from VPS** first (to get latest health JSON)
2. Go to **System Health tab**
3. Click **Check VPS Health**
4. **Expected**: 10/10 checks OK, last checked within the past hour

### What healthy output looks like

```
✅ 10/10 checks OK
Last checked: 2026-03-15 13:00:00 (42min ago)

[OK ] systemd service RUNNING (pid XXXXX)
[OK ] Deribit API OK — BTC $XX,XXX
[OK ] Database connected (PostgreSQL 16.x)
[OK ] historical_trades: BTC X rows, latest Xmin ago | ETH X rows, latest Xmin ago
[OK ] snapshots: X,XXX instruments, latest Xmin ago
[OK ] hourly_snapshots: BTC Xh, latest Xmin ago | ETH Xh, latest Xmin ago
[OK ] onchain_snapshots: BTC X rows, latest Xmin ago | ETH X rows, latest Xmin ago
[OK ] funding_rate_history: BTC X rows, latest Xmin ago | ETH X rows, latest Xmin ago
[OK ] dvol_history: BTC X rows, latest Xmin ago | ETH X rows, latest Xmin ago
[OK ] ohlcv_history: BTC X candles, latest X.Xh ago | ETH X candles, latest X.Xh ago
```

### If a check fails

| Problem | Likely cause | Fix |
|---------|-------------|-----|
| systemd service NOT RUNNING | Daemon crashed | `ssh option-server` → `sudo systemctl restart option-trading` |
| API unreachable | Network issue on VPS | Check VPS connectivity |
| Database failed | PostgreSQL crashed | `sudo systemctl restart postgresql` |
| Table STALE (>2h) | Daemon not collecting | Check daemon logs: `journalctl -u option-trading -n 50` |

### To SSH into the VPS

```bash
ssh option-server
```

### To check daemon logs

```bash
ssh option-server
journalctl -u option-trading -n 100 --no-pager
```

### To manually run a health check on VPS

```bash
ssh option-server
cd option_trading && .venv/bin/python -m scripts.check_vps_health
```

### To restart the daemon

```bash
ssh option-server
sudo systemctl restart option-trading
sudo systemctl status option-trading
```

### To check ML data progress

```bash
ssh option-server
sudo -u postgres psql -d option_trading -c "
SELECT currency, COUNT(DISTINCT snapshot_hour) as hours
FROM hourly_snapshots
GROUP BY currency;"
```
Target: 720h per currency.
