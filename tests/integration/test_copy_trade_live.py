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

import os

COPY_TRADER_WALLET = os.getenv("COPY_TRADER_WALLET", "")


def test_copy_trade_executes_against_live_market():
    """
    Finds the first valid trade from the configured wallet in the 0.15–0.85 price range
    with a live CLOB order book and executes a copy trade.
    Tries all candidates in order until one succeeds.
    Set COPY_TRADER_WALLET env var to the target trader's wallet address.
    """
    assert COPY_TRADER_WALLET, "COPY_TRADER_WALLET env var not set"
    trades = fetch_user_trades(COPY_TRADER_WALLET, limit=100)
    candidates = [t for t in trades if 0.15 <= t.price <= 0.85]

    assert candidates, "No candidates in 0.15–0.85 range — all recent trades are near-expiry"

    for candidate in candidates:
        result = execute_copy_trade(candidate)
        if result:
            return  # success

    pytest.fail(
        f"Tried {len(candidates)} candidate(s) — all skipped "
        "(near-expiry at current price, slippage, insufficient balance, or market closed)"
    )
