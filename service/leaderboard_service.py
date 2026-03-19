"""
leaderboard_service.py
High-level service for fetching and filtering Polymarket leaderboard data.
Orchestrates core API client calls and maps responses to domain models.
"""

import logging

from core.api import polymarket_client
from core.models.leaderboard import LeaderboardEntry, BuilderLeaderboardEntry
from utility.constants import (
    Category,
    TimePeriod,
    OrderBy,
    DEFAULT_LIMIT,
    DEFAULT_OFFSET,
    MAX_LIMIT,
    MAX_OFFSET,
)
from utility.helpers import retry

logger = logging.getLogger(__name__)


def fetch_leaderboard(
    category: Category = Category.OVERALL,
    time_period: TimePeriod = TimePeriod.DAY,
    order_by: OrderBy = OrderBy.PNL,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
) -> list[LeaderboardEntry]:
    """
    Fetch and parse the Polymarket trader leaderboard.

    Args:
        category: Market category filter.
        time_period: Time window for rankings.
        order_by: Sort by PnL or Volume.
        limit: Number of results (1–50).
        offset: Pagination offset (0–1000).

    Returns:
        List of LeaderboardEntry objects sorted by rank ascending.

    Raises:
        requests.HTTPError: On non-2xx API responses.
        requests.Timeout: If request exceeds timeout.
    """
    if not (1 <= limit <= MAX_LIMIT):
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}")
    if not (0 <= offset <= MAX_OFFSET):
        raise ValueError(f"offset must be between 0 and {MAX_OFFSET}, got {offset}")

    logger.info(
        "Fetching leaderboard — category=%s period=%s orderBy=%s limit=%d offset=%d",
        category.value,
        time_period.value,
        order_by.value,
        limit,
        offset,
    )

    raw_data = retry(
        polymarket_client.get_leaderboard,
        category=category,
        time_period=time_period,
        order_by=order_by,
        limit=limit,
        offset=offset,
    )

    entries = [LeaderboardEntry.from_api_response(item) for item in raw_data]
    logger.info("Parsed %d leaderboard entries.", len(entries))
    return entries


def fetch_full_leaderboard(
    category: Category = Category.OVERALL,
    time_period: TimePeriod = TimePeriod.DAY,
    order_by: OrderBy = OrderBy.PNL,
    max_traders: int = 100,
) -> list[LeaderboardEntry]:
    """
    Fetch multiple pages of leaderboard data and return a combined list.

    Args:
        category: Market category filter.
        time_period: Time window for rankings.
        order_by: Sort by PnL or Volume.
        max_traders: Total number of traders to fetch (up to MAX_OFFSET).

    Returns:
        Combined list of LeaderboardEntry objects sorted by rank ascending.
    """
    all_entries: list[LeaderboardEntry] = []
    offset = 0

    while len(all_entries) < max_traders:
        batch_limit = min(MAX_LIMIT, max_traders - len(all_entries))
        batch = fetch_leaderboard(
            category=category,
            time_period=time_period,
            order_by=order_by,
            limit=batch_limit,
            offset=offset,
        )
        if not batch:
            logger.debug("Empty batch at offset=%d, stopping pagination.", offset)
            break

        all_entries.extend(batch)
        offset += batch_limit

    logger.info("Total leaderboard entries fetched: %d", len(all_entries))
    return all_entries


def fetch_builder_leaderboard(
    time_period: TimePeriod = TimePeriod.DAY,
    limit: int = DEFAULT_LIMIT,
    offset: int = DEFAULT_OFFSET,
) -> list[BuilderLeaderboardEntry]:
    """
    Fetch and parse the Polymarket builder leaderboard.

    Args:
        time_period: Aggregation time window.
        limit: Number of builders to return (1–50).
        offset: Pagination offset.

    Returns:
        List of BuilderLeaderboardEntry objects.
    """
    logger.info(
        "Fetching builder leaderboard — period=%s limit=%d offset=%d",
        time_period.value,
        limit,
        offset,
    )

    raw_data = retry(
        polymarket_client.get_builder_leaderboard,
        time_period=time_period,
        limit=limit,
        offset=offset,
    )

    entries = [BuilderLeaderboardEntry.from_api_response(item) for item in raw_data]
    logger.info("Parsed %d builder leaderboard entries.", len(entries))
    return entries
