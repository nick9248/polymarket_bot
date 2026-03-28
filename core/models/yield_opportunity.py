"""
yield_opportunity.py
Data model for a single yield farming opportunity discovered by the market scanner.
Pure data — no business logic, no API calls.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class YieldOpportunity:
    """
    Represents a near-expiry market where one outcome has high probability.

    Attributes:
        condition_id: Unique market condition identifier.
        token_id: CLOB token ID for the high-confidence outcome.
        title: Human-readable market question.
        outcome: Outcome name (e.g. "Yes", "No", "Up").
        price: Current probability price (0.0 – 1.0) from Gamma API.
        close_time: UTC datetime when the market closes.
    """

    condition_id: str
    token_id: str
    title: str
    outcome: str
    price: float
    close_time: datetime
