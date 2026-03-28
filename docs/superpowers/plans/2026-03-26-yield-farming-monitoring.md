# Yield Farming Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add full lifecycle trade tracking, three-circuit-breaker risk guard, Telegram push alerts, and a separate live monitoring dashboard (port 5051) to the yield farming bot.

**Architecture:** DB-first design — `yield_trades` table is the single source of truth shared between the bot process and the dashboard process. The bot writes trade records and a heartbeat every cycle; the dashboard reads both. Risk guard, monitor service, and Telegram alerts are isolated services wired into the main yield cycle.

**Tech Stack:** Python, psycopg2, Flask, Chart.js (CDN), Polymarket Data API (positions/closed-positions endpoints), py_clob_client, python-dotenv.

---

## File Map

**New files:**
- `core/models/yield_trade_result.py` — `YieldTradeResult` dataclass (return type for execute_yield_trade)
- `service/risk_guard_service.py` — three circuit breakers, single `check_risk()` entry point
- `service/monitor_service.py` — lifecycle polling, daily summary scheduler
- `monitoring/__init__.py` — empty, makes it a package
- `monitoring/app.py` — Flask API on port 5051
- `monitoring/index.html` — dashboard UI
- `tests/test_risk_guard_service.py` — unit tests for risk guard
- `tests/test_yield_repository.py` — unit tests for yield repository functions

**Modified files:**
- `core/database/connection.py` — add `yield_trades` + `bot_heartbeat` table DDL
- `core/database/repository.py` — add all yield_trades and heartbeat CRUD functions
- `service/db_service.py` — expose yield_trades + heartbeat functions
- `service/copy_trade_service.py` — `execute_yield_trade` returns `YieldTradeResult` instead of `bool`
- `service/yield_farming_service.py` — write yield_trades rows + call risk guard
- `service/telegram_service.py` — add yield-specific alert functions
- `main.py` — wire heartbeat, risk guard, monitor_service into yield cycle; fetch session_start_balance at startup

---

## Task 1: DB Schema — yield_trades + bot_heartbeat tables

**Files:**
- Modify: `core/database/connection.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_yield_repository.py`:

```python
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
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/test_yield_repository.py::test_schema_contains_yield_trades -v
```
Expected: `FAILED` — `assert "yield_trades" in combined`

- [ ] **Step 3: Add SQL to connection.py**

In `core/database/connection.py`, after `_CREATE_TRACKED_WALLETS`, add:

```python
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
```

Then update `_ALL_TABLES`:

```python
_ALL_TABLES = [
    _CREATE_LEADERBOARD_SNAPSHOTS,
    _CREATE_TRADER_TRADES,
    _CREATE_TRACKED_WALLETS,
    _CREATE_YIELD_TRADES,
    _CREATE_BOT_HEARTBEAT,
]
```

Also update the log message in `init_schema()`:

```python
logger.info("Database schema initialised (tables: leaderboard_snapshots, trader_trades, tracked_wallets, yield_trades, bot_heartbeat).")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_yield_repository.py::test_schema_contains_yield_trades tests/test_yield_repository.py::test_schema_contains_bot_heartbeat -v
```
Expected: both `PASSED`

- [ ] **Step 5: Commit**

```bash
git add core/database/connection.py tests/test_yield_repository.py
git commit -m "feat: add yield_trades and bot_heartbeat tables to schema"
```

---

## Task 2: Repository Functions for yield_trades and bot_heartbeat

**Files:**
- Modify: `core/database/repository.py`
- Modify: `tests/test_yield_repository.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_yield_repository.py`:

```python
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
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_yield_repository.py -v
```
Expected: `FAILED` — `ImportError` or `AttributeError` on missing functions

- [ ] **Step 3: Add repository functions**

Append to `core/database/repository.py`:

```python
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
             shares, cost_usd, clob_order_id, status, session_balance_start, balance_before)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            token_id, condition_id, title, outcome, signal_price, fill_price,
            shares, cost_usd, clob_order_id, status, session_balance_start, balance_before,
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
    allowed = {"status", "fill_price", "resolved_at", "settled_at", "pnl_usd"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [trade_id]
    sql = f"UPDATE yield_trades SET {set_clause} WHERE id = %s"
    with conn.cursor() as cur:
        cur.execute(sql, values)
    conn.commit()


def get_open_yield_trades(conn: psycopg2.extensions.connection) -> list[dict]:
    """
    Return all yield_trades rows with status 'submitted' or 'filled'.
    Used by monitor_service to advance lifecycle states.

    Args:
        conn: Open psycopg2 connection.

    Returns:
        List of dicts with keys: id, token_id, condition_id, title, outcome,
        signal_price, fill_price, shares, cost_usd, status, resolved_at,
        settled_at, pnl_usd, session_balance_start, balance_before.
    """
    sql = """
        SELECT id, token_id, condition_id, title, outcome, signal_price, fill_price,
               shares, cost_usd, status, resolved_at, settled_at, pnl_usd,
               session_balance_start, balance_before
        FROM yield_trades
        WHERE status IN ('submitted', 'filled')
        ORDER BY submitted_at ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in rows]


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
        ORDER BY submitted_at DESC
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
            DATE(submitted_at AT TIME ZONE 'UTC') AS day,
            SUM(pnl_usd) OVER (ORDER BY DATE(submitted_at AT TIME ZONE 'UTC')) AS cumulative_pnl
        FROM yield_trades
        WHERE pnl_usd IS NOT NULL
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
        # Serialize datetimes to ISO strings for JSON
        for dt_col in ("submitted_at", "resolved_at", "settled_at"):
            if d.get(dt_col) is not None:
                d[dt_col] = d[dt_col].isoformat()
        result.append(d)
    return result


def upsert_bot_heartbeat(
    conn: psycopg2.extensions.connection,
    mode: str,
    session_start_balance: float,
    current_balance: float,
) -> None:
    """
    Insert or update the single bot_heartbeat row (id=1).
    Called every cycle by the bot to signal liveness to the dashboard.

    Args:
        conn: Open psycopg2 connection.
        mode: Bot mode string (e.g. 'yield-farming').
        session_start_balance: USDC balance when the bot session started.
        current_balance: Current USDC balance.
    """
    sql = """
        INSERT INTO bot_heartbeat (id, mode, last_seen, session_start_balance, current_balance)
        VALUES (1, %s, NOW(), %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            mode = EXCLUDED.mode,
            last_seen = NOW(),
            session_start_balance = EXCLUDED.session_start_balance,
            current_balance = EXCLUDED.current_balance
    """
    with conn.cursor() as cur:
        cur.execute(sql, (mode, session_start_balance, current_balance))
    conn.commit()


def get_bot_heartbeat(conn: psycopg2.extensions.connection) -> dict | None:
    """
    Return the bot_heartbeat row, or None if the bot has never run.

    Returns:
        Dict with keys: mode, last_seen (ISO str), session_start_balance, current_balance.
        None if no heartbeat row exists.
    """
    sql = "SELECT mode, last_seen, session_start_balance, current_balance FROM bot_heartbeat WHERE id = 1"
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
    }
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_yield_repository.py -v
```
Expected: all 7 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add core/database/repository.py tests/test_yield_repository.py
git commit -m "feat: add yield_trades and bot_heartbeat repository functions"
```

---

## Task 3: db_service — yield_trades and heartbeat interface

**Files:**
- Modify: `service/db_service.py`

- [ ] **Step 1: Append functions to db_service.py**

```python
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
```

- [ ] **Step 2: Verify import**

```bash
python -c "from service import db_service; print('db_service OK')"
```
Expected: `db_service OK`

- [ ] **Step 3: Commit**

```bash
git add service/db_service.py
git commit -m "feat: expose yield_trades and heartbeat functions in db_service"
```

---

## Task 4: YieldTradeResult dataclass + execute_yield_trade return type

**Files:**
- Create: `core/models/yield_trade_result.py`
- Modify: `service/copy_trade_service.py`

- [ ] **Step 1: Create YieldTradeResult model**

Create `core/models/yield_trade_result.py`:

```python
"""
yield_trade_result.py
Return type for execute_yield_trade(). Pure data — no logic.
"""
from dataclasses import dataclass


