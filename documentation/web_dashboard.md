# Web Dashboard

This document covers the architecture, deployment, and daily operations of the Polymarket Analyzer web dashboard — a read-only Flask interface for exploring leaderboard data and trader intelligence without touching the running bot.

---

## 1. What It Is

A single-page browser dashboard that reuses the project's existing services (leaderboard, trades, analysis) to surface two views:

| Section | Description |
|---|---|
| **Super-Traders** | Cards for traders appearing across multiple leaderboards simultaneously — strong signal of consistent edge |
| **Efficiency Ranking** | Sortable table ranking all fetched traders by RoV% (Return on Volume = PnL ÷ Volume × 100), with BOT/HUMAN detection |

**Key constraint**: the dashboard is entirely read-only. It makes no DB writes, sends no Telegram alerts, and places no orders. It is safe to run alongside the live bot.

---

## 2. Architecture

```
web_page/
├── app.py        — Flask server; exposes /api/meta and /api/run
└── index.html    — Single-page UI (vanilla JS, no framework)
```

```
Browser
   │  GET /
   │  → index.html (served as static file)
   │
   │  POST /api/run  { categories, periods, limit, order_by }
   ▼
app.py
   ├── leaderboard_service.fetch_leaderboard()   ← Polymarket Data API
   ├── trades_service.fetch_user_trades()         ← per-trader trade history
   ├── Analyzer.get_overlapping_traders()         ← multi-leaderboard filter
   ├── Analyzer.get_efficiency_ranking()          ← RoV% sort
   ├── Analyzer.detect_hft_patterns()             ← BOT/HUMAN detection
   └── StrategyAnalyzer.determine_profile()       ← classification label
         │
         ▼
   JSON response → browser renders cards + table
```

`app.py` adds the project root to `sys.path` so it can import from `service/`, `analysis/`, and `utility/` exactly as `main.py` does.

---

## 3. API Endpoints

### `GET /`
Returns `index.html`. The entire UI is a single static file.

### `GET /api/meta`
Returns the available filter options (categories, periods, order_by values). Currently unused by the frontend (which hardcodes the same values), but available for future tooling.

```json
{
  "categories": ["OVERALL","POLITICS","SPORTS","CRYPTO","CULTURE","MENTIONS","WEATHER","ECONOMICS","TECH","FINANCE"],
  "periods":    ["DAY","WEEK","MONTH","ALL"],
  "order_by":   ["PNL","VOL"]
}
```

### `POST /api/run`

**Request body:**
```json
{
  "categories": ["CRYPTO", "POLITICS"],
  "periods":    ["ALL", "MONTH"],
  "limit":      10,
  "order_by":   "PNL"
}
```

**Response:**
```json
{
  "overlapping": [ ...traders present in 2+ leaderboards... ],
  "ranking":     [ ...all traders ranked by RoV%... ]
}
```

Each trader object in `ranking` includes: `rank`, `user_name`, `proxy_wallet`, `x_username`, `profile_image`, `verified_badge`, `lists`, `pnl`, `vol`, `rov_percentage`, `is_bot`, `trades_per_day`, `classification`, `description`, `bot_reasons`.

---

## 4. UI Controls

| Control | Values | Default |
|---|---|---|
| Category | OVERALL, POLITICS, SPORTS, CRYPTO, CULTURE, MENTIONS, WEATHER, ECONOMICS, TECH, FINANCE | CRYPTO |
| Time Period | DAY, WEEK, MONTH, ALL | MONTH + ALL |
| Limit | 1–50 entries per leaderboard | 10 |
| Order By | PNL, VOL | PNL |

Multiple categories and periods can be selected simultaneously. The backend fetches every combination and deduplicates by wallet address — a trader appearing in both CRYPTO/ALL and CRYPTO/MONTH gets a single entry with both periods listed.

---

## 5. Running Locally

```bash
# From project root
pip install flask flask-cors
python web_page/app.py
```

Then open `http://localhost:5050`.

Port can be overridden:
```bash
PORT=8080 python web_page/app.py
```

---

## 6. VPS Deployment

The dashboard runs on the Spain VPS (`65.20.101.75`) alongside the main bot. It was deployed manually via `scp` (the VPS has no git repo).

### Deployed files

| File | VPS path |
|---|---|
| `web_page/app.py` | `/home/nick/polymarket_bot/web_page/app.py` |
| `web_page/index.html` | `/home/nick/polymarket_bot/web_page/index.html` |

### How it was started

```bash
cd /home/nick/polymarket_bot
nohup .venv/bin/python web_page/app.py > /var/log/polymarket-dashboard.log 2>&1 &
```

`nohup` keeps it running after SSH disconnects. It will not auto-restart if it crashes — see the Operations section for how to restart it.

### Firewall

Port 5050 is open:

| Port | Protocol | Purpose |
|---|---|---|
| 22 | TCP | SSH |
| 51820 | UDP | WireGuard (reserved) |
| 5050 | TCP | Web dashboard |

---

## 7. Accessing the Dashboard

### Option A — Direct IP (from anywhere, no SSH needed)

```
http://65.20.101.75:5050
```

No authentication. Anyone with the IP can access it.

### Option B — SSH tunnel (private, encrypted)

```bash
ssh -L 5050:localhost:5050 root@spain-vpn
```

Then open `http://localhost:5050`. Traffic is encrypted through SSH and port 5050 is not publicly required. Close the terminal to close the tunnel.

---

## 8. Operations

### Check if the dashboard is running

```bash
ssh root@spain-vpn "pgrep -fa 'web_page/app.py'"
```

### View live logs

```bash
ssh root@spain-vpn "tail -f /var/log/polymarket-dashboard.log"
```

### Stop the dashboard

```bash
ssh root@spain-vpn "pkill -f 'web_page/app.py'"
```

### Restart the dashboard

```bash
ssh root@spain-vpn "pkill -f 'web_page/app.py'; sleep 1; cd /home/nick/polymarket_bot && nohup .venv/bin/python web_page/app.py > /var/log/polymarket-dashboard.log 2>&1 &"
```

### Update a file after a code change

```bash
scp web_page/app.py root@spain-vpn:/home/nick/polymarket_bot/web_page/
scp web_page/index.html root@spain-vpn:/home/nick/polymarket_bot/web_page/
# Then restart (see above)
```

### Verify the main bot is unaffected

```bash
ssh root@spain-vpn "systemctl is-active polymarket-bot"
# Expected: active
```

---

## 9. Relationship to the Main Bot

The dashboard shares the same Python environment and service layer as `main.py` but has no operational coupling:

| Concern | Bot (`main.py`) | Dashboard (`web_page/app.py`) |
|---|---|---|
| Process | systemd service, auto-restarts | `nohup` background process |
| DB writes | Yes (trader_trades, tracked_wallets) | None |
| Telegram | Yes (alerts, commands) | None |
| CLOB orders | Yes | None |
| Port | — | 5050 |
| Restart on crash | Yes (systemd Restart=always) | No (manual restart needed) |

Deploying or restarting the dashboard has zero impact on the bot.
