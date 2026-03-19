"""
config.py
Database configuration.
Reads credentials from .env using python-dotenv.
All other modules import DB_CONFIG from here — never hardcode credentials.
"""

import os
from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file)
_env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
load_dotenv(dotenv_path=_env_path)

DB_NAME = os.environ.get("DB_NAME", "polymarket_robot")

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", 5433)),
    "dbname": DB_NAME,
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", os.environ.get("db_pass", "")),
}

# Config used to connect to the default 'postgres' DB when creating polymarket_robot
DB_ADMIN_CONFIG = {
    **DB_CONFIG,
    "dbname": "postgres",  # connect to default DB first to run CREATE DATABASE
}
