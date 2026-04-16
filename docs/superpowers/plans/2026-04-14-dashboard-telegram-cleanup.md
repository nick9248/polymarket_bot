# Dashboard Enhancement, Telegram Menu & Code Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the 3-page monitoring dashboard with live guards/positions/streak panels, add a Telegram persistent keyboard with 3 new commands, clean up code against CLAUDE.md standards, and update all documentation.

**Architecture:** Backend-first (DB → API → frontend). New DB columns on `bot_heartbeat`, new repository queries, 5 new Flask endpoints, then HTML/JS additions that poll those endpoints every 5s. Telegram keyboard sent at startup; new commands wired in `main.py` dispatch loop.

**Tech Stack:** Python, psycopg2, Flask, vanilla JS/HTML, Telegram Bot API (sendMessage with reply_markup, setMyCommands)

---

## File Map

| File | Change |
|---|---|
| `core/database/connection.py` | Add migration for 2 new bot_heartbeat columns |
| `core/database/repository.py` | Add 7 new query functions |
| `service/db_service.py` | Add wrappers for new repository functions |
| `service/yield_farming_service.py` | Write last_rv_value + rv_blocked_since to heartbeat each cycle |
| `service/telegram_service.py` | Add keyboard support, register_commands(), 3 new send_* formatters |
| `main.py` | Call register_commands() + send_keyboard() at startup; add /balance, /summary, /trades handlers |
| `monitoring/app.py` | Fix import style; add 5 new endpoints |
| `monitoring/index.html` | Add 4 new panels + auto-refresh |
| `monitoring/analytics.html` | Add 2 new sections + auto-refresh |
| `monitoring/health.html` | Add RV guard health check; rebalance scores |
| `documentation/error_analysis.md` | Add patterns 005–008 |
| `documentation/fix_versions.md` | Update to current state |
| `documentation/optimization_ideas.md` | Update to reflect implemented items |
| `tests/test_yield_repository.py` | Add tests for new repository functions |

---

## Task 1: Code Cleaning & CLAUDE.md Alignment

**Files:** `monitoring/app.py`, `service/yield_farming_service.py`, `main.py`

- [ ] **Step 1: Fix `monitoring/app.py` — move deferred imports to top**

  Currently `from datetime import timedelta` appears inside three function bodies. Move to top-level imports. Confirm the file already has `from datetime import datetime, timezone` at the top and just add `timedelta` there.

  At the top of `monitoring/app.py`, change:
  ```python
  from datetime import datetime, timezone
  ```
  to:
  ```python
  from datetime import datetime, timedelta, timezone
  ```
  Then remove the three inline `from datetime import timedelta` lines inside the `balance()`, `risk()`, and `health_check_api()` functions.

- [ ] **Step 2: Verify architecture — main.py has no direct DB/API calls**

  Open `main.py`. Search for any direct `psycopg2`, `requests.get/post`, or `repository.` calls outside of service imports. There should be none — all calls go through `service/`. If found, move them to the appropriate service. (Current expectation: none found; this is a verification step.)

- [ ] **Step 3: Verify yield_farming_service.py layer discipline**

  Open `service/yield_farming_service.py`. Confirm:
  - No direct `psycopg2` imports (uses db_service)
  - No direct `requests` calls for Polymarket APIs (uses utility/endpoints + timed_get)
  - `_SHORT_WINDOW_RE` and all constants are module-level (not inside functions)

- [ ] **Step 4: Commit cleanup**

  ```bash
  git add monitoring/app.py
  git commit -m "refactor: move deferred imports to module level in monitoring/app.py"
  ```

---

## Task 2: Documentation Updates

**Files:** `documentation/error_analysis.md`, `documentation/fix_versions.md`, `documentation/optimization_ideas.md`

- [ ] **Step 1: Add Pattern 005 to error_analysis.md**

  Append after Pattern 004:

  ```markdown
  ---

  ## Pattern 005 — Stop-loss CLOB Collateral Failure

  **Status:** Fixed (2026-04-13)

  ### Observation

  Stop-loss SELL orders failed with HTTP 400 "not enough balance" even when the
  proxy wallet held sufficient USDC. The pre-check using `get_balance_allowance()`
  always passed because it returns the total proxy wallet balance (~$120), not the
  CLOB's internal collateral accounting for open positions.

  ### Root Cause

  `client.get_balance_allowance()` with `signature_type=1` (POLY_PROXY) returns
  the total proxy wallet USDC balance. It does NOT reflect how much the CLOB has
  already earmarked as collateral for open positions. A $0.50/share ask requires
  `shares × $1.00` USDC in CLOB-internal collateral regardless of sell price.
  The pre-check `max_sellable = int(balance / 1.0)` was always ≥ 5, so the cap
  never reduced shares. The SELL then failed at the API level.

  ### Fix Applied

  Removed the pre-check entirely. Replaced with a retry-on-failure loop inside
  `copy_trade_service.execute_stop_loss_sell()`:
  1. Attempt full `shares` count
  2. If CLOB returns 400 "not enough balance", reduce by 1 and retry
  3. Repeat until success or shares == 0

  Confirmed working: trade 10753 sold 4 of 5 shares at $0.46, recovering $2.81 vs
  a $4.85 full loss.

  ---

  ## Pattern 006 — Inflated P&L from Polymarket `cashPnl` Field

  **Status:** Fixed (2026-04-13)

  ### Observation

  Trades 10727 and 10728 showed P&L of +$4.94 and +$4.98 respectively — roughly
  equal to the cost_usd (what we paid). These were winning trades entered close to
  resolution; the correct P&L was ~$0.06 and ~$0.20.

  ### Root Cause

  Polymarket's positions API `cashPnl` field calculates profit relative to the
  market's `initialValue` at inception, NOT relative to our fill price. For markets
  where we entered at $0.988 (very close to resolution), Polymarket's formula
  returned the entire redemption value ($4.94) as "profit" rather than the actual
  margin above our cost.

  ### Fix Applied

  Removed all use of `cashPnl` from `monitor_service.poll_lifecycle()`. Won-trade
  P&L now calculated as: `shares × $1.00 − cost_usd` using our own DB data.
  Affected DB records corrected manually.

  ---

  ## Pattern 007 — Hourly Market Entry Timing Risk (Option B)

  **Status:** Fixed (2026-04-13)

  ### Observation

  Hourly markets (e.g. "XRP Up or Down — 5PM ET") entered 8–15 min before close
  had a 3× higher loss rate than short-window markets (e.g. "4:15PM–4:30PM ET").
  Historical analysis of 4 April 9 losses showed the flip point was often 5–15 min
  before close — well within our holding window for hourly entries.

  ### Root Cause

  Hourly markets have a larger direction-change window. A trade entered at 10 min
  to close on a 60-min market is entered during the last 17% of the market's life,
  where terminal noise is highest. Short-window markets (15 min total) entered at
  the same 10 min point are only 2/3 through — very different risk profiles.

  ### Fix Applied

  Market-type-aware timing guard (Option B) added to `yield_farming_service.py`:
  - Short-window markets (title matches `\d+:\d+[AP]M-\d+:\d+[AP]M`): unchanged
  - Hourly markets: only enter if ≤3 min remaining (`_MAX_MINS_HOURLY = 3.0`)

  Confirmed working in live trading: XRP 10PM ET entered at 1.11 min, XRP 1:15AM
  entered at 2.38 min. Both won.

  ---

  ## Pattern 008 — Irreducible Binary-Flip Losses

  **Status:** Acknowledged — no fix possible

  ### Observation

  Trade 10672 (XRP Up 4AM ET): signal price $0.959, curPrice $0.96–$0.97 at 1 min
  to close. Market then resolved as Down — price flipped from ~$0.96 to $0.00 at
  the exact resolution timestamp with no detectable warning.

  ### Root Cause

  Some Polymarket markets stay near-certain pricing (96–99%) right up until the
  final resolution event, then flip in a single atomic step. The CLOB curPrice does
  not degrade gradually — the market simply resolves the opposite way. No polling
  interval (even 1s) could detect this in time to exit.

  ### Data

  From the historical dataset (982 Up-direction trades with t3 entry):
  - All 3 historical losses had curPrice > 0.98 at t=1 min (irreducible type)
  - Stop-loss threshold sweep: no threshold between $0.05 and $0.95 catches any
    historical loss — all resolve via binary flip, not gradual decline

  ### Decision

  **ACCEPTED as structural risk.** The yield farming strategy takes a small
  directional risk per trade (~1–3%). Option B (hourly guard) and the direction
  filter reduce exposure significantly, but this loss type cannot be eliminated.
  Expected frequency: ~0.3% of Up-direction trades. EV impact is acceptable given
  the overall win rate.

  *Last updated: 2026-04-14*
  ```