@dataclass
class YieldTradeResult:
    """
    Outcome of a single yield trade execution attempt.

    Attributes:
        success: True if the CLOB accepted the order.
        order_id: CLOB orderID string if successful, None otherwise.
        fill_price: Actual price used for the order, None if blocked before submission.
        shares: Number of shares ordered, None if blocked before sizing.
        cost_usd: shares × fill_price, None if blocked before sizing.
        balance_before: USDC balance just before this trade was placed.
    """
    success: bool
    order_id: str | None
    fill_price: float | None
    shares: int | None
    cost_usd: float | None
    balance_before: float | None
```

- [ ] **Step 2: Update execute_yield_trade to return YieldTradeResult**

In `service/copy_trade_service.py`, add the import at the top:

```python
from core.models.yield_trade_result import YieldTradeResult
```

Then replace every `return False` and `return True` in `execute_yield_trade` with `YieldTradeResult` instances. The full updated function body (replace everything after the `logger.info("=== PREPARING YIELD TRADE ===")` line):

```python
    if not is_in_spain():
        logger.error("Execution blocked: Geo location is not Spain (ES).")
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)

    if not token_id:
        logger.error("Execution blocked: No token_id provided.")
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)

    if signal_price <= 0.0 or signal_price > 1.0:
        logger.error("Execution blocked: Invalid signal price %.6f", signal_price)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)

    try:
        pk = os.getenv("poly_private_key", "").strip(" '\"")
        client = _get_client()

        current_price = _get_current_market_price(client, token_id, "BUY")
        if current_price is None:
            logger.error("Execution blocked: Order book empty or market closed: %s", title[:60])
            return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)

        slippage_pct = abs(current_price - signal_price) / signal_price * 100
        if slippage_pct > _MAX_SLIPPAGE_PCT:
            logger.warning(
                "Skipping: slippage %.1f%% exceeds %.0f%% threshold "
                "(signal $%.3f → current $%.3f): %s",
                slippage_pct, _MAX_SLIPPAGE_PCT, signal_price, current_price, title[:60],
            )
            return YieldTradeResult(success=False, order_id=None, fill_price=current_price, shares=None, cost_usd=None, balance_before=None)

        logger.info("Current market price: $%.4f  (signal: $%.4f, slippage: %.1f%%)", current_price, signal_price, slippage_pct)

        if current_price >= 0.99 or current_price <= 0.01:
            logger.warning("Skipping: current price %.4f is outside CLOB range (0.01–0.99): %s", current_price, title[:60])
            return YieldTradeResult(success=False, order_id=None, fill_price=current_price, shares=None, cost_usd=None, balance_before=None)

        balance = _get_usdc_balance(pk)
        budget_usd = max(_CLOB_MIN_NOTIONAL_USD, balance * budget_fraction)
        min_market_shares = _get_min_order_size(client, condition_id)
        min_shares_for_notional = math.ceil(_CLOB_MIN_NOTIONAL_USD / current_price)
        target_shares = math.floor(budget_usd / current_price)
        order_size = max(min_market_shares, min_shares_for_notional, target_shares)
        order_cost = order_size * current_price

        if balance < order_cost:
            logger.error(
                "Execution blocked: Insufficient USDC. Need $%.2f (%d shares × $%.3f), have $%.2f",
                order_cost, order_size, current_price, balance,
            )
            return YieldTradeResult(success=False, order_id=None, fill_price=current_price, shares=order_size, cost_usd=order_cost, balance_before=balance)

        logger.info("Balance: $%.2f | budget: $%.2f | %d shares @ $%.4f = $%.2f", balance, budget_usd, order_size, current_price, order_cost)

        args = OrderArgs(
            token_id=token_id,
            price=round(current_price, 2),
            size=float(order_size),
            side=BUY,
        )
        logger.info("Submitting yield BUY: %d shares @ $%.4f (~$%.2f)", order_size, args.price, args.price * args.size)

        signed_order = client.create_order(args)
        resp = client.post_order(signed_order)

        if resp.get("success"):
            order_id = resp.get("orderID")
            logger.info("YIELD TRADE SUBMITTED! OrderID=%s status=%s", order_id, resp.get("status"))
            return YieldTradeResult(success=True, order_id=order_id, fill_price=current_price, shares=order_size, cost_usd=order_cost, balance_before=balance)
        else:
            logger.error("YIELD TRADE REJECTED: %s", resp)
            return YieldTradeResult(success=False, order_id=None, fill_price=current_price, shares=order_size, cost_usd=order_cost, balance_before=balance)

    except PolyApiException as e:
        if e.status_code == 404:
            logger.warning("CLOB market not found (404) — market already closed: %s", title[:60])
        else:
            logger.error("CLOB API error (status=%s): %s", e.status_code, e)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)
    except (ValueError, KeyError) as e:
        logger.error("Invalid yield trade parameters: %s", e)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)
    except requests.RequestException as e:
        logger.error("Network error during yield trade: %s", e)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)
    except Exception as e:
        logger.error("Unexpected error during yield trade: %s", e)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)
```

- [ ] **Step 3: Verify import**

```bash
python -c "from service.copy_trade_service import execute_yield_trade; from core.models.yield_trade_result import YieldTradeResult; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add core/models/yield_trade_result.py service/copy_trade_service.py
git commit -m "feat: execute_yield_trade returns YieldTradeResult with order_id, shares, cost"
```

---

## Task 5: risk_guard_service

**Files:**
- Create: `service/risk_guard_service.py`
- Create: `tests/test_risk_guard_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_risk_guard_service.py`:

```python
"""Unit tests for risk_guard_service. Mocks db_service — no real DB required."""
import pytest
from unittest.mock import patch


def test_allows_when_all_checks_pass():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=[]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=49.0, session_start_balance=50.0)
    assert result.allowed is True
    assert result.reason is None


def test_blocks_on_balance_floor():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=[]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=4.0, session_start_balance=50.0)
    assert result.allowed is False
    assert "balance floor" in result.reason.lower()


def test_blocks_on_drawdown():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=[]):
        from service.risk_guard_service import check_risk
        # 14% drawdown > 10% threshold
        result = check_risk(current_balance=43.0, session_start_balance=50.0)
    assert result.allowed is False
    assert "drawdown" in result.reason.lower()


def test_blocks_on_consecutive_losses():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=["lost", "lost", "lost"]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=47.0, session_start_balance=50.0)
    assert result.allowed is False
    assert "consecutive" in result.reason.lower()


