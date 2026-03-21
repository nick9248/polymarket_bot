"""
trades.py
Data model for a single Polymarket trade entry.
Pure data — no business logic, no API calls.

Live API response shape confirmed from GET /trades?user=<wallet>:
  proxyWallet, side, size, price, timestamp, title, outcome, transactionHash
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class TradeEntry:
    """
    Represents a single trade made by a Polymarket trader.

    Attributes:
        proxy_wallet: On-chain wallet address of the trader.
        side: "BUY" or "SELL".
        size: Share quantity traded.
        price: Price per share (0.0 – 1.0), implied probability.
        timestamp: Unix timestamp (seconds) of the trade.
        title: Human-readable market question (e.g. "Will X win?").
        outcome: Outcome bet on (e.g. "Yes", "No").
        outcome_index: Numeric index of the outcome (0 = first outcome).
        transaction_hash: On-chain transaction hash.
        slug: Market URL slug.
        condition_id: Unique market condition identifier.
    """

    proxy_wallet: str
    side: str
    size: float
    price: float
    timestamp: int
    title: str
    outcome: str
    outcome_index: int
    transaction_hash: str
    slug: str
    condition_id: str
    asset: str  # CLOB token ID — use directly instead of resolving via gamma API

    @classmethod
    def from_api_response(cls, data: dict) -> "TradeEntry":
        """
        Construct a TradeEntry from a raw API response dictionary.

        Args:
            data: Single dict item from the Polymarket /trades JSON response.

        Returns:
            A populated TradeEntry instance.

        Raises:
            KeyError: If a required field is missing from the response.
        """
        return cls(
            proxy_wallet=data["proxyWallet"],
            side=data["side"],
            size=float(data.get("size", 0.0)),
            price=float(data.get("price", 0.0)),
            timestamp=int(data["timestamp"]),
            title=data.get("title", ""),
            outcome=data.get("outcome", ""),
            outcome_index=int(data.get("outcomeIndex", 0)),
            transaction_hash=data.get("transactionHash", ""),
            slug=data.get("slug", ""),
            condition_id=data.get("conditionId", ""),
            asset=data.get("asset", ""),
        )

    @property
    def datetime_utc(self) -> datetime:
        """Return the trade timestamp as a UTC datetime object."""
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    @property
    def usdc_value(self) -> float:
        """Approximate USD value of the trade: size × price."""
        return self.size * self.price
