"""
helpers.py
Generic, stateless utility functions used across all layers.
No business logic — only pure helper operations.
"""

import time
import logging
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry(
    func: Callable[..., T],
    *args,
    retries: int = 3,
    delay_seconds: float = 2.0,
    **kwargs,
) -> T:
    """
    Retry a callable up to `retries` times with a fixed delay between attempts.

    Args:
        func: The function to call.
        *args: Positional arguments to pass to func.
        retries: Maximum number of attempts.
        delay_seconds: Seconds to wait between retries.
        **kwargs: Keyword arguments to pass to func.

    Returns:
        The return value of func on success.

    Raises:
        The last exception raised by func if all retries are exhausted.
    """
    last_exception: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exception = exc
            logger.warning(
                "Attempt %d/%d failed for %s: %s",
                attempt,
                retries,
                func.__name__,
                exc,
            )
            if attempt < retries:
                time.sleep(delay_seconds)

    raise last_exception


def format_pnl(value: float) -> str:
    """
    Format a PnL float as a human-readable string with sign and commas.
    Example: 1234567.89 → '+$1,234,567.89'

    Args:
        value: Raw PnL float from the API.

    Returns:
        Formatted string.
    """
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


def format_volume(value: float) -> str:
    """
    Format a volume float with thousands separator.
    Example: 11814689.67 → '$11,814,689.67'

    Args:
        value: Raw volume float from the API.

    Returns:
        Formatted string.
    """
    return f"${value:,.2f}"
