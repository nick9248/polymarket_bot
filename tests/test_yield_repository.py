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