- [ ] **Step 2: Update fix_versions.md**

  Open `documentation/fix_versions.md`. Add entries for all fixes since the last recorded date:
  - Stop-loss retry loop (Pattern 005)
  - cashPnl fix (Pattern 006)
  - Option B hourly guard (Pattern 007)
  - Stop-loss threshold lowered from 2.0 to 1.0 min
  - Direction filter (Up only) via YIELD_DIRECTION_FILTER env var
  - RV guard via YIELD_MAX_REALIZED_VOL env var

- [ ] **Step 3: Update optimization_ideas.md**

  Mark implemented items as done. Verify anything listed as "pending" is still relevant.

- [ ] **Step 4: Commit docs**

  ```bash
  git add documentation/
  git commit -m "docs: add error patterns 005-008, update fix_versions and optimization_ideas"
  ```

---

## Task 3: DB Schema — New bot_heartbeat Columns

**Files:** `core/database/connection.py`, `tests/test_yield_repository.py`

- [ ] **Step 1: Write failing schema test**

  Add to `tests/test_yield_repository.py`:
  ```python
  def test_schema_contains_rv_columns():
      from core.database.connection import _MIGRATE_BOT_HEARTBEAT_RV
      assert "last_rv_value" in _MIGRATE_BOT_HEARTBEAT_RV
      assert "rv_blocked_since" in _MIGRATE_BOT_HEARTBEAT_RV
  ```

- [ ] **Step 2: Run test — confirm it fails**

  ```bash
  pytest tests/test_yield_repository.py::test_schema_contains_rv_columns -v
  ```
  Expected: `ImportError` or `AttributeError` — `_MIGRATE_BOT_HEARTBEAT_RV` does not exist yet.

- [ ] **Step 3: Add migration to connection.py**

  In `core/database/connection.py`, add after `_MIGRATE_YIELD_TRADES_STOP_LOSS`:
  ```python
  # Migrate bot_heartbeat to add RV guard tracking columns.
  _MIGRATE_BOT_HEARTBEAT_RV = """
  ALTER TABLE bot_heartbeat ADD COLUMN IF NOT EXISTS last_rv_value NUMERIC(6,4);
  ALTER TABLE bot_heartbeat ADD COLUMN IF NOT EXISTS rv_blocked_since TIMESTAMPTZ;
  """
  ```

  Then find the `init_schema()` function (or wherever migrations are run) and add `_MIGRATE_BOT_HEARTBEAT_RV` to the migration list alongside the other `_MIGRATE_*` statements.

- [ ] **Step 4: Run test — confirm it passes**

  ```bash
  pytest tests/test_yield_repository.py::test_schema_contains_rv_columns -v
  ```
  Expected: PASS

- [ ] **Step 5: Apply migration on VPS**

  ```bash
  ssh root@spain-vpn "su -c \"psql -d polymarket_bot -c 'ALTER TABLE bot_heartbeat ADD COLUMN IF NOT EXISTS last_rv_value NUMERIC(6,4); ALTER TABLE bot_heartbeat ADD COLUMN IF NOT EXISTS rv_blocked_since TIMESTAMPTZ;'\" postgres"
  ```
  Expected: `ALTER TABLE` (twice)

- [ ] **Step 6: Commit**

  ```bash
  git add core/database/connection.py tests/test_yield_repository.py
  git commit -m "feat: add last_rv_value and rv_blocked_since columns to bot_heartbeat"
  ```

---

## Task 4: New Repository Queries + db_service Wrappers

**Files:** `core/database/repository.py`, `service/db_service.py`, `tests/test_yield_repository.py`

- [ ] **Step 1: Write failing tests for all new queries**

  Add to `tests/test_yield_repository.py`:

  ```python
  def test_get_win_streak_all_wins():
      from core.database import repository
      conn, cur = _make_conn(fetchall=[
          {"status": "won"}, {"status": "won"}, {"status": "won"},
      ])
      result = repository.get_win_streak(conn)
      assert result["current"] == 3
      assert result["best"] >= 3

  def test_get_win_streak_broken_streak():
      from core.database import repository
      conn, cur = _make_conn(fetchall=[
          {"status": "won"}, {"status": "lost"}, {"status": "won"}, {"status": "won"},
      ])
      result = repository.get_win_streak(conn)
      # Most recent is won, streak=1; best streak is 2
      assert result["current"] == 1
      assert result["best"] == 2

  def test_get_stop_loss_savings_none():
      from core.database import repository
      conn, cur = _make_conn(fetchone=(None,))
      result = repository.get_stop_loss_savings(conn)
      assert result == {"total_saved": 0.0, "trigger_count": 0}

  def test_get_stop_loss_savings_with_data():
      from core.database import repository
      # fetchall for COUNT and SUM in one query — returns one row
      conn, cur = _make_conn(fetchone=(1, 3.88))
      result = repository.get_stop_loss_savings(conn)
      assert result["trigger_count"] == 1
      assert abs(result["total_saved"] - 3.88) < 0.01

  def test_get_active_trades_empty():
      from core.database import repository
      conn, cur = _make_conn(fetchall=[])
      result = repository.get_active_trades(conn)
      assert result == []
  ```

