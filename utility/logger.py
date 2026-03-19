"""
logger.py
Centralised logging initialisation.
Call init_logging() once at the entry point (main.py / scripts).
All other modules simply call: logger = logging.getLogger(__name__)
"""

import logging
import sys

LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def init_logging(level: str = "INFO") -> None:
    """
    Initialise the root logger. Call once at application startup.

    Args:
        level: Log level string — DEBUG, INFO, WARNING, ERROR, CRITICAL.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