def test_allows_when_not_enough_losses_yet():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=["lost", "lost"]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=48.0, session_start_balance=50.0)
    assert result.allowed is True


def test_allows_when_losses_interrupted_by_win():
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=["lost", "won", "lost"]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=47.0, session_start_balance=50.0)
    assert result.allowed is True


def test_balance_floor_checked_before_drawdown():
    # Balance floor ($4 < $5 floor) should fire even though drawdown is only 2%
    with patch("service.db_service.get_recent_yield_trade_statuses", return_value=[]):
        from service.risk_guard_service import check_risk
        result = check_risk(current_balance=4.0, session_start_balance=4.08)
    assert result.allowed is False
    assert "balance floor" in result.reason.lower()
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_risk_guard_service.py -v
```
Expected: `FAILED` — `ModuleNotFoundError: No module named 'service.risk_guard_service'`

- [ ] **Step 3: Create service/risk_guard_service.py**

```python
"""
risk_guard_service.py
Three independent circuit breakers that must all pass before a yield trade is executed.
Pure decision layer — no API calls, no mutations.

Circuit breakers (evaluated in order, first failure wins):
  1. Balance floor — stop if USDC < YIELD_BALANCE_FLOOR
  2. Session drawdown — stop if loss from session start > YIELD_MAX_DRAWDOWN_PCT %
  3. Consecutive losses — stop if last N resolved trades are all 'lost'

Configure via .env:
  YIELD_BALANCE_FLOOR=5
  YIELD_MAX_CONSECUTIVE_LOSSES=3
  YIELD_MAX_DRAWDOWN_PCT=10
"""

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_BALANCE_FLOOR = float(os.getenv("YIELD_BALANCE_FLOOR", "5"))
_MAX_CONSECUTIVE_LOSSES = int(os.getenv("YIELD_MAX_CONSECUTIVE_LOSSES", "3"))
_MAX_DRAWDOWN_PCT = float(os.getenv("YIELD_MAX_DRAWDOWN_PCT", "10"))


@dataclass
class RiskStatus:
    """
    Result of a risk check.

    Attributes:
        allowed: True if trading is permitted, False if halted.
        reason: Human-readable explanation when allowed=False, None otherwise.
    """
    allowed: bool
    reason: str | None


def check_risk(current_balance: float, session_start_balance: float) -> RiskStatus:
    """
    Run all three circuit breakers. Returns on the first failure.

    Args:
        current_balance: Current USDC balance.
        session_start_balance: USDC balance when the bot session started.

    Returns:
        RiskStatus with allowed=True if all checks pass, or allowed=False
        with a human-readable reason string identifying the triggered breaker.
    """
    # 1. Balance floor
    if current_balance < _BALANCE_FLOOR:
        reason = f"Balance floor hit: ${current_balance:.2f} < ${_BALANCE_FLOOR:.2f} minimum"
        logger.warning("Risk guard BLOCKED: %s", reason)
        return RiskStatus(allowed=False, reason=reason)

    # 2. Session drawdown
    if session_start_balance > 0:
        drawdown_pct = (session_start_balance - current_balance) / session_start_balance * 100
        if drawdown_pct > _MAX_DRAWDOWN_PCT:
            reason = (
                f"Drawdown limit hit: {drawdown_pct:.1f}% > {_MAX_DRAWDOWN_PCT:.0f}% "
                f"(${session_start_balance:.2f} → ${current_balance:.2f})"
            )
            logger.warning("Risk guard BLOCKED: %s", reason)
            return RiskStatus(allowed=False, reason=reason)

    # 3. Consecutive losses (DB read — read-only, no mutations)
    from service import db_service
    recent = db_service.get_recent_yield_trade_statuses(limit=_MAX_CONSECUTIVE_LOSSES)
    if len(recent) >= _MAX_CONSECUTIVE_LOSSES and all(s == "lost" for s in recent):
        reason = f"{_MAX_CONSECUTIVE_LOSSES} consecutive losses — manual review required"
        logger.warning("Risk guard BLOCKED: %s", reason)
        return RiskStatus(allowed=False, reason=reason)

    logger.debug(
        "Risk guard OK: balance=$%.2f, drawdown=%.1f%%, recent=%s",
        current_balance,
        (session_start_balance - current_balance) / session_start_balance * 100 if session_start_balance > 0 else 0,
        recent,
    )
    return RiskStatus(allowed=True, reason=None)


def get_risk_dashboard_state(current_balance: float, session_start_balance: float) -> dict:
    """
    Return per-breaker state for the monitoring dashboard /api/risk endpoint.

    Returns:
        Dict with keys: balance_floor, drawdown, consecutive_losses.
        Each value is a dict with: current, threshold, triggered (bool), label (str).
    """
    from service import db_service
    recent = db_service.get_recent_yield_trade_statuses(limit=_MAX_CONSECUTIVE_LOSSES)
    consecutive_loss_count = sum(1 for s in recent if s == "lost") if all(s == "lost" for s in recent) else 0
    drawdown_pct = (
        (session_start_balance - current_balance) / session_start_balance * 100
        if session_start_balance > 0 else 0.0
    )

    return {
        "balance_floor": {
            "current": round(current_balance, 2),
            "threshold": _BALANCE_FLOOR,
            "triggered": current_balance < _BALANCE_FLOOR,
            "label": f"${current_balance:.2f} / ${_BALANCE_FLOOR:.2f} floor",
        },
        "drawdown": {
            "current": round(drawdown_pct, 2),
            "threshold": _MAX_DRAWDOWN_PCT,
            "triggered": drawdown_pct > _MAX_DRAWDOWN_PCT,
            "label": f"{drawdown_pct:.1f}% / {_MAX_DRAWDOWN_PCT:.0f}% max",
        },
        "consecutive_losses": {
            "current": consecutive_loss_count,
            "threshold": _MAX_CONSECUTIVE_LOSSES,
            "triggered": consecutive_loss_count >= _MAX_CONSECUTIVE_LOSSES,
            "label": f"{consecutive_loss_count} / {_MAX_CONSECUTIVE_LOSSES} max",
        },
    }
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_risk_guard_service.py -v
```
Expected: all 7 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add service/risk_guard_service.py tests/test_risk_guard_service.py
git commit -m "feat: add risk_guard_service with three circuit breakers"
```

---

## Task 6: yield_farming_service — write to DB + accept session_balance_start

**Files:**
- Modify: `service/yield_farming_service.py`

- [ ] **Step 1: Update imports at top of yield_farming_service.py**

Replace the existing imports block with:

