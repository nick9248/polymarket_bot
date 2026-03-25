"""
validator_service.py
Reconciliation service: compares target wallet trades against our own executed
trades, identifies gaps (missed executions), and returns them for retry.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from service.trades_service import fetch_user_trades
from core.models.trades import TradeEntry

load_dotenv()
logger = logging.getLogger(__name__)

# How far back to look when reconciling missed trades
LOOKBACK_MINUTES = 30


def find_missed_trades(
    target_trades: list[TradeEntry],
    lookback_minutes: int = LOOKBACK_MINUTES,
    already_executed: set[tuple[str, str]] | None = None,
) -> list[TradeEntry]:
    """
    Compare target wallet's recent trades against our own proxy wallet executions.

    Returns trades the target made that we did NOT mirror, filtered to only those
    we could still attempt (price in 0.01–0.99 range, within lookback window).

    Matching is done by (asset, side) pair — if we executed on the same CLOB token
    with the same direction within the window, it counts as covered.

    Args:
        target_trades: Full trade buffer already fetched for the target wallet this cycle.
        lookback_minutes: How far back (in minutes) to look for missed trades.
        already_executed: Set of (asset, side) pairs executed THIS cycle by the main loop.
            These are excluded from retry to prevent double-execution caused by API
            propagation lag (our order won't appear in the Polymarket API within 5s).

    Returns:
        List of TradeEntry objects representing missed executions to retry.
    """
    wallet = os.getenv("poly_funder_address", "").strip(" '\"")
    if not wallet:
        logger.warning("Validator: No poly_funder_address configured — skipping reconciliation.")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)

    # Filter target trades to the lookback window and CLOB-valid price range only.
    # Official constraint is 0.01–0.99; outside that the CLOB rejects orders outright.
    recent_target = [
        t for t in target_trades
        if t.datetime_utc >= cutoff and 0.01 < t.price < 0.99
    ]

    if not recent_target:
        return []

    # Fetch our own recent trades to see what we actually executed
    try:
        our_trades = fetch_user_trades(wallet, limit=50)
    except Exception as e:
        logger.error("Validator: Failed to fetch own wallet trades: %s", e)
        return []

    # Build a set of (asset, side) pairs we have already executed within the window
    our_executed = {
        (t.asset, t.side)
        for t in our_trades
        if t.datetime_utc >= cutoff
    }

    # Merge API-confirmed executions with same-cycle executions (which won't be in
    # the API yet due to propagation lag — this is what caused 5x duplicate orders).
    if already_executed:
        our_executed |= already_executed

    # Find target trades with no matching execution on our side.
    # Deduplicate by (asset, side) — stingo43 may enter the same market multiple
    # times; we only need one execution per direction to cover the position.
    seen: set[tuple[str, str]] = set()
    missed = []
    for t in recent_target:
        key = (t.asset, t.side)
        if key not in our_executed and key not in seen:
            missed.append(t)
            seen.add(key)

    if missed:
        logger.info(
            "Validator: %d missed execution(s) in the last %d min "
            "(target had %d eligible trade(s) in window, we have %d executed).",
            len(missed), lookback_minutes, len(recent_target), len(our_executed),
        )
    else:
        logger.info(
            "Validator: All %d recent target trade(s) accounted for — no gaps.",
            len(recent_target),
        )

    return missed
