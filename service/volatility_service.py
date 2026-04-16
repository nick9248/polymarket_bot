"""
volatility_service.py
Provides a cached BTC volatility snapshot (DVOL + IV percentile) for logging
alongside each yield trade.

The IV percentile calculation requires 12 months of hourly DVOL history from
Deribit (~8,760 rows). This is expensive to fetch and changes slowly, so results
are cached for 1 hour. A stale cache is returned on fetch errors rather than
blocking the trade cycle.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np

from core.api.dvol_fetcher import DVOLFetcher
from core.api.realized_vol_fetcher import RealizedVolFetcher

logger = logging.getLogger(__name__)

_CACHE_TTL = timedelta(hours=1)
_RV_CACHE_TTL = timedelta(minutes=5)  # realized vol changes fast — refresh every 5 min
_fetcher = DVOLFetcher()
_rv_fetcher = RealizedVolFetcher()

# Module-level cache — (dvol, iv_percentile) refreshed every hour
_cached_snapshot: Optional[tuple[float, float]] = None
_cache_refreshed_at: Optional[datetime] = None

# Realized vol cache — refreshed every 5 minutes
_cached_rv: Optional[float] = None
_rv_cache_refreshed_at: Optional[datetime] = None


def get_btc_volatility_snapshot() -> Optional[tuple[float, float]]:
    """
    Return (btc_dvol, btc_iv_percentile_365d) for the current moment.

    Fetches fresh data from Deribit on first call and every hour thereafter.
    Returns the last known cached value if a refresh fails — never crashes
    the caller. Returns None only if no data has ever been successfully fetched.

    Returns:
        (dvol, iv_percentile) — e.g. (53.96, 89.6) — or None on first-call failure.
    """
    global _cached_snapshot, _cache_refreshed_at

    now = datetime.now(timezone.utc)
    cache_is_fresh = (
        _cached_snapshot is not None
        and _cache_refreshed_at is not None
        and (now - _cache_refreshed_at) < _CACHE_TTL
    )

    if cache_is_fresh:
        return _cached_snapshot

    logger.info("Volatility snapshot: refreshing BTC DVOL cache...")
    try:
        current_dvol = _fetcher.fetch_latest("BTC")
        history = _fetcher.fetch_history("BTC", months=12)

        if current_dvol is None or not history:
            logger.warning("Volatility snapshot: fetch returned no data — using stale cache")
            return _cached_snapshot  # stale but better than None

        arr = np.array([v for _, v in history])
        iv_percentile = float(np.mean(arr <= current_dvol) * 100.0)

        _cached_snapshot = (round(current_dvol, 2), round(iv_percentile, 2))
        _cache_refreshed_at = now
        logger.info(
            "Volatility snapshot refreshed: DVOL=%.2f, IV Pct=%.1f%%",
            current_dvol, iv_percentile,
        )
        return _cached_snapshot

    except Exception as exc:
        logger.error("Volatility snapshot refresh failed: %s — using stale cache", exc)
        return _cached_snapshot  # stale or None


def get_btc_realized_vol() -> Optional[float]:
    """
    Return the BTC 30-minute realized vol for the current moment.

    Refreshed every 5 minutes. Returns stale value on Binance error rather than
    blocking the trade cycle. Returns None only if no data has ever been fetched.

    Returns:
        Annualised realized vol (e.g. 0.35 = 35%), or None on first-call failure.
    """
    global _cached_rv, _rv_cache_refreshed_at

    now = datetime.now(timezone.utc)
    cache_is_fresh = (
        _cached_rv is not None
        and _rv_cache_refreshed_at is not None
        and (now - _rv_cache_refreshed_at) < _RV_CACHE_TTL
    )

    if cache_is_fresh:
        return _cached_rv

    logger.info("Realized vol: refreshing BTC 30-min rv cache...")
    rv = _rv_fetcher.fetch()
    if rv is not None:
        _cached_rv = round(rv, 4)
        _rv_cache_refreshed_at = now
        logger.info("Realized vol refreshed: BTC rv=%.4f", rv)
    else:
        logger.warning("Realized vol fetch failed — using stale cache (rv=%s)", _cached_rv)

    return _cached_rv
