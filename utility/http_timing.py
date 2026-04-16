"""
http_timing.py
Thin wrappers around requests.get / requests.post that log millisecond latency
for every HTTP call. Use these instead of raw requests.get/post in any
latency-sensitive code path.

Log format (INFO level, grep-friendly):
  [TIMING] GET <url> (<label>) → <status>  <ms>ms
  [TIMING] POST <url> (<label>) → <status>  <ms>ms
"""

import time
import logging
import requests
from typing import Any

logger = logging.getLogger(__name__)


def timed_get(url: str, *, label: str = "", **kwargs: Any) -> requests.Response:
    """
    requests.get with millisecond timing logged at INFO level.

    Args:
        url: Request URL.
        label: Short human-readable description (e.g. "Gamma scan", "CLOB book").
        **kwargs: Passed directly to requests.get (params, timeout, headers, etc.)

    Returns:
        The requests.Response object (same as requests.get).
    """
    t0 = time.perf_counter()
    resp = requests.get(url, **kwargs)
    ms = (time.perf_counter() - t0) * 1000
    tag = f" ({label})" if label else ""
    logger.info("[TIMING] GET %s%s → %d  %.0fms", url[:90], tag, resp.status_code, ms)
    return resp


def timed_post(url: str, *, label: str = "", **kwargs: Any) -> requests.Response:
    """
    requests.post with millisecond timing logged at INFO level.

    Args:
        url: Request URL.
        label: Short human-readable description (e.g. "CLOB order").
        **kwargs: Passed directly to requests.post.

    Returns:
        The requests.Response object (same as requests.post).
    """
    t0 = time.perf_counter()
    resp = requests.post(url, **kwargs)
    ms = (time.perf_counter() - t0) * 1000
    tag = f" ({label})" if label else ""
    logger.info("[TIMING] POST %s%s → %d  %.0fms", url[:90], tag, resp.status_code, ms)
    return resp


def timed_sdk_call(fn, *args, label: str = "", **kwargs) -> Any:
    """
    Times any callable (e.g. a py_clob_client SDK method) that doesn't use
    raw requests directly. Logs the wall-clock duration including internal HTTP.

    Args:
        fn: Callable to time.
        *args: Positional args forwarded to fn.
        label: Short description for the log entry.
        **kwargs: Keyword args forwarded to fn.

    Returns:
        Whatever fn returns.

    Raises:
        Re-raises any exception from fn after logging the duration.
    """
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        ms = (time.perf_counter() - t0) * 1000
        tag = f" ({label})" if label else ""
        logger.info("[TIMING] SDK%s → OK  %.0fms", tag, ms)
        return result
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        tag = f" ({label})" if label else ""
        logger.warning("[TIMING] SDK%s → ERROR  %.0fms  %s", tag, ms, exc)
        raise
