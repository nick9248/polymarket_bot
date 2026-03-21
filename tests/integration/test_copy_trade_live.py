"""
test_copy_trade_live.py
Live integration test — places a REAL order on Polymarket CLOB.
Run only on the Spain VPS with valid credentials and USDC balance.

Usage:
    pytest tests/integration/test_copy_trade_live.py -v -s
"""
import pytest
from service.trades_service import fetch_user_trades
from service.copy_trade_service import execute_copy_trade

COINMAN2_WALLET = "0xTARGET_WALLET_REDACTED"


def test_copy_trade_executes_against_live_market():
    """
    Finds the first valid coinman2 trade in the 0.15–0.85 price range
    with a live CLOB order book and executes a copy trade.
    Tries all candidates in order until one succeeds.
    """
    trades = fetch_user_trades(COINMAN2_WALLET, limit=100)
    candidates = [t for t in trades if 0.15 <= t.price <= 0.85]

    assert candidates, "No candidates in 0.15–0.85 range — all of coinman2's recent trades are near-expiry"

    for candidate in candidates:
        result = execute_copy_trade(candidate)
        if result:
            return  # success

    pytest.fail(
        f"Tried {len(candidates)} candidate(s) — all skipped "
        "(near-expiry at current price, slippage, insufficient balance, or market closed)"
    )
