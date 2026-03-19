"""
endpoints.py
All Polymarket API endpoint URLs as named constants.
Never hardcode URLs elsewhere — always import from here.
"""

# ── Polymarket Data API (free, no authentication required) ────────────────────
DATA_API_BASE_URL = "https://data-api.polymarket.com/v1"

# Trader leaderboard: PnL or Volume ranked
LEADERBOARD = f"{DATA_API_BASE_URL}/leaderboard"

# Builder/platform leaderboard
BUILDER_LEADERBOARD = f"{DATA_API_BASE_URL}/builders/leaderboard"

# Trader activity feed (TRADE, REDEEM, SPLIT events)
ACTIVITY = f"{DATA_API_BASE_URL}/activity"

# Trader trades only (cleaner than activity — BUY/SELL only)
TRADES = f"{DATA_API_BASE_URL}/trades"

# ── Polymarket CLOB API (authentication required — for trading) ───────────────
CLOB_API_BASE_URL = "https://clob.polymarket.com"
