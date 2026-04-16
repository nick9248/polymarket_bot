"""
repository.py
All SQL queries in one place. Receives an open connection — does not manage connections.
Business logic lives in db_service.py. This layer is pure SQL I/O.
"""

import logging
from datetime import datetime, timezone

import psycopg2.extensions
from psycopg2 import sql as psql

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


def insert_yield_trade(
    conn: psycopg2.extensions.connection,
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
    gamma_clob_spread: float | None = None,
    minutes_to_close: float | None = None,
    btc_dvol: float | None = None,
    btc_iv_percentile: float | None = None,
) -> int:
    """
    Insert a new yield trade record. Returns the new row id.

    Args:
        conn: Open psycopg2 connection.

    Returns:
        The id of the newly inserted row.
    """
    sql = """
        INSERT INTO yield_trades
            (token_id, condition_id, title, outcome, signal_price, fill_price,
             shares, cost_usd, clob_order_id, status, session_balance_start, balance_before,
             gamma_clob_spread, minutes_to_close, btc_dvol, btc_iv_percentile)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            token_id, condition_id, title, outcome, signal_price, fill_price,
            shares, cost_usd, clob_order_id, status, session_balance_start, balance_before,
            gamma_clob_spread, minutes_to_close, btc_dvol, btc_iv_percentile,
        ))
        row = cur.fetchone()
    conn.commit()
    return row[0]


def update_yield_trade(
    conn: psycopg2.extensions.connection,
    trade_id: int,
    **fields,
) -> None:
    """
    Update one or more columns on a yield_trades row by id.

    Accepted keyword fields: status, fill_price, resolved_at, settled_at, pnl_usd.

    Args:
        conn: Open psycopg2 connection.
        trade_id: Row id to update.
        **fields: Column name → new value pairs.
    """
    allowed = {"status", "fill_price", "resolved_at", "settled_at", "pnl_usd", "stop_loss_exit_price", "stop_loss_at"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_parts = [psql.SQL("{} = %s").format(psql.Identifier(k)) for k in updates]
    set_clause = psql.SQL(", ").join(set_parts)
    query = psql.SQL("UPDATE yield_trades SET {} WHERE id = %s").format(set_clause)
    values = list(updates.values()) + [trade_id]
    with conn.cursor() as cur:
        cur.execute(query, values)
    conn.commit()


def get_open_yield_trades(conn: psycopg2.extensions.connection) -> list[dict]:
    """
    Return all yield_trades rows with status 'submitted' or 'filled'.
    Used by monitor_service to advance lifecycle states.

    Args:
        conn: Open psycopg2 connection.

    Returns:
        List of dicts with keys: id, token_id, condition_id, title, outcome,
        signal_price, fill_price, shares, cost_usd, status, submitted_at,
        resolved_at, settled_at, pnl_usd, session_balance_start, balance_before.
    """
    sql = """
        SELECT id, token_id, condition_id, title, outcome, signal_price, fill_price,
               shares, cost_usd, status, submitted_at, resolved_at, settled_at, pnl_usd,
               session_balance_start, balance_before, minutes_to_close
        FROM yield_trades
        WHERE status IN ('submitted', 'filled')
        ORDER BY submitted_at ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in rows]


def get_session_realized_losses(
    conn: psycopg2.extensions.connection,
    session_start_time,
) -> float:
    """
    Return total cost_usd of confirmed 'lost' yield trades since session_start_time.

    Used by risk_guard_service to measure drawdown from real losses only —
    ignoring in-flight positions that have temporarily reduced the CLOB balance.

    Args:
        conn: Open psycopg2 connection.
        session_start_time: Datetime marking the start of the current session.

    Returns:
        Sum of cost_usd for all lost trades since session_start_time (0.0 if none).
    """
    sql = """
        SELECT COALESCE(SUM(cost_usd), 0)
        FROM yield_trades
        WHERE status = 'lost'
        AND submitted_at >= %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (session_start_time,))
        return float(cur.fetchone()[0])


def get_recent_yield_trade_statuses(
    conn: psycopg2.extensions.connection,
    limit: int = 3,
) -> list[str]:
    """
    Return the status strings of the most recent N yield_trades rows (resolved only).
    Used by risk_guard_service to detect consecutive losses.

    Args:
        conn: Open psycopg2 connection.
        limit: How many recent rows to return.

    Returns:
        List of status strings, most recent first (e.g. ['lost', 'lost', 'won']).
    """
    sql = """
        SELECT status FROM yield_trades
        WHERE status IN ('won', 'lost')
        ORDER BY resolved_at DESC
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
    return [row[0] for row in rows]