- [ ] **Step 2: Run tests — confirm they fail**

  ```bash
  pytest tests/test_yield_repository.py::test_get_win_streak_all_wins tests/test_yield_repository.py::test_get_stop_loss_savings_none tests/test_yield_repository.py::test_get_active_trades_empty -v
  ```
  Expected: all FAIL with `AttributeError`

- [ ] **Step 3: Add query functions to repository.py**

  Add at the end of `core/database/repository.py`:

  ```python
  def get_win_streak(conn) -> dict:
      """
      Return current consecutive win streak and best-ever streak from resolved trades.
      Counts from most recent trade backwards. Statuses counted: won, lost, stopped.
      """
      with conn.cursor() as cur:
          cur.execute("""
              SELECT status FROM yield_trades
              WHERE status IN ('won', 'lost', 'stopped')
              ORDER BY id DESC
          """)
          rows = cur.fetchall()

      if not rows:
          return {"current": 0, "best": 0}

      # rows is a list of dicts or tuples depending on cursor factory
      # repository uses plain tuples — access by index, or dict if RealDictCursor
      statuses = [r["status"] if isinstance(r, dict) else r[0] for r in rows]

      current = 0
      for s in statuses:
          if s == "won":
              current += 1
          else:
              break

      best = 0
      run = 0
      for s in reversed(statuses):  # chronological order
          if s == "won":
              run += 1
              best = max(best, run)
          else:
              run = 0

      return {"current": current, "best": best}


  def get_stop_loss_savings(conn) -> dict:
      """
      Return total USD saved by stop-loss exits and number of triggers.
      Saved = cost_usd - abs(pnl_usd) for each stopped trade.
      """
      with conn.cursor() as cur:
          cur.execute("""
              SELECT COUNT(*), SUM(cost_usd - ABS(pnl_usd))
              FROM yield_trades
              WHERE status = 'stopped'
          """)
          row = cur.fetchone()

      count = int(row[0] or 0)
      saved = float(row[1] or 0.0)
      return {"trigger_count": count, "total_saved": round(saved, 2)}


  def get_active_trades(conn) -> list:
      """Return all trades currently in submitted or filled status."""
      with conn.cursor() as cur:
          cur.execute("""
              SELECT id, title, outcome, status, fill_price, cost_usd,
                     submitted_at, minutes_to_close
              FROM yield_trades
              WHERE status IN ('submitted', 'filled')
              ORDER BY submitted_at DESC
          """)
          rows = cur.fetchall()

      cols = ["id", "title", "outcome", "status", "fill_price", "cost_usd",
              "submitted_at", "minutes_to_close"]
      return [dict(zip(cols, r)) for r in rows]


  def get_market_type_stats(conn) -> dict:
      """
      Return win/loss/pnl breakdown for short-window vs hourly markets.
      Classification done in Python using the title regex.
      """
      import re
      short_re = re.compile(r'\d+:\d+[AP]M-\d+:\d+[AP]M', re.IGNORECASE)

      with conn.cursor() as cur:
          cur.execute("""
              SELECT title, status, pnl_usd
              FROM yield_trades
              WHERE status IN ('won', 'lost', 'stopped')
          """)
          rows = cur.fetchall()

      def empty_bucket():
          return {"count": 0, "won": 0, "lost": 0, "pnl": 0.0}

      short = empty_bucket()
      hourly = empty_bucket()

      for title, status, pnl in rows:
          bucket = short if short_re.search(title or "") else hourly
          bucket["count"] += 1
          if status == "won":
              bucket["won"] += 1
          else:
              bucket["lost"] += 1
          bucket["pnl"] = round(bucket["pnl"] + float(pnl or 0), 4)

      for b in (short, hourly):
          resolved = b["won"] + b["lost"]
          b["win_rate"] = round(b["won"] / resolved, 4) if resolved > 0 else None
          b["avg_pnl"] = round(b["pnl"] / b["count"], 4) if b["count"] > 0 else 0.0

      return {"short_window": short, "hourly": hourly}


  def update_heartbeat_rv(conn, last_rv_value: float | None, rv_blocked_since) -> None:
      """Update RV guard tracking columns on bot_heartbeat."""
      with conn.cursor() as cur:
          cur.execute("""
              UPDATE bot_heartbeat
              SET last_rv_value = %s, rv_blocked_since = %s
              WHERE id = 1
          """, (last_rv_value, rv_blocked_since))
      conn.commit()
  ```

- [ ] **Step 4: Run tests — confirm they pass**

  ```bash
  pytest tests/test_yield_repository.py::test_get_win_streak_all_wins tests/test_yield_repository.py::test_get_win_streak_broken_streak tests/test_yield_repository.py::test_get_stop_loss_savings_none tests/test_yield_repository.py::test_get_stop_loss_savings_with_data tests/test_yield_repository.py::test_get_active_trades_empty -v
  ```
  Expected: all PASS

- [ ] **Step 5: Add db_service wrappers**

  Add to `service/db_service.py`:

  ```python
  def get_win_streak() -> dict:
      """Return current and best win streak from resolved trades."""
      with _get_connection() as conn:
          return repository.get_win_streak(conn)


  def get_stop_loss_savings() -> dict:
      """Return total USD saved by stop-loss exits and trigger count."""
      with _get_connection() as conn:
          return repository.get_stop_loss_savings(conn)


  def get_active_trades() -> list:
      """Return all trades in submitted or filled status."""
      with _get_connection() as conn:
          return repository.get_active_trades(conn)


  def get_market_type_stats() -> dict:
      """Return win/loss stats split by short-window vs hourly market type."""
      with _get_connection() as conn:
          return repository.get_market_type_stats(conn)


  def update_heartbeat_rv(last_rv_value: float | None, rv_blocked_since) -> None:
      """Write current RV value and blocked-since timestamp to heartbeat."""
      with _get_connection() as conn:
          repository.update_heartbeat_rv(conn, last_rv_value, rv_blocked_since)
  ```

  Note: `_get_connection()` is whatever the existing context manager pattern is in db_service.py — match it exactly to the existing wrappers above it.

- [ ] **Step 6: Run full test suite**

  ```bash
  pytest tests/ -v --tb=short
  ```
  Expected: all existing tests pass, new tests pass.

- [ ] **Step 7: Commit**

  ```bash
  git add core/database/repository.py service/db_service.py tests/test_yield_repository.py
  git commit -m "feat: add win streak, stop-loss savings, active trades, market type queries"
  ```

---

## Task 5: Bot Writes RV Data to Heartbeat

