"""
dvol_fetcher.py
Fetches Deribit DVOL index history and latest value.

Deribit endpoint: /public/get_volatility_index_data
Returns OHLC data (open, high, low, close) for DVOL index.
Currency: BTC or ETH
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
_RESOLUTION = 3600  # hourly (seconds) - daily not supported by Deribit
_TIMEOUT_SEC = 15


class DVOLFetcher:
    """Fetches DVOL index values from Deribit for volatility regime calculation."""

    def _build_url(self, asset: str, start_ms: int, end_ms: int) -> str:
        if asset not in ("BTC", "ETH"):
            raise ValueError(f"Unsupported asset: {asset}. Must be BTC or ETH.")
        return (f"{_BASE_URL}?currency={asset}"
                f"&start_timestamp={start_ms}&end_timestamp={end_ms}"
                f"&resolution={_RESOLUTION}")

    def _parse_response(self, data: dict) -> List[Tuple[datetime, float]]:
        """Parse Deribit response into list of (datetime, dvol_value) tuples.

        API returns OHLC data: [timestamp_ms, open, high, low, close]
        We extract timestamp and close (index 4).
        """
        rows = data["result"]["data"]
        return [
            (datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc), float(row[4]))
            for row in rows
        ]

    def fetch_latest(self, asset: str) -> Optional[float]:
        """
        Fetch the most recent DVOL value for the given asset.

        Returns None on any error — callers should treat None as unavailable.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = now_ms - 2 * 24 * 3600 * 1000  # 2 days back to ensure at least 1 row
        try:
            url = self._build_url(asset, start_ms, now_ms)
            resp = requests.get(url, timeout=_TIMEOUT_SEC)
            if resp.status_code != 200:
                logger.warning("DVOL fetch failed for %s: HTTP %s", asset, resp.status_code)
                return None
            rows = self._parse_response(resp.json())
            if not rows:
                logger.warning("DVOL fetch returned empty data for %s", asset)
                return None
            return rows[-1][1]  # most recent
        except Exception as exc:
            logger.error("DVOLFetcher.fetch_latest error for %s: %s", asset, exc)
            return None

    def fetch_history(
        self, asset: str, months: int = 36
    ) -> List[Tuple[datetime, float]]:
        """
        Fetch DVOL history with pagination.

        The Deribit API returns 1000 rows max per request. Pagination works backwards:
        - continuation token marks the start of the next batch
        - To continue, request from a point before continuation up to continuation

        Returns list of (datetime, dvol_value) tuples, oldest first.
        Returns empty list on any error.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        # Calculate the cutoff once — pagination must not go beyond this point.
        # Previously start_ms was recalculated inside the loop from the shrinking
        # end_ms, causing the fetch window to slide backward forever and downloading
        # ~5 years of data instead of the intended months window.
        cutoff_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000
        )
        end_ms = now_ms
        all_rows = []

        try:
            batch_count = 0
            while True:
                batch_count += 1
                url = self._build_url(asset, cutoff_ms, end_ms)
                resp = requests.get(url, timeout=_TIMEOUT_SEC)
                if resp.status_code != 200:
                    logger.warning("DVOL history fetch failed for %s: HTTP %s", asset, resp.status_code)
                    break

                result = resp.json().get("result", {})
                rows = result.get("data", [])
                if not rows:
                    logger.info("No data returned for %s", asset)
                    break

                all_rows.extend(self._parse_response(resp.json()))
                logger.info("Batch %d for %s: %d rows", batch_count, asset, len(rows))

                continuation = result.get("continuation")
                if not continuation or continuation <= cutoff_ms:
                    logger.info("Reached earliest data for %s", asset)
                    break

                end_ms = continuation

            logger.info("DVOLFetcher: fetched %d total rows for %s in %d batches",
                        len(all_rows), asset, batch_count)
            return sorted(all_rows, key=lambda x: x[0])
        except Exception as exc:
            logger.error("DVOLFetcher.fetch_history error for %s: %s", asset, exc)
            return []
