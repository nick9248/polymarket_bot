"""
trades_service.py
Service for fetching and parsing Polymarket wallet trade data.
"""

import logging

from core.api import polymarket_client
from core.models.trades import TradeEntry
from utility.helpers import retry

logger = logging.getLogger(__name__)

DEFAULT_TRADES_LIMIT = 5


def fetch_user_trades(wallet: str, limit: int = DEFAULT_TRADES_LIMIT) -> list[TradeEntry]:
    """
    Fetch the most recent trades for a given wallet address.

    Args:
        wallet: Proxy wallet address (0x-prefixed).
        limit: Number of recent trades to fetch.

    Returns:
        List of TradeEntry objects, most recent first.

    Raises:
        ValueError: If wallet is empty.
        requests.HTTPError: On non-2xx API responses.
    """
    if not wallet:
        raise ValueError("wallet address cannot be empty")

    logger.info("Fetching last %d trades for wallet %s", limit, wallet)

    raw_data = retry(
        polymarket_client.get_user_trades,
        wallet=wallet,
        limit=limit,
    )

    entries = [TradeEntry.from_api_response(item) for item in raw_data]
    logger.info("Parsed %d trades for %s", len(entries), wallet)
    return entries