```python
import json
import logging
from datetime import datetime, timedelta, timezone

import requests

from core.models.yield_opportunity import YieldOpportunity
from service.copy_trade_service import execute_yield_trade
from service import db_service
from utility.constants import REQUEST_TIMEOUT_SECONDS
from utility.endpoints import GAMMA_MARKETS, CLOB_MARKETS

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Update run_yield_farming_cycle signature and body**

Replace the entire `run_yield_farming_cycle` function:

```python
def run_yield_farming_cycle(
    threshold: float = _DEFAULT_THRESHOLD,
    window_minutes: int = _DEFAULT_WINDOW_MINUTES,
    budget_fraction: float = 0.01,
    dry_run: bool = False,
    session_balance_start: float = 0.0,
) -> int:
    """
    One full yield farming cycle: scan → filter → execute → record.

    Args:
        threshold: Minimum price to act on (e.g. 0.95).
        window_minutes: Look-ahead window for closing markets.
        budget_fraction: Fraction of USDC balance to spend per trade (default 1%).
        dry_run: If True, log what would be traded but submit no orders and write no DB rows.
        session_balance_start: USDC balance when the bot session started (written to each trade row).

    Returns:
        Number of orders successfully submitted (or would-be submitted in dry_run) this cycle.
    """
    opportunities = scan_opportunities(threshold=threshold, window_minutes=window_minutes)

    if not opportunities:
        logger.info("Yield cycle: no qualifying opportunities found.")
        return 0

    submitted = 0
    for opp in opportunities[:_MAX_TRADES_PER_CYCLE]:
        mins_until_close = (opp.close_time - datetime.now(timezone.utc)).total_seconds() / 60
        logger.info(
            "%sOpportunity: %s | %s @ $%.4f | closes in %.1f min",
            "[DRY-RUN] " if dry_run else "",
            opp.title[:55], opp.outcome, opp.price, mins_until_close,
        )

        if dry_run:
            logger.info(
                "[DRY-RUN] Would execute: token=%s... condition=%s...",
                opp.token_id[:20], opp.condition_id[:20],
            )
            submitted += 1
            continue

        result = execute_yield_trade(
            token_id=opp.token_id,
            condition_id=opp.condition_id,
            title=opp.title,
            signal_price=opp.price,
            budget_fraction=budget_fraction,
        )

        # Record every attempt (success or failure) to the DB for monitoring
        status = "submitted" if result.success else "error"
        try:
            db_service.insert_yield_trade(
                token_id=opp.token_id,
                condition_id=opp.condition_id,
                title=opp.title,
                outcome=opp.outcome,
                signal_price=opp.price,
                fill_price=result.fill_price,
                shares=result.shares,
                cost_usd=result.cost_usd,
                clob_order_id=result.order_id,
                status=status,
                session_balance_start=session_balance_start,
                balance_before=result.balance_before or 0.0,
            )
        except Exception as e:
            logger.error("Failed to write yield trade to DB: %s", e)

        if result.success:
            _executed_token_ids.add(opp.token_id)
            submitted += 1
            logger.info("Yield trade submitted: %s (%s)", opp.title[:55], opp.outcome)
        else:
            logger.warning("Yield trade failed: %s (%s)", opp.title[:55], opp.outcome)

    logger.info(
        "Yield cycle complete%s: %d/%d trade(s) submitted.",
        " [DRY-RUN]" if dry_run else "",
        submitted,
        len(opportunities[:_MAX_TRADES_PER_CYCLE]),
    )
    return submitted
```

- [ ] **Step 3: Verify import**

```bash
python -c "from service.yield_farming_service import run_yield_farming_cycle; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add service/yield_farming_service.py
git commit -m "feat: yield_farming_service writes yield_trades rows to DB"
```

---

## Task 7: Telegram yield alert functions

**Files:**
- Modify: `service/telegram_service.py`

- [ ] **Step 1: Append alert functions to telegram_service.py**

```python
def send_yield_trade_submitted(title: str, outcome: str, fill_price: float, shares: int, cost_usd: float, balance_after: float) -> bool:
    """Alert when a yield trade order is accepted by the CLOB."""
    text = (
        f"🟢 <b>YIELD TRADE SUBMITTED</b>\n"
        f"\n"
        f"📋 <b>Market:</b> {title}\n"
        f"🎯 <b>Outcome:</b> {outcome}\n"
        f"💵 <b>Fill price:</b> ${fill_price:.4f} ({fill_price * 100:.1f}% prob)\n"
        f"📦 <b>Shares:</b> {shares}\n"
        f"💰 <b>Cost:</b> ${cost_usd:.2f}\n"
        f"🏦 <b>Balance after:</b> ${balance_after:.2f}"
    )
    return send_message(text)


def send_yield_trade_won(title: str, outcome: str, pnl_usd: float, session_net_pnl: float, win_rate: float) -> bool:
    """Alert when a yield trade resolves as a win."""
    text = (
        f"✅ <b>YIELD TRADE WON</b>\n"
        f"\n"
        f"📋 <b>Market:</b> {title}\n"
        f"🎯 <b>Outcome:</b> {outcome}\n"
        f"💸 <b>P&L:</b> +${pnl_usd:.4f}\n"
        f"📊 <b>Session net P&L:</b> ${session_net_pnl:+.2f}\n"
        f"🏆 <b>Win rate:</b> {win_rate * 100:.1f}%"
    )
    return send_message(text)


def send_yield_trade_lost(title: str, outcome: str, loss_usd: float, session_net_pnl: float, win_rate: float) -> bool:
    """Alert when a yield trade resolves as a loss."""
    text = (
        f"❌ <b>YIELD TRADE LOST</b>\n"
        f"\n"
        f"📋 <b>Market:</b> {title}\n"
        f"🎯 <b>Outcome:</b> {outcome}\n"
        f"💸 <b>Loss:</b> -${abs(loss_usd):.4f}\n"
        f"📊 <b>Session net P&L:</b> ${session_net_pnl:+.2f}\n"
        f"📉 <b>Win rate:</b> {win_rate * 100:.1f}%"
    )
    return send_message(text)


def send_risk_guard_blocked(reason: str) -> bool:
    """Alert when a circuit breaker halts trading."""
    text = (
        f"🛑 <b>TRADING HALTED — RISK GUARD</b>\n"
        f"\n"
        f"⚠️ <b>Reason:</b> {reason}\n"
        f"\n"
        f"Bot will keep scanning but will not execute trades until conditions improve."
    )
    return send_message(text)


def send_balance_warning(current_balance: float, floor: float) -> bool:
    """Alert when balance drops below 2× the floor threshold."""
    text = (
        f"⚠️ <b>LOW BALANCE WARNING</b>\n"
        f"\n"
        f"🏦 <b>Current balance:</b> ${current_balance:.2f}\n"
        f"🛑 <b>Floor (halt threshold):</b> ${floor:.2f}\n"
        f"📉 Balance is below 2× floor — approaching trading halt."
    )
    return send_message(text)


def send_yield_daily_summary(
    total_trades: int,
    won: int,
    lost: int,
    win_rate: float,
    net_pnl: float,
    current_balance: float,
) -> bool:
    """Send the daily summary at 23:00 UTC."""
    icon = "📈" if net_pnl >= 0 else "📉"
    text = (
        f"{icon} <b>Yield Farming — Daily Summary</b>\n"
        f"\n"
        f"📊 <b>Trades today:</b> {total_trades} | Won: {won} | Lost: {lost}\n"
        f"🏆 <b>Win rate:</b> {win_rate * 100:.1f}%\n"
        f"💸 <b>Net P&L:</b> ${net_pnl:+.2f}\n"
        f"🏦 <b>Current balance:</b> ${current_balance:.2f}"
    )
    return send_message(text)


def send_yield_error(context: str, error: str) -> bool:
    """Alert on unexpected errors in the yield farming cycle."""
    text = (
        f"🔴 <b>YIELD FARMING ERROR</b>\n"
        f"\n"
        f"📍 <b>Context:</b> {context}\n"
        f"⚠️ <b>Error:</b> {error[:300]}"
    )
    return send_message(text)
