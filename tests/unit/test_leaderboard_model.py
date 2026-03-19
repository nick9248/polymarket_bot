"""
test_leaderboard_model.py
Unit tests for LeaderboardEntry and BuilderLeaderboardEntry data models.
"""

import pytest
from core.models.leaderboard import LeaderboardEntry, BuilderLeaderboardEntry


SAMPLE_ENTRY = {
    "rank": "1",
    "proxyWallet": "0xabc123",
    "userName": "toptrader",
    "xUsername": "@toptrader",
    "vol": 1234567.89,
    "pnl": 500000.0,
    "profileImage": "https://example.com/img.png",
    "verifiedBadge": True,
}

SAMPLE_BUILDER_ENTRY = {
    "rank": "1",
    "builder": "0xbuild123",
    "volume": 99999.99,
    "activeUsers": 42,
    "verified": True,
    "builderLogo": "https://example.com/logo.png",
}


class TestLeaderboardEntryFromApiResponse:
    def test_parses_all_fields_correctly(self):
        entry = LeaderboardEntry.from_api_response(SAMPLE_ENTRY)
        assert entry.rank == 1
        assert entry.proxy_wallet == "0xabc123"
        assert entry.user_name == "toptrader"
        assert entry.x_username == "@toptrader"
        assert entry.vol == 1234567.89
        assert entry.pnl == 500000.0
        assert entry.profile_image == "https://example.com/img.png"
        assert entry.verified_badge is True

    def test_rank_is_int(self):
        entry = LeaderboardEntry.from_api_response(SAMPLE_ENTRY)
        assert isinstance(entry.rank, int)

    def test_missing_optional_fields_default_to_empty(self):
        minimal = {
            "rank": "5",
            "proxyWallet": "0xdef456",
            "userName": "anon",
        }
        entry = LeaderboardEntry.from_api_response(minimal)
        assert entry.x_username == ""
        assert entry.vol == 0.0
        assert entry.pnl == 0.0
        assert entry.profile_image == ""
        assert entry.verified_badge is False

    def test_missing_required_field_raises_key_error(self):
        bad_data = {"rank": "1", "proxyWallet": "0x123"}  # missing userName
        with pytest.raises(KeyError):
            LeaderboardEntry.from_api_response(bad_data)

    def test_invalid_rank_raises_value_error(self):
        bad_data = {**SAMPLE_ENTRY, "rank": "not_a_number"}
        with pytest.raises(ValueError):
            LeaderboardEntry.from_api_response(bad_data)


class TestBuilderLeaderboardEntryFromApiResponse:
    def test_parses_all_fields_correctly(self):
        entry = BuilderLeaderboardEntry.from_api_response(SAMPLE_BUILDER_ENTRY)
        assert entry.rank == 1
        assert entry.builder == "0xbuild123"
        assert entry.volume == 99999.99
        assert entry.active_users == 42
        assert entry.verified is True
        assert entry.builder_logo == "https://example.com/logo.png"