**Files:** `service/yield_farming_service.py`

- [ ] **Step 1: Find where RV is computed in yield_farming_service.py**

  Search for `rv` or `realized_vol` in the file. The RV guard block looks like:
  ```python
  if rv > _MAX_REALIZED_VOL:
      logger.warning("RV guard: BTC 30-min rv=%.4f > %.2f threshold ...", rv, _MAX_REALIZED_VOL)
      return  # or continue
  ```
  Identify the variable name holding the current RV value and where the guard fires.

- [ ] **Step 2: Add rv_blocked_since tracking at module level**

  At the top of `yield_farming_service.py`, add:
  ```python
  _rv_blocked_since: datetime | None = None
  ```

- [ ] **Step 3: Update heartbeat write after RV computation**

  Find the section that computes RV and calls the heartbeat update. Add RV tracking:

  ```python
  global _rv_blocked_since

  # After rv is computed:
  if rv is not None and rv > _MAX_REALIZED_VOL:
      if _rv_blocked_since is None:
          _rv_blocked_since = datetime.now(timezone.utc)
      db_service.update_heartbeat_rv(last_rv_value=rv, rv_blocked_since=_rv_blocked_since)
      logger.warning("RV guard: BTC 30-min rv=%.4f > %.2f threshold — skipping cycle (volatile regime)", rv, _MAX_REALIZED_VOL)
      return
  else:
      _rv_blocked_since = None
      if rv is not None:
          db_service.update_heartbeat_rv(last_rv_value=rv, rv_blocked_since=None)
  ```

  Place this where the existing RV guard check is, replacing/wrapping the existing return/continue.

- [ ] **Step 4: Verify bot still starts cleanly on VPS after deploy**

  ```bash
  scp service/yield_farming_service.py root@spain-vpn:/home/nick/polymarket_bot/service/
  ssh root@spain-vpn "systemctl restart polymarket-bot && sleep 3 && journalctl -u polymarket-bot -n 20 --no-pager"
  ```
  Expected: no errors, normal cycle logs visible.

- [ ] **Step 5: Verify columns are being written**

  ```bash
  ssh root@spain-vpn "su -c \"psql -d polymarket_bot -c 'SELECT last_rv_value, rv_blocked_since FROM bot_heartbeat WHERE id=1;'\" postgres"
  ```
  Expected: `last_rv_value` shows a numeric value (or NULL if rv fetch failed), `rv_blocked_since` shows NULL (if not currently blocked).

- [ ] **Step 6: Commit**

  ```bash
  git add service/yield_farming_service.py
  git commit -m "feat: track RV guard state in bot_heartbeat for live dashboard display"
  ```

---

## Task 6: New Monitoring API Endpoints

**Files:** `monitoring/app.py`

- [ ] **Step 1: Add `/api/guards/status` endpoint**

  Add to `monitoring/app.py` after the existing endpoints:

  ```python
  @app.route("/api/guards/status")
  def guards_status():
      """Live state of all active trading guards."""
      import os
      from datetime import timedelta

      direction = os.getenv("YIELD_DIRECTION_FILTER", "").strip(" '\"").lower() or None
      rv_threshold = float(os.getenv("YIELD_MAX_REALIZED_VOL", "0.50") or "0.50")
      hourly_cap_mins = 3.0       # _MAX_MINS_HOURLY from yield_farming_service
      stop_loss_threshold = 0.50  # _STOP_LOSS_THRESHOLD from monitor_service

      heartbeat = db_service.get_bot_heartbeat()
      last_rv = heartbeat.get("last_rv_value") if heartbeat else None
      rv_blocked_since_raw = heartbeat.get("rv_blocked_since") if heartbeat else None

      rv_blocked_mins = None
      if rv_blocked_since_raw:
          try:
              rv_blocked_dt = datetime.fromisoformat(str(rv_blocked_since_raw))
              if rv_blocked_dt.tzinfo is None:
                  rv_blocked_dt = rv_blocked_dt.replace(tzinfo=timezone.utc)
              rv_blocked_mins = round((datetime.now(timezone.utc) - rv_blocked_dt).total_seconds() / 60, 1)
          except Exception:
              pass

      rv_blocking = last_rv is not None and float(last_rv) > rv_threshold

      return jsonify({
          "direction": {
              "active": direction is not None,
              "value": direction or "all",
              "label": f"Direction: {direction.upper()} only" if direction else "Direction: all",
          },
          "rv": {
              "threshold": rv_threshold,
              "current": float(last_rv) if last_rv is not None else None,
              "blocking": rv_blocking,
              "blocked_mins": rv_blocked_mins,
              "label": f"RV: {float(last_rv):.2f} / {rv_threshold:.2f}" if last_rv is not None else f"RV: — / {rv_threshold:.2f}",
          },
          "hourly_cap": {
              "active": True,
              "mins": hourly_cap_mins,
              "label": f"Hourly: ≤{hourly_cap_mins:.0f} min",
          },
          "stop_loss": {
              "active": True,
              "threshold": stop_loss_threshold,
              "label": f"Stop-loss: ${stop_loss_threshold:.2f}",
          },
      })
  ```

- [ ] **Step 2: Add `/api/trades/active` endpoint**

  ```python
  @app.route("/api/trades/active")
  def active_trades():
      """All trades currently in submitted or filled status with time remaining."""
      try:
          trades = db_service.get_active_trades()
          now_utc = datetime.now(timezone.utc)
          result = []
          for t in trades:
              mins_remaining = None
              sub = t.get("submitted_at")
              mtc = t.get("minutes_to_close")
              if sub and mtc is not None:
                  try:
                      sub_dt = datetime.fromisoformat(str(sub))
                      if sub_dt.tzinfo is None:
                          sub_dt = sub_dt.replace(tzinfo=timezone.utc)
                      close_dt = sub_dt + timedelta(minutes=float(mtc))
                      mins_remaining = round((close_dt - now_utc).total_seconds() / 60, 1)
                  except Exception:
                      pass
              result.append({
                  "id": t["id"],
                  "title": t["title"],
                  "outcome": t["outcome"],
                  "status": t["status"],
                  "fill_price": float(t["fill_price"]) if t.get("fill_price") else None,
                  "cost_usd": float(t["cost_usd"]) if t.get("cost_usd") else None,
                  "mins_remaining": mins_remaining,
              })
          return jsonify(result)
      except Exception as e:
          return jsonify({"error": str(e)}), 500
  ```

- [ ] **Step 3: Add `/api/stats/streak` endpoint**

  ```python
  @app.route("/api/stats/streak")
  def streak_stats():
      """Current win streak, best streak, and stop-loss savings."""
      try:
          streak = db_service.get_win_streak()
          savings = db_service.get_stop_loss_savings()
          return jsonify({
              "current_streak": streak["current"],
              "best_streak": streak["best"],
              "stop_loss_trigger_count": savings["trigger_count"],
              "stop_loss_total_saved": savings["total_saved"],
          })
      except Exception as e:
          return jsonify({"error": str(e)}), 500
  ```

