"""
validate_system.py
Comprehensive health check for the polymarket_robot system.
Verifies API reachability, response structure, and data plausibility.

Usage:
    python -m scripts.validate_system
"""

import sys
import logging

from utility.logger import init_logging
from utility.constants import Category, TimePeriod, OrderBy
from service import leaderboard_service

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def check_api_reachable() -> bool:
    """Check that the Polymarket Data API returns a non-empty response."""
    try:
        entries = leaderboard_service.fetch_leaderboard(limit=1)
        assert len(entries) == 1, "Expected 1 entry, got 0"
        logger.info("✅ API reachable — received 1 entry.")
        return True
    except Exception as exc:
        logger.error("❌ API unreachable: %s", exc)
        return False


def check_data_structure() -> bool:
    """Verify that leaderboard entries parse correctly into models."""
    try:
        entries = leaderboard_service.fetch_leaderboard(limit=5)
        assert len(entries) > 0, "No entries returned"
        entry = entries[0]
        assert isinstance(entry.rank, int), "rank must be int"
        assert isinstance(entry.pnl, float), "pnl must be float"
        assert isinstance(entry.vol, float), "vol must be float"
        assert isinstance(entry.user_name, str), "user_name must be str"
        logger.info("✅ Data structure valid — spot-checked 5 entries.")
        return True
    except Exception as exc:
        logger.error("❌ Data structure check failed: %s", exc)
        return False


def check_data_plausibility() -> bool:
    """Check that PnL values are non-zero and plausible for top traders."""
    try:
        entries = leaderboard_service.fetch_leaderboard(
            category=Category.OVERALL,
            time_period=TimePeriod.WEEK,
            order_by=OrderBy.PNL,
            limit=5,
        )
        top = entries[0]
        assert top.pnl > 0, f"Top PnL unexpectedly non-positive: {top.pnl}"
        assert top.rank == 1, f"First entry should be rank 1, got {top.rank}"
        logger.info(
            "✅ Data plausibility OK — top trader: %s PnL=$%.2f",
            top.user_name,
            top.pnl,
        )
        return True
    except Exception as exc:
        logger.error("❌ Data plausibility check failed: %s", exc)
        return False


def main() -> None:
    logger.info("=" * 50)
    logger.info("polymarket_robot — System Validation")
    logger.info("=" * 50)

    checks = {
        "API Reachable": check_api_reachable,
        "Data Structure": check_data_structure,
        "Data Plausibility": check_data_plausibility,
    }

    results = {}
    for name, check_fn in checks.items():
        results[name] = check_fn()

    logger.info("=" * 50)
    all_passed = all(results.values())
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info("%s  %s", status, name)
    logger.info("=" * 50)

    if all_passed:
        logger.info("All checks passed. System is healthy.")
        sys.exit(0)
    else:
        logger.error("One or more checks failed. See above for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
