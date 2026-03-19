"""
test_trade_model.py
Unit tests for the TradeEntry data model.
"""

import pytest
from core.models.trades import TradeEntry


SAMPLE_TRADE = {
    "proxyWallet": "0xabc123",
    "side": "BUY",
    "asset": "0xasset",
    "conditionId": "0xcond",
    "size": 100000.0,
    "price": 0.52,
    "timestamp": 1773259161,
    "title": "Will X win the election?",
    "slug": "will-x-win",
    "outcome": "Yes",
    "outcomeIndex": 0,
    "transactionHash": "0xtxhash",
}


class TestTradeEntryFromApiResponse:
    def test_parses_all_fields_correctly(self):
        trade = TradeEntry.from_api_response(SAMPLE_TRADE)
        assert trade.proxy_wallet == "0xabc123"
        assert trade.side == "BUY"
        assert trade.size == 100000.0
        assert trade.price == 0.52
        assert trade.timestamp == 1773259161
        assert trade.title == "Will X win the election?"
        assert trade.outcome == "Yes"
        assert trade.outcome_index == 0
        assert trade.transaction_hash == "0xtxhash"
        assert trade.slug == "will-x-win"
        assert trade.condition_id == "0xcond"

    def test_missing_optional_fields_default_safely(self):
        minimal = {
            "proxyWallet": "0xabc",
            "side": "SELL",
            "timestamp": 1773259161,
        }
        trade = TradeEntry.from_api_response(minimal)
        assert trade.size == 0.0
        assert trade.price == 0.0
        assert trade.title == ""
        assert trade.outcome == ""
        assert trade.transaction_hash == ""
        assert trade.slug == ""
        assert trade.condition_id == ""

    def test_missing_required_field_raises_key_error(self):
        bad = {"side": "BUY", "timestamp": 123}  # missing proxyWallet
        with pytest.raises(KeyError):
            TradeEntry.from_api_response(bad)

    def test_usdc_value_property(self):
        trade = TradeEntry.from_api_response(SAMPLE_TRADE)
        assert abs(trade.usdc_value - 52000.0) < 0.01  # 100000 * 0.52

    def test_datetime_utc_property(self):
        trade = TradeEntry.from_api_response(SAMPLE_TRADE)
        dt = trade.datetime_utc
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_side_values(self):
        buy = TradeEntry.from_api_response({**SAMPLE_TRADE, "side": "BUY"})
        sell = TradeEntry.from_api_response({**SAMPLE_TRADE, "side": "SELL"})
        assert buy.side == "BUY"
        assert sell.side == "SELL"