- [ ] **Step 4: Add `/api/analytics/market-types` endpoint**

  ```python
  @app.route("/api/analytics/market-types")
  def market_types():
      """Win/loss stats split by short-window vs hourly market type."""
      try:
          stats = db_service.get_market_type_stats()
          return jsonify(stats)
      except Exception as e:
          return jsonify({"error": str(e)}), 500
  ```

- [ ] **Step 5: Add `/api/analytics/guard-stats` endpoint**

  ```python
  @app.route("/api/analytics/guard-stats")
  def guard_stats():
      """Guard fire counts derivable from existing DB data."""
      try:
          savings = db_service.get_stop_loss_savings()
          summary = db_service.get_yield_pnl_summary()
          heartbeat = db_service.get_bot_heartbeat()
          last_rv = heartbeat.get("last_rv_value") if heartbeat else None
          rv_threshold = float(os.getenv("YIELD_MAX_REALIZED_VOL", "0.50") or "0.50")
          return jsonify({
              "stop_loss_triggers": savings["trigger_count"],
              "expired_trades": summary.get("expired", 0),
              "current_rv": float(last_rv) if last_rv is not None else None,
              "rv_threshold": rv_threshold,
          })
      except Exception as e:
          return jsonify({"error": str(e)}), 500
  ```

  Add `import os` to the top of `monitoring/app.py` if not already present.

- [ ] **Step 6: Manually test all new endpoints**

  Start the monitoring app locally:
  ```bash
  python monitoring/app.py
  ```
  Then in another terminal:
  ```bash
  curl http://localhost:5051/api/guards/status | python -m json.tool
  curl http://localhost:5051/api/trades/active | python -m json.tool
  curl http://localhost:5051/api/stats/streak | python -m json.tool
  curl http://localhost:5051/api/analytics/market-types | python -m json.tool
  curl http://localhost:5051/api/analytics/guard-stats | python -m json.tool
  ```
  Expected: each returns valid JSON with no errors.

- [ ] **Step 7: Commit**

  ```bash
  git add monitoring/app.py
  git commit -m "feat: add guards, active trades, streak, and market-type API endpoints"
  ```

---

## Task 7: Overview Page Enhancements (index.html)

**Files:** `monitoring/index.html`

- [ ] **Step 1: Add auto-refresh infrastructure**

  Find the closing `</script>` tag in index.html. Add before it:
  ```javascript
  // Auto-refresh all live panels every 5 seconds
  function startAutoRefresh() {
      setInterval(function() {
          fetchGuards();
          fetchActiveTrades();
          fetchStreak();
          fetchLiveTrades();
      }, 5000);
  }
  document.addEventListener('DOMContentLoaded', function() {
      fetchGuards();
      fetchActiveTrades();
      fetchStreak();
      fetchLiveTrades();
      startAutoRefresh();
  });
  ```

- [ ] **Step 2: Add Guard Status Panel**

  Find the main content area in index.html (after the stats row, before the trades table). Insert:
  ```html
  <!-- Guard Status Panel -->
  <div class="card" style="margin-bottom:20px">
    <div class="card-header">
      <h3>Active Guards <span id="guard-refresh-dot" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#4ade80;margin-left:8px;vertical-align:middle"></span></h3>
    </div>
    <div class="card-body">
      <div id="guard-pills" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
        <span style="color:#64748b;font-size:13px">Loading...</span>
      </div>
    </div>
  </div>
  ```

  Add the JS function:
  ```javascript
  function fetchGuards() {
      fetch('/api/guards/status')
          .then(r => r.json())
          .then(data => {
              const container = document.getElementById('guard-pills');
              if (!container) return;

              function pill(label, ok, warning) {
                  const bg = warning ? '#78350f' : (ok ? '#14532d' : '#1e293b');
                  const color = warning ? '#fbbf24' : (ok ? '#4ade80' : '#94a3b8');
                  return `<span style="background:${bg};color:${color};padding:5px 12px;border-radius:20px;font-size:13px;font-weight:500">${label}</span>`;
              }

              let html = '';
              if (data.direction && data.direction.active) {
                  html += pill('▲ ' + data.direction.label, true, false);
              }
              if (data.rv) {
                  const blocking = data.rv.blocking;
                  const label = blocking
                      ? data.rv.label + ' ⚠ PAUSED' + (data.rv.blocked_mins ? ` ${data.rv.blocked_mins}m` : '')
                      : data.rv.label + ' ✓';
                  html += pill(label, !blocking, blocking);
              }
              if (data.hourly_cap) {
                  html += pill(data.hourly_cap.label + ' ✓', true, false);
              }
              if (data.stop_loss) {
                  html += pill(data.stop_loss.label + ' ✓', true, false);
              }
              container.innerHTML = html || '<span style="color:#64748b">No guards active</span>';
          })
          .catch(() => {});
  }
  ```

- [ ] **Step 3: Add Streak + Stop-loss Savings stat cards**

  Find the stats row (the div containing existing stat cards for balance, P&L, win rate). After the last existing card, append two new cards:
  ```html
  <div class="stat-card">
    <div class="stat-value" id="stat-streak">—</div>
    <div class="stat-label">Win Streak</div>
    <div class="stat-sub" id="stat-best-streak">best: —</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" id="stat-sl-saved">—</div>
    <div class="stat-label">Saved by SL</div>
    <div class="stat-sub" id="stat-sl-count">— triggers</div>
  </div>
  ```

  Add the JS function:
  ```javascript
  function fetchStreak() {
      fetch('/api/stats/streak')
          .then(r => r.json())
          .then(data => {
              const streak = document.getElementById('stat-streak');
              const best = document.getElementById('stat-best-streak');
              const saved = document.getElementById('stat-sl-saved');
              const count = document.getElementById('stat-sl-count');
              if (streak) streak.textContent = (data.current_streak > 0 ? '🔥' : '') + data.current_streak;
              if (best) best.textContent = 'best: ' + data.best_streak;
              if (saved) saved.textContent = '$' + data.stop_loss_total_saved.toFixed(2);
              if (count) count.textContent = data.stop_loss_trigger_count + ' trigger' + (data.stop_loss_trigger_count !== 1 ? 's' : '');
          })
          .catch(() => {});
  }
  ```

