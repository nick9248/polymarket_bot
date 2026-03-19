"""
test_polymarket_api.py
Integration tests — makes REAL HTTP calls to the Polymarket Data API.
Run these to verify the live API is reachable and response structure is stable.

Usage:
    pytest tests/integration/ -v
"""

import pytest
from core.api import polymarket_client
from core.models.leaderboard import LeaderboardEntry
from utility.constants import Category, TimePeriod, OrderBy


class TestGetLeaderboardLive:
    def test_returns_non_empty_list(self):
        data = polymarket_client.get_leaderboard(limit=5)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_response_contains_required_fields(self):
        data = polymarket_client.get_leaderboard(limit=1)
        entry = data[0]
        assert "rank" in entry
        assert "proxyWallet" in entry
        assert "userName" in entry
        assert "pnl" in entry
        assert "vol" in entry

    def test_rank_one_is_first_by_pnl(self):
        data = polymarket_client.get_leaderboard(
            order_by=OrderBy.PNL, limit=3
        )
        assert data[0]["rank"] == "1"

    def test_category_filter_works(self):
        data = polymarket_client.get_leaderboard(
            category=Category.CRYPTO, limit=5
        )
        assert len(data) > 0

    def test_time_period_all_works(self):
        data = polymarket_client.get_leaderboard(
            time_period=TimePeriod.ALL, limit=5
        )
        assert len(data) > 0

    def test_models_parse_without_error(self):
        data = polymarket_client.get_leaderboard(limit=10)
        entries = [LeaderboardEntry.from_api_response(item) for item in data]
        assert len(entries) == len(data)
        assert all(isinstance(e.rank, int) for e in entries)
