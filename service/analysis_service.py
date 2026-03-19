"""
analysis_service.py
Trader profiling and signal analysis on top of leaderboard data.
Stub — to be implemented in Phase 3.
"""

import logging

from core.models.leaderboard import LeaderboardEntry

logger = logging.getLogger(__name__)


def get_top_pnl_traders(
    entries: list[LeaderboardEntry],
    top_n: int = 10,
) -> list[LeaderboardEntry]:
    """
    Return the top N traders sorted by PnL descending.

    Args:
        entries: List of LeaderboardEntry objects.
        top_n: Number of top traders to return.

    Returns:
        Sorted list of up to top_n entries.
    """
    sorted_entries = sorted(entries, key=lambda e: e.pnl, reverse=True)
    return sorted_entries[:top_n]


def get_high_volume_traders(
    entries: list[LeaderboardEntry],
    min_volume: float = 100_000.0,
) -> list[LeaderboardEntry]:
    """
    Filter traders whose total volume exceeds a threshold.

    Args:
        entries: List of LeaderboardEntry objects.
        min_volume: Minimum volume in USD.

    Returns:
        Filtered list of entries meeting the volume threshold.
    """
    filtered = [e for e in entries if e.vol >= min_volume]
    logger.debug(
        "High-volume filter (>= $%.0f): %d/%d traders pass.",
        min_volume,
        len(filtered),
        len(entries),
    )
    return filtered


def summarise_leaderboard(entries: list[LeaderboardEntry]) -> dict:
    """
    Compute summary statistics for a leaderboard snapshot.

    Args:
        entries: List of LeaderboardEntry objects.

    Returns:
        Dict with total_traders, total_pnl, total_vol, avg_pnl, avg_vol,
        top_trader_name, top_trader_pnl.
    """
    if not entries:
        return {}

    total_pnl = sum(e.pnl for e in entries)
    total_vol = sum(e.vol for e in entries)
    top = max(entries, key=lambda e: e.pnl)

    return {
        "total_traders": len(entries),
        "total_pnl": total_pnl,
        "total_vol": total_vol,
        "avg_pnl": total_pnl / len(entries),
        "avg_vol": total_vol / len(entries),
        "top_trader_name": top.user_name,
        "top_trader_pnl": top.pnl,
    }
