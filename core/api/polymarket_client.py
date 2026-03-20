"""
polymarket_client.py
Raw HTTP client for the Polymarket Data API.
Responsibility: make the HTTP request and return raw parsed JSON.
No business logic — just connect, fetch, and return.
"""

import logging
import requests

from utility.endpoints import LEADERBOARD, BUILDER_LEADERBOARD, TRADES
from utility.constants import (
    Category,
    TimePeriod,
    OrderBy,
    DEFAULT_LIMIT,
    DEFAULT_OFFSET,
    REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


def get_leaderboard(
    category: Category = Category.OVERALL,
    time_period: TimePeriod = TimePeriod.DAY,
    order_by: OrderBy = OrderBy.PNL,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
    user: str | None = None,
    user_name: str | None = None,
) -> list[dict]:
    """
    Fetch raw trader leaderboard data from the Polymarket Data API.

    Args:
        category: Market category to filter by.
        time_period: Time window for rankings.
        order_by: Sort by PnL or Volume.
        limit: Number of traders to return (1–50).
        offset: Pagination offset (0–1000).
        user: Filter to a single trader by wallet address.
        user_name: Filter to a single trader by username.

    Returns:
        List of raw dicts from the API response JSON.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        requests.ConnectionError: If the network is unreachable.
        requests.Timeout: If the request exceeds REQUEST_TIMEOUT_SECONDS.
    """
    params = {
        "category": category.value,
        "timePeriod": time_period.value,
        "orderBy": order_by.value,
        "limit": limit,
        "offset": offset,
    }

    if user:
        params["user"] = user
    if user_name:
        params["userName"] = user_name

    logger.debug(
        "Fetching leaderboard — category=%s timePeriod=%s orderBy=%s limit=%d offset=%d",
        category.value,
        time_period.value,
        order_by.value,
        limit,
        offset,
    )

    response = requests.get(
        LEADERBOARD,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    data = response.json()
    logger.debug("Received %d leaderboard entries.", len(data))
    return data


def get_builder_leaderboard(
    time_period: TimePeriod = TimePeriod.DAY,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
) -> list[dict]:
    """
    Fetch raw builder leaderboard data from the Polymarket Data API.

    Args:
        time_period: Aggregation time window.
        limit: Number of builders to return (1–50).
        offset: Pagination offset (0–1000).

    Returns:
        List of raw dicts from the API response JSON.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        requests.ConnectionError: If the network is unreachable.
        requests.Timeout: If the request exceeds REQUEST_TIMEOUT_SECONDS.
    """
    params = {
        "timePeriod": time_period.value,
        "limit": limit,
        "offset": offset,
    }

    logger.debug(
        "Fetching builder leaderboard — timePeriod=%s limit=%d offset=%d",
        time_period.value,
        limit,
        offset,
    )

    response = requests.get(
        BUILDER_LEADERBOARD,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    data = response.json()
    logger.debug("Received %d builder leaderboard entries.", len(data))
    return data


def get_user_trades(wallet: str, limit: int = 5) -> list[dict]:
    """
    Fetch raw trade data for a specific wallet from the Polymarket Data API.

    Args:
        wallet: Proxy wallet address (0x-prefixed, 40-hex chars).
        limit: Number of most-recent trades to return.

    Returns:
        List of raw dicts from the API /trades JSON response.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        requests.ConnectionError: If the network is unreachable.
        requests.Timeout: If the request exceeds REQUEST_TIMEOUT_SECONDS.
    """
    params = {
        "user": wallet,
        "limit": limit,
    }

    logger.debug("Fetching trades for wallet=%s limit=%d", wallet, limit)

    response = requests.get(
        TRADES,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    data = response.json()
    logger.debug("Received %d trades for wallet %s.", len(data), wallet)
    return data


def get_market_token_id(condition_id: str, outcome_index: int) -> str:
    """
    Fetch the clobTokenId for a specific market condition and outcome.
    
    Args:
        condition_id: The unique conditionId for the Polymarket market.
        outcome_index: The integer index of the chosen outcome (e.g. 0 for Yes).
        
    Returns:
        The exact string token ID for the CLOB API.
    """
    url = f"https://gamma-api.polymarket.com/markets?condition_id={condition_id}"
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    
    data = response.json()
    if not data:
        logger.warning("No market found for condition_id: %s", condition_id)
        return ""
        
    market = data[0]
    tokens_str = market.get("clobTokenIds", "[]")
    
    import json
    tokens = json.loads(tokens_str)
    
    if outcome_index < len(tokens):
        return tokens[outcome_index]
        
    logger.warning("Outcome index %d out of range for tokens: %s", outcome_index, tokens)
    return ""