```

- [ ] **Step 2: Verify import**

```bash
python -c "from service.telegram_service import send_yield_trade_won, send_risk_guard_blocked; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add service/telegram_service.py
git commit -m "feat: add yield farming Telegram alert functions"
```

---

## Task 8: monitor_service — lifecycle polling + daily summary

**Files:**
- Create: `service/monitor_service.py`

- [ ] **Step 1: Create service/monitor_service.py**

```python
"""
monitor_service.py
Polls the Polymarket positions API to advance yield_trades lifecycle statuses
and fires Telegram alerts on state transitions.

Called once per yield farming cycle (after execution). Also handles the daily
summary at 23:00 UTC.

Lifecycle: submitted → filled → won | lost → settled_at set after 30min
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from service import db_service, telegram_service
from utility.constants import REQUEST_TIMEOUT_SECONDS
from utility.endpoints import POSITIONS, CLOSED_POSITIONS

load_dotenv()
logger = logging.getLogger(__name__)

_OUR_WALLET = os.getenv("poly_funder_address", "").strip(" '\"")
_STUCK_HOURS = 24          # flag trades unresolved after this many hours
_SETTLE_DELAY_MINUTES = 30 # mark settled_at this long after resolved_at
_BALANCE_WARNING_MULTIPLIER = 2.0  # warn when balance < N × floor

# Track last daily summary date to avoid double-sending
_last_daily_summary_date: str | None = None


def _fetch_open_positions() -> list[dict]:
    """Fetch all current open positions for our wallet."""
    try:
        resp = requests.get(POSITIONS, params={"user": _OUR_WALLET, "limit": 500}, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Monitor: could not fetch open positions: %s", e)
        return []


def _fetch_closed_positions() -> list[dict]:
    """Fetch recent closed positions for our wallet (last 500)."""
    try:
        resp = requests.get(CLOSED_POSITIONS, params={"user": _OUR_WALLET, "limit": 500}, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Monitor: could not fetch closed positions: %s", e)
        return []


def poll_lifecycle() -> None:
    """
    Advance statuses for all open yield_trades rows.
    Fires Telegram alerts on each transition.
    Called once per cycle.
    """
    if not _OUR_WALLET:
        logger.warning("Monitor: poly_funder_address not set — skipping lifecycle poll.")
        return

    open_trades = db_service.get_open_yield_trades()
    if not open_trades:
        return

    now_utc = datetime.now(timezone.utc)
    open_positions = _fetch_open_positions()

    # Build lookup: (conditionId, outcome) → position dict for open positions
    open_pos_lookup: dict[tuple[str, str], dict] = {}
    for pos in open_positions:
        key = (pos.get("conditionId", ""), pos.get("outcome", ""))
        open_pos_lookup[key] = pos

    # We only fetch closed positions once if any trade needs resolution check
    closed_positions: list[dict] = []
    closed_fetched = False

    for trade in open_trades:
        trade_id = trade["id"]
        condition_id = trade["condition_id"]
        outcome = trade["outcome"]
        key = (condition_id, outcome)

        # ── submitted → filled ────────────────────────────────────────────────
        if trade["status"] == "submitted" and key in open_pos_lookup:
            pos = open_pos_lookup[key]
            fill_price = float(pos.get("curPrice", trade["signal_price"] or 0))
            db_service.update_yield_trade(trade_id, status="filled", fill_price=fill_price)
            logger.info("Monitor: trade %d status → filled @ $%.4f (%s)", trade_id, fill_price, trade["title"][:50])
            continue

        # ── filled/submitted → won or lost ────────────────────────────────────
        # Only check if not in open positions (resolved) and past submitted time
        submitted_at = trade.get("submitted_at")
        if key not in open_pos_lookup:
            if not closed_fetched:
                closed_positions = _fetch_closed_positions()
                closed_fetched = True

            closed_lookup: dict[tuple[str, str], dict] = {
                (p.get("conditionId", ""), p.get("outcome", "")): p
                for p in closed_positions
            }

            if key in closed_lookup:
                closed = closed_lookup[key]
                realized_pnl = float(closed.get("realizedPnl", 0))
                cost = float(trade["cost_usd"] or 0)
                if realized_pnl > 0:
                    db_service.update_yield_trade(
                        trade_id,
                        status="won",
                        pnl_usd=realized_pnl,
                        resolved_at=now_utc,
                    )
                    logger.info("Monitor: trade %d WON — pnl=$%.4f (%s)", trade_id, realized_pnl, trade["title"][:50])
                    summary = db_service.get_yield_pnl_summary()
                    telegram_service.send_yield_trade_won(
                        title=trade["title"],
                        outcome=outcome,
                        pnl_usd=realized_pnl,
                        session_net_pnl=summary["net_pnl"],
                        win_rate=summary["win_rate"],
                    )
                else:
                    db_service.update_yield_trade(
                        trade_id,
                        status="lost",
                        pnl_usd=-cost,
                        resolved_at=now_utc,
                    )
                    logger.info("Monitor: trade %d LOST — cost=$%.4f (%s)", trade_id, cost, trade["title"][:50])
                    summary = db_service.get_yield_pnl_summary()
                    telegram_service.send_yield_trade_lost(
                        title=trade["title"],
                        outcome=outcome,
                        loss_usd=cost,
                        session_net_pnl=summary["net_pnl"],
                        win_rate=summary["win_rate"],
                    )

            # Flag trades stuck > 24h with no resolution
            elif submitted_at:
                try:
                    submitted_dt = datetime.fromisoformat(str(submitted_at)).replace(tzinfo=timezone.utc) if not hasattr(submitted_at, 'tzinfo') else submitted_at
                    if (now_utc - submitted_dt).total_seconds() > _STUCK_HOURS * 3600:
                        db_service.update_yield_trade(trade_id, status="error")
                        logger.warning("Monitor: trade %d stuck >%dh — marking error", trade_id, _STUCK_HOURS)
                        telegram_service.send_yield_error(
                            context=f"Trade {trade_id} stuck >{_STUCK_HOURS}h",
                            error=f"Market: {trade['title'][:80]} | Outcome: {outcome}"
                        )
                except Exception as e:
                    logger.warning("Monitor: error parsing submitted_at for trade %d: %s", trade_id, e)

        # ── resolved → settled (30min delay) ─────────────────────────────────
        if trade.get("resolved_at") and not trade.get("settled_at"):
            try:
                resolved_dt = datetime.fromisoformat(str(trade["resolved_at"])).replace(tzinfo=timezone.utc) if not hasattr(trade["resolved_at"], 'tzinfo') else trade["resolved_at"]
                if (now_utc - resolved_dt).total_seconds() > _SETTLE_DELAY_MINUTES * 60:
                    db_service.update_yield_trade(trade_id, settled_at=now_utc)
                    logger.info("Monitor: trade %d marked settled", trade_id)
            except Exception as e:
                logger.warning("Monitor: error parsing resolved_at for trade %d: %s", trade_id, e)


def check_balance_warning(current_balance: float) -> None:
    """Fire a Telegram alert if balance is approaching the floor threshold."""
    from service.risk_guard_service import _BALANCE_FLOOR
    if current_balance < _BALANCE_FLOOR * _BALANCE_WARNING_MULTIPLIER:
        logger.warning("Monitor: balance $%.2f is below 2× floor ($%.2f)", current_balance, _BALANCE_FLOOR)
        telegram_service.send_balance_warning(current_balance=current_balance, floor=_BALANCE_FLOOR)


def send_daily_summary_if_due(current_balance: float) -> None:
    """
    Send the daily P&L summary if the current UTC hour is 23 and
    we have not sent one today yet.
    """
    global _last_daily_summary_date
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour != 23:
        return
    today_str = now_utc.strftime("%Y-%m-%d")
    if _last_daily_summary_date == today_str:
        return

    try:
        summary = db_service.get_yield_pnl_summary()
        telegram_service.send_yield_daily_summary(
            total_trades=summary["total_trades"],
            won=summary["won"],
            lost=summary["lost"],
            win_rate=summary["win_rate"],
            net_pnl=summary["net_pnl"],
            current_balance=current_balance,
        )
        _last_daily_summary_date = today_str
        logger.info("Monitor: daily summary sent for %s", today_str)
    except Exception as e:
        logger.error("Monitor: failed to send daily summary: %s", e)
```

- [ ] **Step 2: Verify import**

```bash
python -c "from service.monitor_service import poll_lifecycle, send_daily_summary_if_due; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add service/monitor_service.py
git commit -m "feat: add monitor_service for yield trade lifecycle polling and daily summary"
```

---

## Task 9: main.py — wire yield farming cycle together

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add imports at top of main.py**

After the existing service imports, add:

```python
from service.risk_guard_service import check_risk
from service.monitor_service import poll_lifecycle, check_balance_warning, send_daily_summary_if_due
from service.copy_trade_service import _get_usdc_balance
```

Also add to existing imports line:
```python
import os
```
(if not already present — check first)

- [ ] **Step 2: Fetch session_start_balance at startup in main()**

In `main()`, after `db_service.initialise_database()` succeeds, add:

```python
    # Fetch USDC balance once at session start — used for drawdown calculation
    pk = os.getenv("poly_private_key", "").strip(" '\"")
    session_start_balance = 0.0
    if args.yield_farming and pk:
        from service.copy_trade_service import _get_usdc_balance
        session_start_balance = _get_usdc_balance(pk)
        logger.info("Session start USDC balance: $%.2f", session_start_balance)
```

- [ ] **Step 3: Replace yield farming branch in the main while loop**

Replace:

```python
            if args.yield_farming:
                submitted = run_yield_farming_cycle(
                    threshold=args.threshold,
                    window_minutes=args.window,
                    dry_run=args.dry_run,
                )
                stats["cycles_completed"] += 1
                stats["last_cycle_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                stats["alerts_total"] += submitted
                stats["db_ok"] = True
```

With:

```python
            if args.yield_farming:
                # 1. Update bot heartbeat (liveness signal for dashboard)
                try:
                    from service.copy_trade_service import _get_usdc_balance
                    pk = os.getenv("poly_private_key", "").strip(" '\"")
                    current_balance = _get_usdc_balance(pk) if pk and not args.dry_run else session_start_balance
                    db_service.update_bot_heartbeat(
                        mode="yield-farming" + (" [dry-run]" if args.dry_run else ""),
                        session_start_balance=session_start_balance,
                        current_balance=current_balance,
                    )
                except Exception as e:
                    logger.warning("Could not update bot heartbeat: %s", e)
                    current_balance = session_start_balance

                # 2. Advance lifecycle of previous trades
                if not args.dry_run:
                    try:
                        poll_lifecycle()
                    except Exception as e:
                        logger.error("Monitor lifecycle poll failed: %s", e)

                # 3. Risk guard — check all three circuit breakers
                if not args.dry_run:
                    risk = check_risk(current_balance=current_balance, session_start_balance=session_start_balance)
                    if not risk.allowed:
                        from service.telegram_service import send_risk_guard_blocked
                        send_risk_guard_blocked(risk.reason)
                        logger.warning("Risk guard halted trading this cycle.")
                        stats["cycles_completed"] += 1
                        stats["last_cycle_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                        stats["db_ok"] = True
                        logger.info("Sleeping %ds until next cycle...", POLL_INTERVAL_SECONDS)
                        time.sleep(POLL_INTERVAL_SECONDS)
                        continue

                # 4. Balance warning check
                if not args.dry_run:
                    try:
                        check_balance_warning(current_balance)
                    except Exception as e:
                        logger.warning("Balance warning check failed: %s", e)

                # 5. Execute yield farming cycle
                submitted = run_yield_farming_cycle(
                    threshold=args.threshold,
                    window_minutes=args.window,
                    dry_run=args.dry_run,
                    session_balance_start=session_start_balance,
                )
                stats["cycles_completed"] += 1
                stats["last_cycle_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                stats["alerts_total"] += submitted
                stats["db_ok"] = True

                # 6. Daily summary
                if not args.dry_run:
                    try:
                        send_daily_summary_if_due(current_balance)
                    except Exception as e:
                        logger.warning("Daily summary failed: %s", e)
```

- [ ] **Step 4: Verify main.py imports cleanly**

```bash
python -c "import main; print('main.py OK')"
```
Expected: `main.py OK`

- [ ] **Step 5: Verify --help shows all flags**

```bash
python main.py --help
```
Expected: flags `--yield-farming`, `--threshold`, `--window`, `--dry-run` all visible

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat: wire risk guard, monitor, heartbeat into yield farming main loop"
```

---

## Task 10: monitoring/app.py — Flask API

**Files:**
- Create: `monitoring/__init__.py`
- Create: `monitoring/app.py`

- [ ] **Step 1: Create monitoring/__init__.py**

```python
```
(empty file)

- [ ] **Step 2: Create monitoring/app.py**

```python
"""
monitoring/app.py
Standalone Flask dashboard for yield farming monitoring.
Run separately from the main bot: python monitoring/app.py
Reads from the shared PostgreSQL database — no direct bot coupling.

