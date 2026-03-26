"""
yield_trade_result.py
Return type for execute_yield_trade(). Pure data — no logic.
"""
from dataclasses import dataclass


@dataclass
class YieldTradeResult:
    """
    Outcome of a single yield trade execution attempt.

    Attributes:
        success: True if the CLOB accepted the order.
        order_id: CLOB orderID string if successful, None otherwise.
        fill_price: Actual price used for the order, None if blocked before submission.
        shares: Number of shares ordered, None if blocked before sizing.
        cost_usd: shares × fill_price, None if blocked before sizing.
        balance_before: USDC balance just before this trade was placed.
    """
    success: bool
    order_id: str | None
    fill_price: float | None
    shares: int | None
    cost_usd: float | None
    balance_before: float | None