def get_yield_pnl_summary(conn: psycopg2.extensions.connection) -> dict:
    """
    Return aggregate P&L stats across all yield_trades.

    Returns:
        Dict with keys: total_trades, won, lost, pending, win_rate,
        gross_pnl, total_cost, net_pnl.
    """
    sql = """
        SELECT
            COUNT(*)                                          AS total_trades,
            COUNT(*) FILTER (WHERE status = 'won')           AS won,
            COUNT(*) FILTER (WHERE status = 'lost')          AS lost,
            COUNT(*) FILTER (WHERE status IN ('submitted','filled')) AS pending,
            COALESCE(SUM(pnl_usd) FILTER (WHERE pnl_usd > 0), 0) AS gross_pnl,
            COALESCE(SUM(cost_usd), 0)                       AS total_cost,
            COALESCE(SUM(pnl_usd), 0)                        AS net_pnl
        FROM yield_trades
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    total, won, lost, pending, gross_pnl, total_cost, net_pnl = row
    win_rate = (won / (won + lost)) if (won + lost) > 0 else 0.0
    return {
        "total_trades": total,
        "won": won,
        "lost": lost,
        "pending": pending,
        "win_rate": round(float(win_rate), 4),
        "gross_pnl": round(float(gross_pnl), 4),
        "total_cost": round(float(total_cost), 4),
        "net_pnl": round(float(net_pnl), 4),
    }


def get_yield_pnl_chart(conn: psycopg2.extensions.connection) -> list[dict]:
    """
    Return daily cumulative P&L data points for charting.

    Returns:
        List of dicts with keys: date (str YYYY-MM-DD), cumulative_pnl (float).
    """
    sql = """
        SELECT
            day,
            SUM(daily_pnl) OVER (ORDER BY day) AS cumulative_pnl
        FROM (
            SELECT
                DATE(submitted_at AT TIME ZONE 'UTC') AS day,
                SUM(pnl_usd) AS daily_pnl
            FROM yield_trades
            WHERE pnl_usd IS NOT NULL
            GROUP BY 1
        ) sub
        ORDER BY day
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return [{"date": str(row[0]), "cumulative_pnl": round(float(row[1]), 4)} for row in rows]


def get_yield_trades_page(
    conn: psycopg2.extensions.connection,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Return a paginated list of yield_trades rows, newest first.

    Args:
        conn: Open psycopg2 connection.
        status: Optional status filter (e.g. 'won', 'lost', 'submitted').
        limit: Page size.
        offset: Row offset for pagination.

    Returns:
        List of dicts with all yield_trades columns plus submitted_at as ISO string.
    """
    if status:
        sql = """
            SELECT id, token_id, condition_id, title, outcome, signal_price, fill_price,
                   shares, cost_usd, status, clob_order_id, submitted_at, resolved_at,
                   settled_at, pnl_usd, session_balance_start, balance_before
            FROM yield_trades
            WHERE status = %s
            ORDER BY submitted_at DESC
            LIMIT %s OFFSET %s
        """
        params = (status, limit, offset)
    else:
        sql = """
            SELECT id, token_id, condition_id, title, outcome, signal_price, fill_price,
                   shares, cost_usd, status, clob_order_id, submitted_at, resolved_at,
                   settled_at, pnl_usd, session_balance_start, balance_before
            FROM yield_trades
            ORDER BY submitted_at DESC
            LIMIT %s OFFSET %s
        """
        params = (limit, offset)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

    result = []
    for row in rows:
        d = dict(zip(cols, row))
        for dt_col in ("submitted_at", "resolved_at", "settled_at"):
            if d.get(dt_col) is not None:
                d[dt_col] = d[dt_col].isoformat()
        result.append(d)
    return result


def get_yield_trades_for_analytics(conn: psycopg2.extensions.connection) -> list[dict]:
    """Return all yield_trades rows needed for analytics (minimal columns, oldest first)."""
    sql = """
        SELECT title, outcome, status, submitted_at, pnl_usd, cost_usd, fill_price
        FROM yield_trades
        ORDER BY submitted_at ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    result = []
    for row in rows:
        d = dict(zip(cols, row))
        if d.get("submitted_at") is not None:
            d["submitted_at"] = d["submitted_at"].isoformat()
        result.append(d)
    return result


def upsert_bot_heartbeat(
    conn: psycopg2.extensions.connection,
    mode: str,
    session_start_balance: float,
    current_balance: float,
    session_start_time=None,
) -> None:
    """
    Insert or update the single bot_heartbeat row (id=1).
    Called every cycle by the bot to signal liveness to the dashboard.
    Does NOT overwrite session_start_time or reset_requested — those are
    only mutated by reset_session_start / acknowledge_reset.
    """
    sql = """
        INSERT INTO bot_heartbeat (id, mode, last_seen, session_start_balance, current_balance, session_start_time)
        VALUES (1, %s, NOW(), %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            mode = EXCLUDED.mode,
            last_seen = NOW(),
            session_start_balance = EXCLUDED.session_start_balance,
            current_balance = EXCLUDED.current_balance,
            session_start_time = COALESCE(bot_heartbeat.session_start_time, EXCLUDED.session_start_time)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (mode, session_start_balance, current_balance, session_start_time))
    conn.commit()


def reset_session_start(
    conn: psycopg2.extensions.connection,
    new_balance: float,
    new_time,
) -> None:
    """
    Overwrite session_start_balance and session_start_time in bot_heartbeat.
    Used by manual /reset_risk command and midnight auto-reset.
    Also clears reset_requested flag.
    """
    sql = """
        UPDATE bot_heartbeat
        SET session_start_balance = %s,
            session_start_time    = %s,
            reset_requested       = FALSE
        WHERE id = 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (new_balance, new_time))
    conn.commit()