Endpoints:
  GET /                    — dashboard HTML
  GET /api/status          — bot liveness + mode
  GET /api/balance         — USDC balance + drawdown
  GET /api/risk            — per-breaker circuit breaker state
  GET /api/pnl/summary     — aggregate P&L stats
  GET /api/pnl/chart       — daily cumulative P&L for chart
  GET /api/trades          — paginated yield_trades rows
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from utility.logger import init_logging
from service import db_service
from service.risk_guard_service import get_risk_dashboard_state, _BALANCE_FLOOR

init_logging(level="WARNING")

app = Flask(__name__, static_folder=os.path.dirname(os.path.abspath(__file__)))
CORS(app)

_BOT_ALIVE_THRESHOLD_SECONDS = 30


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def status():
    """Bot liveness, mode, last seen."""
    heartbeat = db_service.get_bot_heartbeat()
    if not heartbeat:
        return jsonify({"alive": False, "mode": None, "last_seen": None, "uptime_seconds": None})

    last_seen_str = heartbeat.get("last_seen")
    alive = False
    uptime_seconds = None
    if last_seen_str:
        try:
            last_seen_dt = datetime.fromisoformat(last_seen_str)
            if last_seen_dt.tzinfo is None:
                last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - last_seen_dt).total_seconds()
            alive = age < _BOT_ALIVE_THRESHOLD_SECONDS
        except Exception:
            pass

    return jsonify({
        "alive": alive,
        "mode": heartbeat.get("mode"),
        "last_seen": last_seen_str,
    })