- [ ] **Step 4: Add Active Positions Monitor**

  Insert before the trades table:
  ```html
  <!-- Active Positions Monitor -->
  <div class="card" style="margin-bottom:20px" id="active-positions-card">
    <div class="card-header">
      <h3>Active Positions <span id="active-count" style="background:#1e293b;color:#64748b;padding:2px 8px;border-radius:10px;font-size:12px;margin-left:6px">0</span></h3>
    </div>
    <div class="card-body" id="active-positions-body">
      <p style="color:#64748b;font-size:13px">No open positions.</p>
    </div>
  </div>
  ```

  Add the JS function:
  ```javascript
  function fetchActiveTrades() {
      fetch('/api/trades/active')
          .then(r => r.json())
          .then(trades => {
              const body = document.getElementById('active-positions-body');
              const countEl = document.getElementById('active-count');
              const card = document.getElementById('active-positions-card');
              if (!body) return;

              if (countEl) countEl.textContent = trades.length;
              if (!trades.length) {
                  body.innerHTML = '<p style="color:#64748b;font-size:13px">No open positions.</p>';
                  return;
              }

              const rows = trades.map(t => {
                  const minsLeft = t.mins_remaining !== null ? t.mins_remaining.toFixed(1) + 'm left' : '';
                  const priceStr = t.fill_price ? `@ $${t.fill_price.toFixed(4)}` : '';
                  const warn = t.fill_price && t.fill_price < 0.55;
                  const color = warn ? '#fbbf24' : (t.status === 'filled' ? '#38bdf8' : '#94a3b8');
                  return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #1e293b;font-size:13px">
                      <span style="color:${color}">${t.title} (${t.outcome})</span>
                      <span style="color:#64748b">${t.status} ${priceStr} · ${minsLeft}</span>
                  </div>`;
              }).join('');
              body.innerHTML = rows;
          })
          .catch(() => {});
  }
  ```

- [ ] **Step 5: Make trades table auto-refresh**

  Find the existing function that loads the trades table (likely called `loadTrades()` or similar). Ensure it's called by `fetchLiveTrades()`:
  ```javascript
  function fetchLiveTrades() {
      // Call the existing trade-loading function
      if (typeof loadTrades === 'function') loadTrades();
  }
  ```
  If the trades table fetches on page load but doesn't have a callable function, extract the fetch logic into a named function `loadTrades()` and call it from both DOMContentLoaded and the refresh loop.

- [ ] **Step 6: Manual verify in browser**

  ```bash
  python monitoring/app.py
  ```
  Open http://localhost:5051. Confirm:
  - Guard pills render with correct colours
  - Streak cards show numbers
  - Active positions shows current open trades (or "No open positions.")
  - Page refreshes data every 5s (watch network tab)

- [ ] **Step 7: Commit**

  ```bash
  git add monitoring/index.html
  git commit -m "feat: add guard status, active positions, streak, and auto-refresh to overview page"
  ```

---

## Task 8: Analytics Page Enhancements (analytics.html)

**Files:** `monitoring/analytics.html`

- [ ] **Step 1: Add auto-refresh to analytics page**

  Same pattern as index.html — add `setInterval` calling `fetchMarketTypes()` and `fetchGuardStats()` every 5000ms on `DOMContentLoaded`.

- [ ] **Step 2: Add Market Type Breakdown section**

  Append before the closing `</main>` or `</div>` of the main content:
  ```html
  <!-- Market Type Breakdown -->
  <div class="card" style="margin-top:24px">
    <div class="card-header"><h3>Market Type Breakdown</h3></div>
    <div class="card-body">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px" id="market-type-grid">
        <div style="color:#64748b;font-size:13px">Loading...</div>
      </div>
    </div>
  </div>
  ```

  Add JS:
  ```javascript
  function fetchMarketTypes() {
      fetch('/api/analytics/market-types')
          .then(r => r.json())
          .then(data => {
              const grid = document.getElementById('market-type-grid');
              if (!grid) return;

              function col(label, color, bucket) {
                  const wr = bucket.win_rate !== null ? (bucket.win_rate * 100).toFixed(1) + '%' : '—';
                  const pnl = bucket.avg_pnl >= 0 ? '+$' + bucket.avg_pnl.toFixed(2) : '-$' + Math.abs(bucket.avg_pnl).toFixed(2);
                  return `<div style="background:#1e293b;border-radius:8px;padding:16px;border-top:3px solid ${color}">
                      <div style="color:${color};font-size:12px;font-weight:600;margin-bottom:12px">${label}</div>
                      <div style="font-size:22px;font-weight:bold;color:#f8fafc;margin-bottom:4px">${wr}</div>
                      <div style="font-size:13px;color:#94a3b8">${bucket.count} trades · avg ${pnl}</div>
                      <div style="font-size:12px;color:#475569;margin-top:4px">${bucket.won}W / ${bucket.lost}L</div>
                  </div>`;
              }

              grid.innerHTML = col('SHORT-WINDOW', '#38bdf8', data.short_window)
                             + col('HOURLY (≤3 min)', '#a78bfa', data.hourly);
          })
          .catch(() => {});
  }
  ```

- [ ] **Step 3: Add Guard Effectiveness Stats section**

  ```html
  <!-- Guard Effectiveness -->
  <div class="card" style="margin-top:24px">
    <div class="card-header"><h3>Guard Activity</h3></div>
    <div class="card-body">
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;text-align:center" id="guard-stats-grid">
        <div style="color:#64748b;font-size:13px">Loading...</div>
      </div>
    </div>
  </div>
  ```

  Add JS:
  ```javascript
  function fetchGuardStats() {
      fetch('/api/analytics/guard-stats')
          .then(r => r.json())
          .then(data => {
              const grid = document.getElementById('guard-stats-grid');
              if (!grid) return;

              function stat(value, label, color) {
                  return `<div><div style="font-size:28px;font-weight:bold;color:${color}">${value}</div>
                      <div style="font-size:12px;color:#64748b;margin-top:4px">${label}</div></div>`;
              }

              const rvLabel = data.current_rv !== null
                  ? `RV: ${data.current_rv.toFixed(2)} / ${data.rv_threshold.toFixed(2)}`
                  : `RV threshold: ${data.rv_threshold}`;

              grid.innerHTML = stat(data.stop_loss_triggers, 'Stop-loss triggers', '#fb923c')
                             + stat(data.expired_trades, 'Expired (unfilled)', '#64748b')
                             + stat(rvLabel, 'Current volatility', data.current_rv > data.rv_threshold ? '#fbbf24' : '#4ade80');
          })
          .catch(() => {});
  }
  ```

- [ ] **Step 4: Manual verify in browser**

  Open http://localhost:5051/analytics. Confirm market type breakdown and guard stats render with real data.

- [ ] **Step 5: Commit**

  ```bash
  git add monitoring/analytics.html
  git commit -m "feat: add market type breakdown and guard activity stats to analytics page"
  ```

---

## Task 9: Health Page Enhancement (health.html)

**Files:** `monitoring/health.html`, `monitoring/app.py`

- [ ] **Step 1: Update health_check_api to add RV guard check**

  In `monitoring/app.py`, find the `health_check_api()` function. The stuck trades check currently scores 10 pts. Change it to 5 pts:

  ```python
  score_stuck = 0 if stuck else 5   # was 10
  checks["stuck_trades"] = {
      "label": "Stuck Trades",
      "score": score_stuck, "max": 5, "passed": not stuck,   # max was 10
      "detail": "No trades pending >30 min" if not stuck else f"{len(stuck)} trade(s) pending >30 minutes",
  }
  ```

  Then add the RV guard check (5 pts) after stuck_trades:

  ```python
  # 6. RV guard — 5 pts: flag if blocked continuously >30 min
  rv_blocked_mins = None
  heartbeat_rv = db_service.get_bot_heartbeat()
  rv_blocked_since_raw = heartbeat_rv.get("rv_blocked_since") if heartbeat_rv else None
  if rv_blocked_since_raw:
      try:
          rv_blocked_dt = datetime.fromisoformat(str(rv_blocked_since_raw))
          if rv_blocked_dt.tzinfo is None:
              rv_blocked_dt = rv_blocked_dt.replace(tzinfo=timezone.utc)
          rv_blocked_mins = round((datetime.now(timezone.utc) - rv_blocked_dt).total_seconds() / 60, 1)
      except Exception:
          pass

  rv_ok = rv_blocked_mins is None or rv_blocked_mins < 30
  score_rv = 5 if rv_ok else 0
  last_rv_val = heartbeat_rv.get("last_rv_value") if heartbeat_rv else None
  rv_detail = (
      f"RV: {float(last_rv_val):.2f} — not blocking" if rv_ok and last_rv_val
      else f"RV guard blocked {rv_blocked_mins:.0f} min (rv={float(last_rv_val):.2f})" if last_rv_val
      else "RV data not available"
  )
  checks["rv_guard"] = {
      "label": "RV Guard",
      "score": score_rv, "max": 5, "passed": rv_ok,
      "detail": rv_detail,
  }
  total_score += score_rv
  ```

  Update the grade thresholds comment — max is still 100 (5+5=10 for these two checks combined, unchanged).

- [ ] **Step 2: Add auto-refresh to health page**

  Same 5s setInterval pattern. Call the existing health-fetching function from both DOMContentLoaded and the interval.

- [ ] **Step 3: Manual verify health page**

  Open http://localhost:5051/health. Confirm:
  - RV Guard check appears in the list
  - Score is correct (5 pts available, passes when RV not blocking)
  - Total still out of 100

- [ ] **Step 4: Commit**

  ```bash
  git add monitoring/app.py monitoring/health.html
  git commit -m "feat: add RV guard health check; rebalance stuck-trades score to 5pts"
  ```

---

## Task 10: Telegram Persistent Keyboard + New Commands

**Files:** `service/telegram_service.py`, `main.py`

- [ ] **Step 1: Add keyboard constant and register_commands() to telegram_service.py**

  Add near the top of `telegram_service.py` (after `_BASE_URL`):

  ```python
  _REPLY_KEYBOARD = {
      "keyboard": [
          [{"text": "🏥 Health"}, {"text": "💰 Balance"}, {"text": "📊 Summary"}],
          [{"text": "📋 Trades"}, {"text": "⚠️ Reset Risk"}, {"text": "🧪 Test"}],
      ],
      "resize_keyboard": True,
      "persistent": True,
      "is_persistent": True,
  }

  # Map keyboard button text → command string
  _KEYBOARD_TEXT_MAP: dict[str, str] = {
      "🏥 health": "/health",
      "💰 balance": "/balance",
      "📊 summary": "/summary",
      "📋 trades": "/trades",
      "⚠️ reset risk": "/reset_risk",
      "🧪 test": "/test",
  }
  ```

- [ ] **Step 2: Add register_commands() function**

  ```python
  def register_commands() -> bool:
      """
      Register the bot command list with Telegram via setMyCommands.
      Makes commands appear in the / autocomplete menu.
      Called once at bot startup (idempotent).
      """
      if not is_configured():
          return False
      commands = [
          {"command": "health",     "description": "Bot uptime, cycle count, DB and geo status"},
          {"command": "balance",    "description": "Current USDC balance and drawdown"},
          {"command": "summary",    "description": "Session P&L, win rate, trade count"},
          {"command": "trades",     "description": "Last 5 resolved trades with P&L"},
          {"command": "reset_risk", "description": "Reset risk guard and resume trading"},
          {"command": "test",       "description": "Run a live order test on the CLOB"},
      ]
      try:
          response = requests.post(
              f"{_BASE_URL}/setMyCommands",
              json={"commands": commands},
              timeout=REQUEST_TIMEOUT_SECONDS,
          )
          response.raise_for_status()
          logger.info("Telegram: bot commands registered (%d commands).", len(commands))
          return True
      except Exception as exc:
          logger.warning("Telegram: failed to register commands: %s", exc)
          return False
  ```

- [ ] **Step 3: Add send_keyboard() function**

  ```python
  def send_keyboard() -> bool:
      """Send the persistent reply keyboard to the chat. Call once at startup."""
      return send_message("🤖 Yield farming bot online.", reply_markup=_REPLY_KEYBOARD)
  ```

- [ ] **Step 4: Update send_message() to accept reply_markup**

  Change the signature and request body:

  ```python
  def send_message(text: str, reply_markup: dict | None = None) -> bool:
      if not is_configured():
          logger.warning("Telegram not configured — skipping message.")
          return False
      try:
          payload = {
              "chat_id": _CHAT_ID,
              "text": text,
              "parse_mode": "HTML",
          }
          if reply_markup is not None:
              payload["reply_markup"] = reply_markup
          response = requests.post(
              f"{_BASE_URL}/sendMessage",
              json=payload,
              timeout=REQUEST_TIMEOUT_SECONDS,
          )
          response.raise_for_status()
          logger.debug("Telegram message sent successfully.")
          return True
      except Exception as exc:
          logger.error("Failed to send Telegram message: %s", exc)
          return False
  ```

- [ ] **Step 5: Update get_pending_commands() to map keyboard text → commands**

  In `get_pending_commands()`, replace:
  ```python
  if text.startswith("/"):
      commands.append(text.split("@")[0])
  ```
  with:
  ```python
  if text in _KEYBOARD_TEXT_MAP:
      commands.append(_KEYBOARD_TEXT_MAP[text])
  elif text.startswith("/"):
      commands.append(text.split("@")[0])
  ```

- [ ] **Step 6: Add 3 new send_* formatters**

  ```python
  def send_balance_status(current_balance: float, session_start: float, drawdown_pct: float, floor: float) -> bool:
      """Send current balance and drawdown on /balance command."""
      icon = "🟢" if current_balance >= floor * 2 else ("🟡" if current_balance >= floor else "🔴")
      text = (
          f"{icon} <b>Balance Status</b>\n"
          f"\n"
          f"🏦 <b>Current balance:</b> ${current_balance:.2f}\n"
          f"📊 <b>Session start:</b> ${session_start:.2f}\n"
          f"📉 <b>Drawdown:</b> {drawdown_pct:.1f}%\n"
          f"🛑 <b>Floor:</b> ${floor:.2f}"
      )
      return send_message(text, reply_markup=_REPLY_KEYBOARD)


  def send_session_summary(total_trades: int, won: int, lost: int, win_rate: float, net_pnl: float) -> bool:
      """Send session P&L summary on /summary command."""
      icon = "📈" if net_pnl >= 0 else "📉"
      text = (
          f"{icon} <b>Session Summary</b>\n"
          f"\n"
          f"📊 <b>Trades:</b> {total_trades} | Won: {won} | Lost: {lost}\n"
          f"🏆 <b>Win rate:</b> {win_rate * 100:.1f}%\n"
          f"💸 <b>Net P&L:</b> ${net_pnl:+.2f}"
      )
      return send_message(text, reply_markup=_REPLY_KEYBOARD)


  def send_recent_trades(trades: list) -> bool:
      """Send last N resolved trades on /trades command."""
      if not trades:
          return send_message("📋 <b>No resolved trades yet.</b>", reply_markup=_REPLY_KEYBOARD)
      lines = ["📋 <b>Recent Trades</b>\n"]
      for t in trades:
          status = t.get("status", "?")
          pnl = t.get("pnl_usd")
          icon = "✅" if status == "won" else ("🛑" if status == "stopped" else ("❌" if status == "lost" else "⏸"))
          pnl_str = f"${float(pnl):+.2f}" if pnl is not None else "—"
          title = (t.get("title") or "Unknown")[:45]
          lines.append(f"{icon} {title}\n    {pnl_str}")
      return send_message("\n".join(lines), reply_markup=_REPLY_KEYBOARD)
  ```

  Also add `reply_markup=_REPLY_KEYBOARD` to all existing command response calls (`send_health_report`, `send_test_result`, `send_risk_guard_reset`) so the keyboard reappears after each response.

- [ ] **Step 7: Wire new commands in main.py**

  In `main.py`, find the command dispatch loop. After startup, call:
  ```python
  telegram_service.register_commands()
  telegram_service.send_keyboard()
  ```

  In the dispatch loop, add:
  ```python
  elif cmd == "/balance":
      hb = db_service.get_bot_heartbeat()
      from service.risk_guard_service import get_balance_floor
      current = float(hb.get("current_balance") or 0) if hb else 0.0
      start = float(hb.get("session_start_balance") or 0) if hb else 0.0
      drawdown = ((start - current) / start * 100) if start > 0 else 0.0
      telegram_service.send_balance_status(
          current_balance=current,
          session_start=start,
          drawdown_pct=drawdown,
          floor=get_balance_floor(),
      )

  elif cmd == "/summary":
      summary = db_service.get_yield_pnl_summary()
      telegram_service.send_session_summary(
          total_trades=summary["total_trades"],
          won=summary["won"],
          lost=summary["lost"],
          win_rate=summary["win_rate"],
          net_pnl=summary["net_pnl"],
      )

  elif cmd == "/trades":
      recent = db_service.get_yield_trades_page(status=None, limit=5, offset=0)
      telegram_service.send_recent_trades(recent)
  ```

- [ ] **Step 8: Deploy and test on VPS**

  ```bash
  scp service/telegram_service.py main.py root@spain-vpn:/home/nick/polymarket_bot/service/
  scp main.py root@spain-vpn:/home/nick/polymarket_bot/
  ssh root@spain-vpn "systemctl restart polymarket-bot && sleep 3 && journalctl -u polymarket-bot -n 20 --no-pager"
  ```

  In Telegram:
  - Confirm the keyboard appears
  - Tap each button — confirm each sends a response
  - Type `/` — confirm command list appears with descriptions

- [ ] **Step 9: Commit**

  ```bash
  git add service/telegram_service.py main.py
  git commit -m "feat: add Telegram persistent keyboard, /balance /summary /trades commands, BotFather registration"
  ```

---

## Task 11: Final Deploy, Commit & Push

- [ ] **Step 1: Deploy all changed files to VPS**

  ```bash
  scp core/database/connection.py root@spain-vpn:/home/nick/polymarket_bot/core/database/
  scp core/database/repository.py root@spain-vpn:/home/nick/polymarket_bot/core/database/
  scp service/db_service.py root@spain-vpn:/home/nick/polymarket_bot/service/
  scp service/yield_farming_service.py root@spain-vpn:/home/nick/polymarket_bot/service/
  scp monitoring/app.py monitoring/index.html monitoring/analytics.html monitoring/health.html root@spain-vpn:/home/nick/polymarket_bot/monitoring/
  ```

- [ ] **Step 2: Restart bot and monitoring service**

  ```bash
  ssh root@spain-vpn "systemctl restart polymarket-bot"
  ssh root@spain-vpn "pkill -f 'monitoring/app.py'; cd /home/nick/polymarket_bot && nohup python monitoring/app.py &"
  ```

- [ ] **Step 3: Verify bot started cleanly**

  ```bash
  ssh root@spain-vpn "journalctl -u polymarket-bot -n 30 --no-pager"
  ```
  Expected: no errors, normal scan logs, "Telegram: bot commands registered" line.

- [ ] **Step 4: Verify DB migration applied**

  ```bash
  ssh root@spain-vpn "su -c \"psql -d polymarket_bot -c '\\d bot_heartbeat'\" postgres"
  ```
  Expected: `last_rv_value` and `rv_blocked_since` columns present.

- [ ] **Step 5: Run full test suite**

  ```bash
  pytest tests/ -v --tb=short
  ```
  Expected: all tests pass.

- [ ] **Step 6: Final commit and push**

  ```bash
  git add -A
  git status  # review what's staged
  git commit -m "chore: final integration — monitoring dashboard v2, telegram menu, code cleanup"
  git push origin main
  ```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Code cleaning (Task 1)
- ✅ Documentation (Task 2)
- ✅ error_analysis.md patterns 005-008 (Task 2)
- ✅ DB columns last_rv_value, rv_blocked_since (Task 3)
- ✅ All new repository queries (Task 4)
- ✅ Bot writes RV to heartbeat (Task 5)
- ✅ 5 new API endpoints (Task 6)
- ✅ Overview: guard pills, active positions, streak cards, live trade feed, auto-refresh (Task 7)
- ✅ Analytics: market type breakdown, guard stats (Task 8)
- ✅ Health: RV guard check, score rebalanced (Task 9)
- ✅ Telegram: keyboard 2×3, register_commands, /balance /summary /trades, keyboard on all responses (Task 10)
- ✅ Deploy + push (Task 11)
