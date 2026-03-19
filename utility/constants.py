"""
constants.py
Global constants and enumerations for the polymarket_robot project.
"""

from enum import Enum


class Category(str, Enum):
    """Market category filter for leaderboard queries."""
    OVERALL = "OVERALL"
    POLITICS = "POLITICS"
    SPORTS = "SPORTS"
    CRYPTO = "CRYPTO"
    CULTURE = "CULTURE"
    MENTIONS = "MENTIONS"
    WEATHER = "WEATHER"
    ECONOMICS = "ECONOMICS"
    TECH = "TECH"
    FINANCE = "FINANCE"


class TimePeriod(str, Enum):
    """Time window for leaderboard rankings."""
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    ALL = "ALL"


class OrderBy(str, Enum):
    """Sort order for leaderboard results."""
    PNL = "PNL"
    VOL = "VOL"


# Leaderboard pagination limits
DEFAULT_LIMIT: int = 25
MAX_LIMIT: int = 50
MAX_OFFSET: int = 1000
DEFAULT_OFFSET: int = 0

# HTTP
REQUEST_TIMEOUT_SECONDS: int = 10
