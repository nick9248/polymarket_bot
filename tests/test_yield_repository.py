"""Tests for yield_trades repository functions — uses mock connections."""
import pytest
from unittest.mock import MagicMock, call


def _make_conn(fetchone=None, fetchall=None):
    """Helper: build a mock psycopg2 connection."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall or []
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cur


def test_schema_contains_yield_trades():
    from core.database.connection import _ALL_TABLES
    combined = " ".join(_ALL_TABLES).lower()
    assert "yield_trades" in combined


def test_schema_contains_bot_heartbeat():
    from core.database.connection import _ALL_TABLES
    combined = " ".join(_ALL_TABLES).lower()
    assert "bot_heartbeat" in combined


def test_insert_yield_trade_returns_id():
    from core.database import repository
    conn, cur = _make_conn(fetchone=(42,))
    result = repository.insert_yield_trade(
        conn,
        token_id="tok123",
        condition_id="cond456",
        title="Will X win?",
        outcome="Yes",
        signal_price=0.95,
        fill_price=0.96,
        shares=2,
        cost_usd=1.92,
        clob_order_id="ord-001",
        status="submitted",
        session_balance_start=50.0,
        balance_before=50.0,
    )
    assert result == 42


def test_update_yield_trade_executes_query():
    from core.database import repository
    conn, cur = _make_conn()
    repository.update_yield_trade(conn, trade_id=1, status="won", pnl_usd=0.08)
    assert cur.execute.called


def test_get_recent_yield_trade_statuses_returns_list():
    from core.database import repository
    conn, cur = _make_conn(fetchall=[("won",), ("lost",), ("won",)])
    result = repository.get_recent_yield_trade_statuses(conn, limit=3)
    assert result == ["won", "lost", "won"]


def test_get_open_yield_trades_returns_dicts():
    from core.database import repository
    conn, cur = _make_conn(fetchall=[
        (1, "tok1", "cond1", "Market A", "Yes", 0.95, 0.96, 2, 1.92, "submitted", None, None, None, 50.0, 50.0)
    ])
    cur.description = [
        ("id",), ("token_id",), ("condition_id",), ("title",), ("outcome",),
        ("signal_price",), ("fill_price",), ("shares",), ("cost_usd",), ("status",),
        ("resolved_at",), ("settled_at",), ("pnl_usd",), ("session_balance_start",), ("balance_before",),
    ]
    result = repository.get_open_yield_trades(conn)
    assert len(result) == 1
    assert result[0]["token_id"] == "tok1"
    assert result[0]["status"] == "submitted"


def test_upsert_bot_heartbeat_executes():
    from core.database import repository
    conn, cur = _make_conn()
    repository.upsert_bot_heartbeat(conn, mode="yield-farming", session_start_balance=50.0, current_balance=49.5)
    assert cur.execute.called