@app.route("/api/balance")
def balance():
    """Current USDC balance, session start, and drawdown."""
    heartbeat = db_service.get_bot_heartbeat()
    if not heartbeat:
        return jsonify({"current_balance": None, "session_start_balance": None, "drawdown_pct": None, "floor_warning": False})

    current = heartbeat.get("current_balance") or 0.0
    start = heartbeat.get("session_start_balance") or 0.0
    drawdown_pct = ((start - current) / start * 100) if start > 0 else 0.0
    floor_warning = current < _BALANCE_FLOOR * 2

    return jsonify({
        "current_balance": current,
        "session_start_balance": start,
        "drawdown_pct": round(drawdown_pct, 2),
        "floor": _BALANCE_FLOOR,
        "floor_warning": floor_warning,
    })


@app.route("/api/risk")
def risk():
    """Per-breaker circuit breaker states."""
    heartbeat = db_service.get_bot_heartbeat()
    current = heartbeat.get("current_balance", 0.0) if heartbeat else 0.0
    start = heartbeat.get("session_start_balance", 0.0) if heartbeat else 0.0
    state = get_risk_dashboard_state(current_balance=current or 0.0, session_start_balance=start or 0.0)
    return jsonify(state)


@app.route("/api/pnl/summary")
def pnl_summary():
    """Aggregate P&L statistics."""
    try:
        summary = db_service.get_yield_pnl_summary()
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pnl/chart")
def pnl_chart():
    """Daily cumulative P&L data points."""
    try:
        chart = db_service.get_yield_pnl_chart()
        return jsonify(chart)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trades")
