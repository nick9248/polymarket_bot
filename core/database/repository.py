"""
repository.py
All SQL queries in one place. Receives an open connection — does not manage connections.
Business logic lives in db_service.py. This layer is pure SQL I/O.
"""

import logging
from datetime import datetime, timezone

import psycopg2.extensions

from core.models.leaderboard import LeaderboardEntry
from core.models.trades import TradeEntry

logger = logging.getLogger(__name__)


def save_leaderboard_snapshot(
    conn: psycopg2.extensions.connection,
    entries: list[LeaderboardEntry],
    period: str,
    category: str,
) -> int:
    """
    Insert a leaderboard snapshot (one row per trader entry).

    Args:
        conn: Open psycopg2 connection.
        entries: Leaderboard entries to persist.
        period: Time period string (e.g. 'ALL', 'WEEKLY').
        category: Category string (e.g. 'OVERALL', 'CRYPTO').

    Returns:
        Number of rows inserted.
    """
    sql = """
        INSERT INTO leaderboard_snapshots
            (period, category, rank, proxy_wallet, user_name, pnl, vol, verified)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    rows = [
        (period, category, e.rank, e.proxy_wallet, e.user_name, e.pnl, e.vol, e.verified_badge)
        for e in entries
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    logger.debug("Inserted %d leaderboard snapshot rows.", len(rows))
    return len(rows)


def save_trades(
    conn: psycopg2.extensions.connection,
    trades: list[TradeEntry],
) -> int:
    """
    Insert trades, silently skipping any that already exist (by transaction_hash).

    Args:
        conn: Open psycopg2 connection.
        trades: Trade entries to persist.

    Returns:
        Number of NEW rows inserted (duplicates excluded).
    """
    sql = """
        INSERT INTO trader_trades
            (proxy_wallet, side, size, price, traded_at, title, outcome,
             transaction_hash, slug, condition_id)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (transaction_hash) DO NOTHING
    """
    rows = [
        (
            t.proxy_wallet,
            t.side,
            t.size,
            t.price,
            datetime.fromtimestamp(t.timestamp, tz=timezone.utc),
            t.title,
            t.outcome,
            t.transaction_hash,
            t.slug,
            t.condition_id,
        )
        for t in trades
    ]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
        inserted = cur.rowcount if cur.rowcount >= 0 else len(rows)
    conn.commit()
    logger.debug("Saved trades: %d attempted, %d inserted (rest already existed).", len(rows), inserted)
    return inserted


def upsert_tracked_wallets(
    conn: psycopg2.extensions.connection,
    entries: list[LeaderboardEntry],
) -> None:
    """
    Insert or update tracked wallets from leaderboard entries.
    Updates user_name if the wallet already exists.

    Args:
        conn: Open psycopg2 connection.
        entries: Leaderboard entries whose wallets should be tracked.
    """
    sql = """
        INSERT INTO tracked_wallets (proxy_wallet, user_name)
        VALUES (%s, %s)
        ON CONFLICT (proxy_wallet)
        DO UPDATE SET user_name = EXCLUDED.user_name
    """
    rows = [(e.proxy_wallet, e.user_name) for e in entries]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    logger.debug("Upserted %d tracked wallets.", len(rows))


def get_latest_trade_hashes(
    conn: psycopg2.extensions.connection,
    wallet: str,
    limit: int = 10,
) -> list[str]:
    """
    Return the most recent transaction hashes stored for a wallet.
    Used to detect new trades (Telegram alert deduplication).

    Args:
        conn: Open psycopg2 connection.
        wallet: Proxy wallet address.
        limit: How many recent hashes to return.

    Returns:
        List of transaction hash strings, most recent first.
    """
    sql = """
        SELECT transaction_hash
        FROM trader_trades
        WHERE proxy_wallet = %s
        ORDER BY traded_at DESC
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (wallet, limit))
        rows = cur.fetchall()
    return [row[0] for row in rows]
