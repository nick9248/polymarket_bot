"""
db_service.py
High-level database service. Orchestrates connection lifecycle and calls repository functions.
This is the only DB-related module that main.py and other services should import.
"""

import logging

from core.database import connection, repository
from core.models.leaderboard import LeaderboardEntry
from core.models.trades import TradeEntry

logger = logging.getLogger(__name__)


def initialise_database() -> None:
    """
    Ensure the polymarket_robot database and all tables exist.
    Safe to call on every startup.
    """
    logger.info("Initialising database...")
    connection.create_database_if_not_exists()
    connection.init_schema()
    logger.info("Database ready.")


def persist_leaderboard(
    entries: list[LeaderboardEntry],
    period: str,
    category: str,
) -> int:
    """
    Save a leaderboard snapshot to the database and upsert tracked wallets.

    Args:
        entries: Leaderboard entries to save.
        period: Time period (e.g. 'ALL', 'WEEKLY').
        category: Category (e.g. 'OVERALL', 'CRYPTO').

    Returns:
        Number of rows inserted into leaderboard_snapshots.
    """
    conn = connection.get_connection()
    try:
        inserted = repository.save_leaderboard_snapshot(conn, entries, period, category)
        repository.upsert_tracked_wallets(conn, entries)
        logger.info(
            "Persisted %d leaderboard entries (period=%s, category=%s).",
            inserted, period, category,
        )
        return inserted
    finally:
        conn.close()


def persist_trades(trades: list[TradeEntry]) -> list[TradeEntry]:
    """
    Save trades to the database, skipping any already stored.

    Args:
        trades: Trade entries to save.

    Returns:
        List of TradeEntry objects that were actually NEW (inserted for the first time).
    """
    if not trades:
        return []
    conn = connection.get_connection()
    try:
        inserted_hashes = repository.save_trades(conn, trades)
        new_trades = [t for t in trades if t.transaction_hash in inserted_hashes]
        logger.info("Persisted trades: %d new inserted.", len(new_trades))
        return new_trades
    finally:
        conn.close()


def get_known_trade_hashes(wallet: str, limit: int = 10) -> list[str]:
    """
    Return the most recently stored transaction hashes for a wallet.
    Used to detect new trades before sending Telegram alerts.

    Args:
        wallet: Proxy wallet address.
        limit: Number of recent hashes to return.

    Returns:
        List of transaction hash strings.
    """
    conn = connection.get_connection()
    try:
        return repository.get_latest_trade_hashes(conn, wallet, limit)
    finally:
        conn.close()


def is_wallet_tracked(wallet: str) -> bool:
    """
    Check whether a wallet has ever been registered in tracked_wallets.
    Used to distinguish a true genesis run from a DB-was-wiped situation.

    Args:
        wallet: Proxy wallet address.

    Returns:
        True if the wallet exists in tracked_wallets, False otherwise.
    """
    conn = connection.get_connection()
    try:
        return repository.is_wallet_tracked(conn, wallet)
    finally:
        conn.close()


def upsert_wallet(wallet: str, user_name: str) -> None:
    """
    Register a wallet in tracked_wallets. Used in copy-trade mode after genesis
    seeding so that subsequent runs correctly identify the wallet as already known.

    Args:
        wallet: Proxy wallet address.
        user_name: Display name for the wallet.
    """
    conn = connection.get_connection()
    try:
        repository.upsert_single_wallet(conn, wallet, user_name)
        logger.info("Wallet registered as tracked: %s (%s)", user_name, wallet[:12] + "...")
    finally:
        conn.close()