def trades():
    """Paginated yield_trades rows. Query params: status, limit, offset."""
    status_filter = request.args.get("status") or None
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    try:
        rows = db_service.get_yield_trades_page(status=status_filter, limit=limit, offset=offset)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("MONITOR_PORT", 5051))
    print(f"\n  Yield Farming Monitor → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
```

- [ ] **Step 3: Verify import**

```bash
python -c "import sys; sys.path.insert(0,'C:/Users/Nick/PycharmProjects/polymarket_robot'); import monitoring.app; print('monitoring/app.py OK')"
```
Expected: `monitoring/app.py OK`

- [ ] **Step 4: Commit**

```bash
git add monitoring/__init__.py monitoring/app.py
git commit -m "feat: add monitoring Flask API on port 5051"
```

---

## Task 11: monitoring/index.html — dashboard UI

**Files:**
- Create: `monitoring/index.html`

- [ ] **Step 1: Create monitoring/index.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Yield Farming Monitor</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh; padding: 24px; }
    h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 20px; color: #f8fafc; }
    .grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 16px; }
    .card { background: #1e2130; border-radius: 10px; padding: 18px; border: 1px solid #2d3348; }
    .card h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; margin-bottom: 12px; }
    .metric { font-size: 1.6rem; font-weight: 700; color: #f1f5f9; }
    .sub { font-size: 0.8rem; color: #64748b; margin-top: 4px; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
    .badge.ok { background: #14532d; color: #4ade80; }
    .badge.warn { background: #451a03; color: #fb923c; }
    .badge.err { background: #450a0a; color: #f87171; }
    .breaker { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #2d3348; font-size: 0.85rem; }
    .breaker:last-child { border-bottom: none; }
    .chart-card { background: #1e2130; border-radius: 10px; padding: 18px; border: 1px solid #2d3348; margin-bottom: 16px; }
    .chart-card h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: #94a3b8; margin-bottom: 12px; }
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    th { text-align: left; padding: 8px 10px; color: #64748b; font-weight: 500; border-bottom: 1px solid #2d3348; }
    td { padding: 8px 10px; border-bottom: 1px solid #1a1f2e; }
    tr:hover td { background: #252a3a; }
    .status-won { color: #4ade80; font-weight: 600; }
    .status-lost { color: #f87171; font-weight: 600; }
    .status-submitted, .status-filled { color: #60a5fa; }
    .status-error { color: #fb923c; }
    .pnl-pos { color: #4ade80; }
    .pnl-neg { color: #f87171; }
    .filter-bar { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
    .filter-btn { background: #2d3348; border: none; color: #94a3b8; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.8rem; }
    .filter-btn.active { background: #3b4fd9; color: #fff; }
    .alive-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
    .alive-dot.on { background: #4ade80; box-shadow: 0 0 6px #4ade80; }
    .alive-dot.off { background: #f87171; }
  </style>
</head>
<body>
  <h1>Yield Farming Monitor</h1>

  <!-- Status / Balance / Risk -->
  <div class="grid3">
    <div class="card" id="card-status">
      <h2>Bot Status</h2>
      <div class="metric" id="bot-mode">—</div>
      <div class="sub" id="bot-lastseen">Loading...</div>
    </div>
    <div class="card">
      <h2>USDC Balance</h2>
      <div class="metric" id="balance-current">—</div>
      <div class="sub" id="balance-info">Loading...</div>
    </div>
    <div class="card">
      <h2>Risk Guards</h2>
      <div id="risk-breakers">Loading...</div>
    </div>
  </div>

  <!-- P&L Summary + Chart -->
  <div class="chart-card">
    <h2>Session P&L — <span id="pnl-summary-line">Loading...</span></h2>
    <canvas id="pnl-chart" height="80"></canvas>
  </div>

  <!-- Trades table -->
  <div class="card">
    <h2>Recent Trades</h2>
    <div class="filter-bar">
      <button class="filter-btn active" onclick="setFilter(null, this)">All</button>
      <button class="filter-btn" onclick="setFilter('submitted', this)">Submitted</button>
      <button class="filter-btn" onclick="setFilter('filled', this)">Filled</button>
      <button class="filter-btn" onclick="setFilter('won', this)">Won</button>
      <button class="filter-btn" onclick="setFilter('lost', this)">Lost</button>
      <button class="filter-btn" onclick="setFilter('error', this)">Error</button>
    </div>
    <table>
      <thead>
        <tr><th>Time (UTC)</th><th>Market</th><th>Outcome</th><th>Price</th><th>Shares</th><th>Cost</th><th>Status</th><th>P&L</th></tr>
      </thead>
      <tbody id="trades-tbody"></tbody>
    </table>
  </div>

<script>
  let pnlChart = null;
  let currentFilter = null;

  function setFilter(status, btn) {
    currentFilter = status;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadTrades();
  }

  async function loadStatus() {
    const [s, b] = await Promise.all([
      fetch('/api/status').then(r => r.json()),
      fetch('/api/balance').then(r => r.json()),
    ]);

    const alive = s.alive;
    const dot = `<span class="alive-dot ${alive ? 'on' : 'off'}"></span>`;
    document.getElementById('bot-mode').innerHTML = dot + (s.mode || 'Unknown');
    document.getElementById('bot-lastseen').textContent = s.last_seen
      ? 'Last seen: ' + new Date(s.last_seen).toLocaleTimeString()
      : 'Never seen';

    document.getElementById('balance-current').textContent =
      b.current_balance != null ? `$${b.current_balance.toFixed(2)}` : '—';
    const dd = b.drawdown_pct != null ? `${b.drawdown_pct.toFixed(1)}%` : '—';
    const floorWarn = b.floor_warning ? ' ⚠️ near floor' : '';
    document.getElementById('balance-info').textContent =
      `Start: $${(b.session_start_balance || 0).toFixed(2)} | Drawdown: ${dd}${floorWarn}`;
  }

  async function loadRisk() {
    const r = await fetch('/api/risk').then(res => res.json());
    const html = Object.entries(r).map(([key, v]) => {
      const cls = v.triggered ? 'err' : 'ok';
      const icon = v.triggered ? '🛑' : '✅';
      const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      return `<div class="breaker"><span>${icon} ${label}</span><span class="badge ${cls}">${v.label}</span></div>`;
    }).join('');
    document.getElementById('risk-breakers').innerHTML = html;
  }

  async function loadPnl() {
    const [summary, chart] = await Promise.all([
      fetch('/api/pnl/summary').then(r => r.json()),
      fetch('/api/pnl/chart').then(r => r.json()),
    ]);

    const wr = ((summary.win_rate || 0) * 100).toFixed(1);
    const netSign = summary.net_pnl >= 0 ? '+' : '';
    document.getElementById('pnl-summary-line').textContent =
      `Net: ${netSign}$${(summary.net_pnl || 0).toFixed(2)} | Won: ${summary.won} Lost: ${summary.lost} | Rate: ${wr}%`;

    const labels = chart.map(d => d.date);
    const data = chart.map(d => d.cumulative_pnl);

    if (pnlChart) {
      pnlChart.data.labels = labels;
      pnlChart.data.datasets[0].data = data;
      pnlChart.update();
    } else {
      const ctx = document.getElementById('pnl-chart').getContext('2d');
      pnlChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'Cumulative P&L ($)',
            data,
            borderColor: data.length && data[data.length-1] >= 0 ? '#4ade80' : '#f87171',
            backgroundColor: 'rgba(74,222,128,0.08)',
            tension: 0.3,
            fill: true,
            pointRadius: 3,
          }]
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: '#64748b' }, grid: { color: '#1e2130' } },
            y: { ticks: { color: '#64748b' }, grid: { color: '#2d3348' } },
          }
        }
      });
    }
  }

  async function loadTrades() {
    const url = '/api/trades?limit=50' + (currentFilter ? '&status=' + currentFilter : '');
    const trades = await fetch(url).then(r => r.json());
    const tbody = document.getElementById('trades-tbody');
    if (!trades.length) { tbody.innerHTML = '<tr><td colspan="8" style="color:#64748b;text-align:center;padding:20px">No trades</td></tr>'; return; }
    tbody.innerHTML = trades.map(t => {
      const time = t.submitted_at ? new Date(t.submitted_at).toLocaleTimeString() : '—';
      const title = (t.title || '').slice(0, 40);
      const price = t.fill_price != null ? `$${parseFloat(t.fill_price).toFixed(3)}` : (t.signal_price ? `~$${parseFloat(t.signal_price).toFixed(3)}` : '—');
      const shares = t.shares != null ? t.shares : '—';
      const cost = t.cost_usd != null ? `$${parseFloat(t.cost_usd).toFixed(2)}` : '—';
      const statusCls = `status-${t.status}`;
      const pnl = t.pnl_usd != null
        ? `<span class="${parseFloat(t.pnl_usd) >= 0 ? 'pnl-pos' : 'pnl-neg'}">${parseFloat(t.pnl_usd) >= 0 ? '+' : ''}$${parseFloat(t.pnl_usd).toFixed(4)}</span>`
        : '—';
      return `<tr><td>${time}</td><td title="${t.title}">${title}</td><td>${t.outcome || '—'}</td><td>${price}</td><td>${shares}</td><td>${cost}</td><td class="${statusCls}">${t.status}</td><td>${pnl}</td></tr>`;
    }).join('');
  }

  async function refresh() {
    await Promise.all([loadStatus(), loadRisk(), loadPnl(), loadTrades()]);
  }

  refresh();
  setInterval(refresh, 10000);
</script>
</body>
</html>
```

- [ ] **Step 2: Test dashboard locally**

```bash
cd "C:/Users/Nick/PycharmProjects/polymarket_robot"
python monitoring/app.py
```

Open `http://localhost:5051` in browser. Verify:
- Page loads without JS errors (check browser console)
- Status panel shows "Never seen" (no heartbeat yet)
- Balance shows `—`
- Risk guards show all green (empty DB, no trades)
- Trades table shows "No trades"

- [ ] **Step 3: Commit**

```bash
git add monitoring/index.html
git commit -m "feat: add yield farming monitoring dashboard at port 5051"
```

---

## Task 12: Environment variables + end-to-end dry-run verification

**Files:**
- Modify: `.env` (local) and `systemd.env` (VPS — edit manually)

- [ ] **Step 1: Add YIELD_* variables to .env**

Add these lines to `.env`:

```
YIELD_BALANCE_FLOOR = "5"
YIELD_MAX_CONSECUTIVE_LOSSES = "3"
YIELD_MAX_DRAWDOWN_PCT = "10"
MONITOR_PORT = "5051"
```

- [ ] **Step 2: Run full dry-run to verify end-to-end pipeline**

```bash
python main.py --yield-farming --dry-run --threshold 0.90 --window 10
```

Expected log output (in order):
1. `Session start USDC balance: $X.XX`
2. `Initialising database...`
3. `Database schema initialised (tables: ... yield_trades, bot_heartbeat)`
4. `Yield scan: N market(s) polled → M opportunit(y/ies)`
5. `[DRY-RUN] Would execute: token=...`
6. `Yield cycle complete [DRY-RUN]: M/M trade(s) submitted`
7. `Sleeping 5s until next cycle...`

Stop with Ctrl+C after one cycle.

- [ ] **Step 3: Add YIELD_* to systemd.env on VPS (manual step)**

SSH into VPS and add to `/home/nick/polymarket_bot/systemd.env`:
```
YIELD_BALANCE_FLOOR=5
YIELD_MAX_CONSECUTIVE_LOSSES=3
YIELD_MAX_DRAWDOWN_PCT=10
MONITOR_PORT=5051
```

- [ ] **Step 4: Commit**

```bash
git add .env
git commit -m "chore: add YIELD_* env vars for risk guard configuration"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| yield_trades table | Task 1, 2, 3 |
| bot_heartbeat table | Task 1, 2, 3 |
| Risk guard — balance floor | Task 5 |
| Risk guard — consecutive losses | Task 5 |
| Risk guard — session drawdown | Task 5 |
| YIELD_* env vars | Task 12 |
| execute_yield_trade returns order_id, shares, cost | Task 4 |
| yield_farming_service writes to DB | Task 6 |
| Telegram: submitted, won, lost, blocked, warning, daily, error | Task 7 |
| Monitor lifecycle poll: submitted→filled→won/lost→settled | Task 8 |
| Monitor stuck >24h detection | Task 8 |
| Monitor daily summary at 23:00 UTC | Task 8 |
| main.py: heartbeat, risk guard, monitor wired in | Task 9 |
| monitoring/app.py — 6 API endpoints | Task 10 |
| monitoring/index.html — status, balance, risk, chart, trades | Task 11 |
| Separate process port 5051 | Task 10, 11 |
| Phase 1 parameters ($5 floor, 3 losses, 10% drawdown) | Task 12 |