def request_risk_reset(conn: psycopg2.extensions.connection) -> None:
    """Set reset_requested=TRUE in bot_heartbeat. Consumed by the main bot loop."""
    with conn.cursor() as cur:
        cur.execute("UPDATE bot_heartbeat SET reset_requested = TRUE WHERE id = 1")
    conn.commit()


def get_bot_heartbeat(conn: psycopg2.extensions.connection) -> dict | None:
    """
    Return the bot_heartbeat row, or None if the bot has never run.

    Returns:
        Dict with keys: mode, last_seen (ISO str), session_start_balance,
        current_balance, session_start_time (ISO str), reset_requested (bool).
        None if no heartbeat row exists.
    """
    sql = """
        SELECT mode, last_seen, session_start_balance, current_balance,
               session_start_time, reset_requested
        FROM bot_heartbeat WHERE id = 1
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if not row:
        return None
    return {
        "mode": row[0],
        "last_seen": row[1].isoformat() if row[1] else None,
        "session_start_balance": float(row[2]) if row[2] else None,
        "current_balance": float(row[3]) if row[3] else None,
        "session_start_time": row[4].isoformat() if row[4] else None,
        "reset_requested": bool(row[5]) if row[5] is not None else False,
    }


def get_db_health(conn: psycopg2.extensions.connection) -> dict:
    """
    Return DB health metrics: stuck trade counts and recent error rate.
    Used by the monitoring dashboard health check.
    """
    with conn.cursor() as cur:
        # Stuck trades: submitted/filled older than 4 hours
        cur.execute("""
            SELECT COUNT(*) FROM yield_trades
            WHERE status IN ('submitted', 'filled')
            AND submitted_at < NOW() - INTERVAL '4 hours'
        """)
        stuck_count = cur.fetchone()[0]

        # Error rate over last 50 resolved trades
        cur.execute("""
            SELECT status FROM yield_trades
            WHERE status IN ('won', 'lost', 'error', 'expired')
            ORDER BY submitted_at DESC LIMIT 50
        """)
        recent = [r[0] for r in cur.fetchall()]
        error_count = sum(1 for s in recent if s == 'error')
        error_rate = round(error_count / len(recent) * 100, 1) if recent else 0.0

        # Total pending (submitted/filled within normal window)
        cur.execute("SELECT COUNT(*) FROM yield_trades WHERE status IN ('submitted', 'filled')")
        total_pending = cur.fetchone()[0]

    return {
        "stuck_trades": stuck_count,
        "total_pending": total_pending,
        "error_rate_pct": error_rate,
        "recent_sample_size": len(recent),
    }
