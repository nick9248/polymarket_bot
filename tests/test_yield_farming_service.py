"""
Unit tests for yield_farming_service.

Covers:
  1. _is_updown_market — title pattern filter replacing the old crypto keyword list
  2. De-duplication — failed token_ids must not be retried in subsequent cycles
"""
import importlib
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_service():
    """Fresh import with empty module-level state (_executed_token_ids reset)."""
    import service.yield_farming_service as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. _is_updown_market — title filter
# ---------------------------------------------------------------------------

class TestIsUpdownMarket:
    """_is_updown_market must accept exactly titles containing 'up or down'."""

    def test_accepts_crypto_updown(self):
        mod = _reload_service()
        assert mod._is_updown_market("Bitcoin Up or Down - March 28, 10AM ET") is True

    def test_accepts_stock_updown(self):
        mod = _reload_service()
        assert mod._is_updown_market("Tesla Up or Down - March 28, 2PM ET") is True

    def test_accepts_forex_updown(self):
        mod = _reload_service()
        assert mod._is_updown_market("EUR/USD Up or Down - March 28, 3PM ET") is True

    def test_accepts_case_insensitive(self):
        mod = _reload_service()
        assert mod._is_updown_market("solana UP OR DOWN - April 1, 9AM ET") is True

    def test_rejects_sports_market(self):
        mod = _reload_service()
        assert mod._is_updown_market("Will the Lakers beat the Celtics?") is False

    def test_rejects_politics_market(self):
        mod = _reload_service()
        assert mod._is_updown_market("Will Biden win the 2024 election?") is False

    def test_rejects_price_milestone(self):
        mod = _reload_service()
        assert mod._is_updown_market("Will Bitcoin reach $100k by end of year?") is False

    def test_rejects_empty_title(self):
        mod = _reload_service()
        assert mod._is_updown_market("") is False

    def test_old_crypto_keyword_alone_is_not_enough(self):
        """A title with 'bitcoin' but no 'up or down' must be rejected."""
        mod = _reload_service()
        assert mod._is_updown_market("Bitcoin price prediction for 2025") is False


# ---------------------------------------------------------------------------
# 2. De-duplication — failed trades must not be retried
# ---------------------------------------------------------------------------

class TestDeduplication:
    """
    After a failed trade attempt, the token_id must be added to
    _executed_token_ids so subsequent cycles skip it.
    """

    def _make_opportunity(self, token_id="tok_abc"):
        from core.models.yield_opportunity import YieldOpportunity
        from datetime import datetime, timezone, timedelta
        return YieldOpportunity(
            condition_id="cond_123",
            token_id=token_id,
            title="Bitcoin Up or Down - March 28, 10AM ET",
            outcome="Up",
            price=0.97,
            close_time=datetime.now(timezone.utc) + timedelta(minutes=3),
        )

    def test_failed_trade_is_not_retried_next_cycle(self):
        """
        A token that produced a failed trade result must appear in
        _executed_token_ids so scan_opportunities skips it next cycle.
        """
        mod = _reload_service()

        failed_result = MagicMock()
        failed_result.success = False
        failed_result.fill_price = None
        failed_result.shares = None
        failed_result.cost_usd = None
        failed_result.balance_before = None
        failed_result.order_id = None

        opp = self._make_opportunity(token_id="tok_failed")

        with patch("service.yield_farming_service.scan_opportunities", return_value=[opp]), \
             patch("service.yield_farming_service.execute_yield_trade", return_value=failed_result), \
             patch("service.db_service.insert_yield_trade"), \
             patch("service.telegram_service.send_yield_trade_submitted"):
            mod.run_yield_farming_cycle()

        assert "tok_failed" in mod._executed_token_ids

    def test_successful_trade_is_also_deduplicated(self):
        """Successful trades must still be added to _executed_token_ids (existing behaviour)."""
        mod = _reload_service()

        success_result = MagicMock()
        success_result.success = True
        success_result.fill_price = 0.97
        success_result.shares = 10.0
        success_result.cost_usd = 1.5
        success_result.balance_before = 12.0
        success_result.order_id = "order_xyz"

        opp = self._make_opportunity(token_id="tok_success")

        with patch("service.yield_farming_service.scan_opportunities", return_value=[opp]), \
             patch("service.yield_farming_service.execute_yield_trade", return_value=success_result), \
             patch("service.db_service.insert_yield_trade"), \
             patch("service.telegram_service.send_yield_trade_submitted"):
            mod.run_yield_farming_cycle()

        assert "tok_success" in mod._executed_token_ids

    def test_already_executed_token_is_skipped_in_scan(self):
        """
        scan_opportunities must exclude token_ids already in _executed_token_ids,
        so they never even reach execute_yield_trade.
        """
        mod = _reload_service()
        opp = self._make_opportunity(token_id="tok_seen")
        mod._executed_token_ids.add("tok_seen")

        fake_market = {
            "conditionId": "cond_123",
            "question": "Bitcoin Up or Down - March 28, 10AM ET",
            "endDate": "2099-01-01T00:00:00Z",
            "outcomePrices": '["0.97", "0.03"]',
            "outcomes": '["Up", "Down"]',
        }

        with patch("service.yield_farming_service._resolve_clob_token", return_value=("tok_seen", 0.97)), \
             patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = [fake_market]
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            opportunities = mod.scan_opportunities()

        assert all(o.token_id != "tok_seen" for o in opportunities)
