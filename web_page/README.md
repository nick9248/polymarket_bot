# Polymarket Analyzer — Web Dashboard

## Quick Start

> Run from the **project root** directory (`polymarket_robot/`):

```bash
# Install dependencies (first time only)
pip install flask flask-cors

# Start the dashboard server
python web_page/app.py
```

Then open **http://localhost:5050** in your browser.

## Features

| Section | Description |
|---|---|
| **Controls** | Select category, time period, limit (1–50), and order-by |
| **Super-Traders** | Cards for traders present in multiple leaderboards |
| **Efficiency Ranking** | Sortable table with BOT/HUMAN filter and RoV% ranking |

## Notes
- The server re-uses all existing project services — no DB writes, no Telegram alerts.
- Port can be overridden with the `PORT` environment variable.
