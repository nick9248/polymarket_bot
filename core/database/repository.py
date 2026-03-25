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
) -> set[str]:
    """
    Insert trades, silently skipping any that already exist (by transaction_hash).

    Args:
        conn: Open psycopg2 connection.
        trades: Trade entries to persist.

    Returns:
        Set of transaction hashes that were actually inserted (duplicates excluded).
    """
    sql = """
        INSERT INTO trader_trades
            (proxy_wallet, side, size, price, traded_at, title, outcome,
             transaction_hash, slug, condition_id)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (transaction_hash) DO NOTHING
        RETURNING transaction_hash
    """
    inserted_hashes: set[str] = set()
    with conn.cursor() as cur:
        for t in trades:
            cur.execute(sql, (
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
            ))
            row = cur.fetchone()
            if row:
                inserted_hashes.add(row[0])
    conn.commit()
    logger.debug("Saved trades: %d attempted, %d inserted (rest already existed).", len(trades), len(inserted_hashes))
    return inserted_hashes


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


def is_wallet_tracked(conn: psycopg2.extensions.connection, wallet: str) -> bool:
    """
    Check if a wallet has ever been registered in tracked_wallets.
    Used to distinguish a true genesis run from an empty-trades-in-DB situation.

    Args:
        conn: Open psycopg2 connection.
        wallet: Proxy wallet address.

    Returns:
        True if the wallet exists in tracked_wallets, False otherwise.
    """
    sql = "SELECT 1 FROM tracked_wallets WHERE proxy_wallet = %s"
    with conn.cursor() as cur:
        cur.execute(sql, (wallet,))
        return cur.fetchone() is not None


def upsert_single_wallet(
    conn: psycopg2.extensions.connection,
    wallet: str,
    user_name: str,
) -> None:
    """
    Register a single wallet in tracked_wallets. Used in copy-trade mode after
    genesis seeding so subsequent runs know the wallet is not new.

    Args:
        conn: Open psycopg2 connection.
        wallet: Proxy wallet address.
        user_name: Display name for the wallet.
    """
    sql = """
        INSERT INTO tracked_wallets (proxy_wallet, user_name)
        VALUES (%s, %s)
        ON CONFLICT (proxy_wallet) DO UPDATE SET user_name = EXCLUDED.user_name
    """
    with conn.cursor() as cur:
        cur.execute(sql, (wallet, user_name))
    conn.commit()
