"""
polymarket_client.py
Raw HTTP client for the Polymarket Data API.
Responsibility: make the HTTP request and return raw parsed JSON.
No business logic — just connect, fetch, and return.
"""

import json
import logging
import time
import requests

from utility.endpoints import LEADERBOARD, BUILDER_LEADERBOARD, TRADES, ACTIVITY, POSITIONS, CLOSED_POSITIONS
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


def get_user_positions(wallet: str) -> list[dict]:
    """
    Fetch all current open positions for a wallet from the Polymarket Data API.

    Args:
        wallet: Proxy wallet address (0x-prefixed, 40-hex chars).

    Returns:
        List of raw position dicts. Each contains size, avgPrice, initialValue,
        currentValue, cashPnl, percentPnl, totalBought, realizedPnl, curPrice,
        redeemable, mergeable, conditionId, title, outcome, endDate.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        requests.ConnectionError: If the network is unreachable.
        requests.Timeout: If the request exceeds REQUEST_TIMEOUT_SECONDS.
    """
    params = {"user": wallet, "limit": 500}

    logger.debug("Fetching open positions for wallet=%s", wallet)

    response = requests.get(POSITIONS, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    data = response.json()
    logger.debug("Received %d open positions for wallet %s.", len(data), wallet)
    return data


def get_user_closed_positions(wallet: str, max_results: int = 500) -> list[dict]:
    """
    Fetch historical closed positions for a wallet, paginating through all pages.

    The API returns at most 50 per page. Pagination stops when a page returns
    fewer than 50 items or max_results is reached. Any HTTP error mid-pagination
    raises immediately — no partial results are returned silently.

    Args:
        wallet: Proxy wallet address (0x-prefixed, 40-hex chars).
        max_results: Maximum total closed positions to retrieve.

    Returns:
        List of raw closed-position dicts. Each contains realizedPnl, avgPrice,
        totalBought (in shares), curPrice, conditionId, title, outcome, timestamp.

    Raises:
        requests.HTTPError: If any page request returns a non-2xx status.
        requests.ConnectionError: If the network is unreachable.
        requests.Timeout: If any request exceeds REQUEST_TIMEOUT_SECONDS.
    """
    page_size = 50
    all_positions = []
    offset = 0

    logger.debug("Fetching closed positions for wallet=%s (max=%d)", wallet, max_results)

    while len(all_positions) < max_results:
        params = {
            "user": wallet,
            "limit": page_size,
            "offset": offset,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }

        response = requests.get(CLOSED_POSITIONS, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()

        page = response.json()
        all_positions.extend(page)

        if len(page) < page_size:
            break

        offset += page_size

    logger.debug("Received %d closed positions for wallet %s.", len(all_positions), wallet)
    return all_positions[:max_results]


def get_user_activity(wallet: str, limit: int = 500) -> list[dict]:
    """
    Fetch on-chain activity events for a wallet from the Polymarket Data API.
    Includes TRADE, REDEEM, SPLIT, and MERGE event types.

    Args:
        wallet: Proxy wallet address (0x-prefixed, 40-hex chars).
        limit: Number of most-recent events to return (max 500).

    Returns:
        List of raw activity dicts. Each contains type, usdcSize, conditionId,
        title, timestamp, and (for trades) side, price, size, asset.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        requests.ConnectionError: If the network is unreachable.
        requests.Timeout: If the request exceeds REQUEST_TIMEOUT_SECONDS.
    """
    params = {"user": wallet, "limit": limit}

    logger.debug("Fetching activity for wallet=%s limit=%d", wallet, limit)

    response = requests.get(ACTIVITY, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    data = response.json()
    logger.debug("Received %d activity events for wallet %s.", len(data), wallet)
    return data


def get_market_token_id(condition_id: str, outcome_index: int) -> str:
    """
    Fetch the clobTokenId for a specific market condition and outcome.
    Retries up to 3 times on transient failures.

    Args:
        condition_id: The unique conditionId for the Polymarket market.
        outcome_index: The integer index of the chosen outcome (e.g. 0 for Yes).

    Returns:
        The exact string token ID for the CLOB API, or "" on failure.
    """
    url = f"https://gamma-api.polymarket.com/markets?condition_id={condition_id}"

    for attempt in range(3):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()

            data = response.json()
            if not data:
                logger.warning("No market found for condition_id: %s", condition_id)
                return ""

            market = data[0]
            tokens_str = market.get("clobTokenIds", "[]")

            try:
                tokens = json.loads(tokens_str)
            except json.JSONDecodeError:
                logger.error("Could not parse clobTokenIds for condition_id: %s — raw: %s", condition_id, tokens_str)
                return ""

            if outcome_index < len(tokens):
                return tokens[outcome_index]

            logger.warning("Outcome index %d out of range for tokens: %s", outcome_index, tokens)
            return ""

        except requests.RequestException as e:
            if attempt < 2:
                logger.warning("Token resolution attempt %d failed: %s — retrying...", attempt + 1, e)
                time.sleep(1)
            else:
                logger.error("Failed to resolve token_id after 3 attempts: %s", e)
                return ""

    return ""
