"""
connection.py
Database connection management, database creation, and schema initialisation.

Responsibilities:
- get_connection()             : return a live psycopg2 connection to polymarket_robot DB
- create_database_if_not_exists() : create the DB if it doesn't exist yet
- init_schema()               : create all tables if they don't exist
"""

import logging
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from core.database.config import DB_CONFIG, DB_ADMIN_CONFIG, DB_NAME

logger = logging.getLogger(__name__)

# ── SQL Definitions ───────────────────────────────────────────────────────────

_CREATE_LEADERBOARD_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
    id            SERIAL PRIMARY KEY,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period        VARCHAR(10)  NOT NULL,
    category      VARCHAR(20)  NOT NULL,
    rank          INTEGER      NOT NULL,
    proxy_wallet  VARCHAR(42)  NOT NULL,
    user_name     VARCHAR(100),
    pnl           NUMERIC(18, 4),
    vol           NUMERIC(18, 4),
    verified      BOOLEAN DEFAULT FALSE
);
"""

_CREATE_TRADER_TRADES = """
CREATE TABLE IF NOT EXISTS trader_trades (
    id                SERIAL PRIMARY KEY,
    captured_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    proxy_wallet      VARCHAR(42)  NOT NULL,
    side              VARCHAR(4)   NOT NULL,
    size              NUMERIC(18, 4),
    price             NUMERIC(8, 6),
    traded_at         TIMESTAMPTZ  NOT NULL,
    title             TEXT,
    outcome           VARCHAR(50),
    transaction_hash  VARCHAR(100) UNIQUE NOT NULL,
    slug              VARCHAR(200),
    condition_id      VARCHAR(100)
);
"""

_CREATE_TRACKED_WALLETS = """
CREATE TABLE IF NOT EXISTS tracked_wallets (
    proxy_wallet  VARCHAR(42) PRIMARY KEY,
    user_name     VARCHAR(100),
    added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active        BOOLEAN DEFAULT TRUE
);
"""

_CREATE_YIELD_TRADES = """
CREATE TABLE IF NOT EXISTS yield_trades (
    id                    SERIAL PRIMARY KEY,
    token_id              TEXT NOT NULL,
    condition_id          TEXT NOT NULL,
    title                 TEXT,
    outcome               TEXT,
    signal_price          NUMERIC(6,4),
    fill_price            NUMERIC(6,4),
    shares                INTEGER,
    cost_usd              NUMERIC(10,4),
    status                TEXT NOT NULL DEFAULT 'submitted',
    clob_order_id         TEXT,
    submitted_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at           TIMESTAMPTZ,
    settled_at            TIMESTAMPTZ,
    pnl_usd               NUMERIC(10,4),
    session_balance_start NUMERIC(10,2),
    balance_before        NUMERIC(10,2)
);
"""

_CREATE_BOT_HEARTBEAT = """
CREATE TABLE IF NOT EXISTS bot_heartbeat (
    id                    INTEGER PRIMARY KEY DEFAULT 1,
    mode                  TEXT,
    last_seen             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_start_balance NUMERIC(10,2),
    current_balance       NUMERIC(10,2)
);
"""

_ALL_TABLES = [
    _CREATE_LEADERBOARD_SNAPSHOTS,
    _CREATE_TRADER_TRADES,
    _CREATE_TRACKED_WALLETS,
    _CREATE_YIELD_TRADES,
    _CREATE_BOT_HEARTBEAT,
]

# ── Public Functions ──────────────────────────────────────────────────────────

def get_connection() -> psycopg2.extensions.connection:
    """
    Return a new psycopg2 connection to the polymarket_robot database.

    Returns:
        An open psycopg2 connection. Caller is responsible for closing it.

    Raises:
        psycopg2.OperationalError: If the connection cannot be established.
    """
    return psycopg2.connect(**DB_CONFIG)


def create_database_if_not_exists() -> None:
    """
    Create the polymarket_robot database if it does not already exist.
    Connects to the default 'postgres' database to issue the CREATE DATABASE command.
    """
    conn = psycopg2.connect(**DB_ADMIN_CONFIG)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)
            )
            exists = cur.fetchone()
            if not exists:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB_NAME)))
                logger.info("Database '%s' created successfully.", DB_NAME)
            else:
                logger.info("Database '%s' already exists.", DB_NAME)
    finally:
        conn.close()


def init_schema() -> None:
    """
    Create all required tables in the polymarket_robot database if they do not exist.
    Safe to call on every startup — uses CREATE TABLE IF NOT EXISTS.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for statement in _ALL_TABLES:
                cur.execute(statement)
        conn.commit()
        logger.info("Database schema initialised (tables: leaderboard_snapshots, trader_trades, tracked_wallets, yield_trades, bot_heartbeat).")
    finally:
        conn.close()
