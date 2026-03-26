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


def insert_yield_trade(
    *,
    token_id: str,
    condition_id: str,
    title: str,
    outcome: str,
    signal_price: float,
    fill_price: float | None,
    shares: int | None,
    cost_usd: float | None,
    clob_order_id: str | None,
    status: str,
    session_balance_start: float,
    balance_before: float,
) -> int:
    """Insert a new yield trade record. Returns the new row id."""
    conn = connection.get_connection()
    try:
        return repository.insert_yield_trade(
            conn,
            token_id=token_id,
            condition_id=condition_id,
            title=title,
            outcome=outcome,
            signal_price=signal_price,
            fill_price=fill_price,
            shares=shares,
            cost_usd=cost_usd,
            clob_order_id=clob_order_id,
            status=status,
            session_balance_start=session_balance_start,
            balance_before=balance_before,
        )
    finally:
        conn.close()


def update_yield_trade(trade_id: int, **fields) -> None:
    """Update status, fill_price, resolved_at, settled_at, or pnl_usd on a yield trade row."""
    conn = connection.get_connection()
    try:
        repository.update_yield_trade(conn, trade_id, **fields)
    finally:
        conn.close()


def get_open_yield_trades() -> list[dict]:
    """Return all yield_trades rows with status 'submitted' or 'filled'."""
    conn = connection.get_connection()
    try:
        return repository.get_open_yield_trades(conn)
    finally:
        conn.close()


def get_recent_yield_trade_statuses(limit: int = 3) -> list[str]:
    """Return the most recent N resolved yield trade statuses ('won' or 'lost')."""
    conn = connection.get_connection()
    try:
        return repository.get_recent_yield_trade_statuses(conn, limit)
    finally:
        conn.close()


def get_yield_pnl_summary() -> dict:
    """Return aggregate P&L stats: total_trades, won, lost, pending, win_rate, net_pnl."""
    conn = connection.get_connection()
    try:
        return repository.get_yield_pnl_summary(conn)
    finally:
        conn.close()


def get_yield_pnl_chart() -> list[dict]:
    """Return daily cumulative P&L data points for charting."""
    conn = connection.get_connection()
    try:
        return repository.get_yield_pnl_chart(conn)
    finally:
        conn.close()


def get_yield_trades_page(status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    """Return paginated yield_trades rows, newest first."""
    conn = connection.get_connection()
    try:
        return repository.get_yield_trades_page(conn, status=status, limit=limit, offset=offset)
    finally:
        conn.close()


def update_bot_heartbeat(mode: str, session_start_balance: float, current_balance: float) -> None:
    """Update the bot liveness heartbeat row every cycle."""
    conn = connection.get_connection()
    try:
        repository.upsert_bot_heartbeat(conn, mode=mode, session_start_balance=session_start_balance, current_balance=current_balance)
    finally:
        conn.close()


def get_bot_heartbeat() -> dict | None:
    """Return heartbeat dict or None if bot has never run."""
    conn = connection.get_connection()
    try:
        return repository.get_bot_heartbeat(conn)
    finally:
        conn.close()
