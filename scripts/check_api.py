"""
check_api.py
Quick connectivity test for Polymarket API endpoints.
Run this first when debugging API issues.

Usage:
    python -m scripts.check_api
"""

import logging
import requests

from utility.logger import init_logging
from utility.endpoints import LEADERBOARD, BUILDER_LEADERBOARD
from utility.constants import REQUEST_TIMEOUT_SECONDS

init_logging(level="INFO")
logger = logging.getLogger(__name__)

ENDPOINTS_TO_CHECK = {
    "Trader Leaderboard": f"{LEADERBOARD}?limit=1",
    "Builder Leaderboard": f"{BUILDER_LEADERBOARD}?limit=1",
}


def check_endpoint(name: str, url: str) -> bool:
    """Check a single endpoint is reachable and returns valid JSON."""
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        logger.info("✅ %-25s HTTP %d — %d item(s) returned", name, response.status_code, len(data))
        return True
    except requests.Timeout:
        logger.error("❌ %-25s TIMEOUT after %ds", name, REQUEST_TIMEOUT_SECONDS)
        return False
    except requests.HTTPError as exc:
        logger.error("❌ %-25s HTTP ERROR: %s", name, exc)
        return False
    except Exception as exc:
        logger.error("❌ %-25s UNEXPECTED ERROR: %s", name, exc)
        return False


def main() -> None:
    logger.info("Checking Polymarket API endpoints...")
    for name, url in ENDPOINTS_TO_CHECK.items():
        check_endpoint(name, url)
    logger.info("Done.")


if __name__ == "__main__":
    main()
