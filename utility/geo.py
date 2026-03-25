import os
import time
import requests
import logging
from dotenv import load_dotenv
from utility.constants import REQUEST_TIMEOUT_SECONDS

load_dotenv()
logger = logging.getLogger(__name__)

# Cache the geo result so we don't hammer ipinfo.io every 5 seconds.
# VPS IP never changes mid-run; 10 minutes is more than sufficient.
_GEO_CACHE_TTL_SECONDS = 600
_GEO_RETRY_BACKOFF_SECONDS = 60   # wait before retrying after a failed request
_geo_cache: dict = {"result": None, "expires_at": 0.0, "last_confirmed": None}


def is_in_spain() -> bool:
    """
    Checks if the system's current public IP is located in Spain.
    If CHECK_GEO_IP is not exactly 'True', it bypasses the check.
    Result is cached for 10 minutes to avoid rate-limiting ipinfo.io.
    On request failure, falls back to the last confirmed result if available.

    Returns:
        True if check is bypassed or if the IP is in Spain ('ES').
        False otherwise.
    """
    check_geo = os.getenv("CHECK_GEO_IP", "False")

    if check_geo.lower() != "true":
        logger.info("Geo IP check is disabled via CHECK_GEO_IP (.env). Bypassing.")
        return True

    now = time.monotonic()
    if _geo_cache["result"] is not None and now < _geo_cache["expires_at"]:
        return _geo_cache["result"]

    logger.info("Geo IP check is ENABLED. Fetching IP location...")

    try:
        resp = requests.get("https://ipinfo.io/json", timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()

        country = data.get("country", "")
        ip_addr = data.get("ip", "unknown")
        logger.info("Detected IP: %s, Country: %s", ip_addr, country)

        result = country == "ES"
        if result:
            logger.info("Geo validation passed: System is in Spain.")
        else:
            logger.error("Geo validation failed: System is in %s, not ES (Spain).", country)

        _geo_cache["result"] = result
        _geo_cache["expires_at"] = now + _GEO_CACHE_TTL_SECONDS
        if result:
            _geo_cache["last_confirmed"] = result
        return result

    except requests.RequestException as e:
        logger.error("Failed to fetch IP geolocation: %s", e)
        # Back off before retrying so we don't flood ipinfo.io.
        _geo_cache["result"] = None
        _geo_cache["expires_at"] = now + _GEO_RETRY_BACKOFF_SECONDS

        # If we've previously confirmed Spain on this process run, trust it.
        # The VPS IP is static — a transient network error should not block trades.
        if _geo_cache["last_confirmed"] is True:
            logger.warning("Geo check failed but last confirmed result was Spain — allowing execution.")
            return True
        return False
