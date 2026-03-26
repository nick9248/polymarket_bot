"""Unit tests for risk_guard_service. Mocks db_service — no real DB required."""
import pytest
from unittest.mock import patch


def test_allows_when_all_checks_pass():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=[]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=49.0, session_start_balance=50.0)
    assert result.allowed is True
    assert result.reason is None


def test_blocks_on_balance_floor():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=[]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=4.0, session_start_balance=50.0)
    assert result.allowed is False
    assert "balance floor" in result.reason.lower()


def test_blocks_on_drawdown():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=[]):
        from service.risk_guard_service import check_risk
        # 14% drawdown > 10% threshold
        result = check_risk(current_balance=43.0, session_start_balance=50.0)
    assert result.allowed is False
    assert "drawdown" in result.reason.lower()


def test_blocks_on_consecutive_losses():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=["lost", "lost", "lost"]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=47.0, session_start_balance=50.0)
    assert result.allowed is False
    assert "consecutive" in result.reason.lower()


def test_allows_when_not_enough_losses_yet():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=["lost", "lost"]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=48.0, session_start_balance=50.0)
    assert result.allowed is True


def test_allows_when_losses_interrupted_by_win():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=["lost", "won", "lost"]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=47.0, session_start_balance=50.0)
    assert result.allowed is True


def test_balance_floor_checked_before_drawdown():
    # Balance floor ($4 < $5 floor) should fire even though drawdown is only 2%
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=[]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=4.0, session_start_balance=4.08)
    assert result.allowed is False
    assert "balance floor" in result.reason.lower()
