"""
test_leaderboard_service.py
Unit tests for leaderboard_service — API calls are mocked.
"""

import pytest
from unittest.mock import patch, MagicMock

from service import leaderboard_service
from core.models.leaderboard import LeaderboardEntry
from utility.constants import Category, TimePeriod, OrderBy


MOCK_API_RESPONSE = [
    {
        "rank": "1", "proxyWallet": "0xaaa", "userName": "alpha",
        "xUsername": "", "vol": 5000000.0, "pnl": 1000000.0,
        "profileImage": "", "verifiedBadge": False,
    },
    {
        "rank": "2", "proxyWallet": "0xbbb", "userName": "beta",
        "xUsername": "", "vol": 3000000.0, "pnl": 500000.0,
        "profileImage": "", "verifiedBadge": False,
    },
]


@patch("service.leaderboard_service.retry")
class TestFetchLeaderboard:
    def test_returns_list_of_leaderboard_entries(self, mock_retry):
        mock_retry.return_value = MOCK_API_RESPONSE
        entries = leaderboard_service.fetch_leaderboard(limit=2)
        assert len(entries) == 2
        assert isinstance(entries[0], LeaderboardEntry)

    def test_entries_parsed_correctly(self, mock_retry):
        mock_retry.return_value = MOCK_API_RESPONSE
        entries = leaderboard_service.fetch_leaderboard(limit=2)
        assert entries[0].user_name == "alpha"
        assert entries[0].pnl == 1000000.0
        assert entries[1].rank == 2

    def test_invalid_limit_raises_value_error(self, mock_retry):
        with pytest.raises(ValueError, match="limit must be between"):
            leaderboard_service.fetch_leaderboard(limit=0)

    def test_limit_above_max_raises_value_error(self, mock_retry):
        with pytest.raises(ValueError, match="limit must be between"):
            leaderboard_service.fetch_leaderboard(limit=51)

    def test_invalid_offset_raises_value_error(self, mock_retry):
        with pytest.raises(ValueError, match="offset must be between"):
            leaderboard_service.fetch_leaderboard(offset=-1)

    def test_empty_api_response_returns_empty_list(self, mock_retry):
        mock_retry.return_value = []
        entries = leaderboard_service.fetch_leaderboard(limit=5)
        assert entries == []
