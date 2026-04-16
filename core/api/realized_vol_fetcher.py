"""
realized_vol_fetcher.py
Computes BTC short-term realized volatility from Binance 1-minute kline data.

Realized vol = annualised std dev of log returns over a trailing window.
At 30 minutes this captures intraday turbulence fast enough to be useful
as a pre-trade regime filter without lagging like DVOL (which is hourly).

Backtest finding (317 trades, Apr 2026):
  rv < 0.40 → 98.1% WR, EV +$0.041/trade (vs baseline -$0.074)
  Up + rv < 0.50 → 100% WR on 119 trades, EV +$0.146/trade
"""

import calendar
import logging
import math
import statistics
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BINANCE_KLINE_URL = "https://api.binance.com/api/v3/klines"
_TIMEOUT_SEC = 10
_ANNUALIZATION_FACTOR = math.sqrt(525600)  # sqrt(minutes per year)


class RealizedVolFetcher:
    """Fetches BTC short-term realized volatility from Binance."""

    def fetch(
        self,
        as_of: Optional[datetime] = None,
        window_minutes: int = 30,
    ) -> Optional[float]:
        """
        Compute annualised realized vol from BTC 1-min closes over the last window_minutes.

        Args:
            as_of: Reference time (default: now UTC).
            window_minutes: Lookback window (default: 30 minutes).

        Returns:
            Annualised realized vol (e.g. 0.35), or None on any error.
            None should be treated by callers as "data unavailable" — never hard-block on it.
        """
        if as_of is None:
            as_of = datetime.now(timezone.utc)

        ts_ms = int(calendar.timegm(as_of.timetuple())) * 1000
        start_ms = ts_ms - window_minutes * 60 * 1000

        try:
            resp = requests.get(
                _BINANCE_KLINE_URL,
                params={
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "startTime": start_ms,
                    "endTime": ts_ms,
                    "limit": window_minutes + 2,
                },
                timeout=_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            klines = resp.json()
        except Exception as exc:
            logger.warning("RealizedVolFetcher: Binance request failed: %s", exc)
            return None

        if len(klines) < 5:
            logger.warning("RealizedVolFetcher: insufficient kline data (%d rows)", len(klines))
            return None

        closes = [float(k[4]) for k in klines]
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

        if len(log_returns) < 2:
            return None

        rv = statistics.stdev(log_returns) * _ANNUALIZATION_FACTOR
        logger.debug("RealizedVolFetcher: BTC %d-min rv=%.4f", window_minutes, rv)
        return rv
