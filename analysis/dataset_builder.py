"""
dataset_builder.py
Builds a historical backtesting dataset from Polymarket APIs.

For every resolved Up/Down 5-minute crypto market within the requested window,
fetches 1-minute CLOB price history and records prices at T-10, T-5, T-3, T-1
before close. The dataset enables backtesting strategies that our live trade DB
cannot test (e.g. two-phase T10→T5 confirmation).

Output: CSV at analysis/historical_dataset.csv
        DB table: backtest_markets (optional, requires VPS tunnel or local PG)

Run from project root:
    python -m analysis.dataset_builder --days 2  --no-db
    python -m analysis.dataset_builder --days 90 --no-db --out analysis/historical_dataset.csv

Rate limiting: 0.3 req/sec per worker. Use --workers to control concurrency.
  2-day run:  ~5,800 markets / 10 workers = ~3 min
  90-day run: ~260,000 markets / 10 workers = ~130 min
"""

import argparse
import csv
import json
import os
import sys
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── API endpoints ─────────────────────────────────────────────────────────────

_GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
_CLOB_PRICES   = "https://clob.polymarket.com/prices-history"
_TIMEOUT       = 15
_RATE_LIMIT_S  = 0.3   # ~3 req/sec — Polymarket CLOB is tolerant of this rate


# ── DB schema ─────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS backtest_markets (
    id                  SERIAL PRIMARY KEY,
    condition_id        TEXT NOT NULL,
    token_id_up         TEXT,
    token_id_down       TEXT,
    title               TEXT,
    outcome_winner      TEXT,           -- 'Up' or 'Down'
    close_time          TIMESTAMPTZ,
    price_up_t10        NUMERIC(6,4),   -- Up token price 10 min before close
    price_up_t5         NUMERIC(6,4),
    price_up_t3         NUMERIC(6,4),
    price_up_t1         NUMERIC(6,4),
    price_up_t0         NUMERIC(6,4),
    signal_direction    TEXT,           -- which outcome was >=0.95 at T-10 (Up/Down/NULL)
    signal_price_t10    NUMERIC(6,4),
    signal_price_t5     NUMERIC(6,4),
    signal_price_t3     NUMERIC(6,4),
    signal_confirmed_t5 BOOLEAN,        -- still >=0.95 at T-5?
    signal_confirmed_t3 BOOLEAN,
    would_win_baseline  BOOLEAN,        -- baseline strategy win?
    would_win_2phase    BOOLEAN,        -- two-phase (T10+T5 confirm) win? NULL = not traded
    fetched_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(condition_id)
);
"""

_CSV_FIELDS = [
    "condition_id", "title", "outcome_winner", "close_time",
    "price_up_t10", "price_up_t5", "price_up_t3", "price_up_t1", "price_up_t0",
    "signal_direction", "signal_price_t10", "signal_price_t5", "signal_price_t3",
    "signal_confirmed_t5", "signal_confirmed_t3",
    "would_win_baseline", "would_win_2phase",
]


# ── Gamma market fetch ────────────────────────────────────────────────────────

def _is_updown_market(title: str) -> bool:
    return "up or down" in title.lower()


def _fetch_gamma_markets(days_back: int) -> list[dict]:
    """
    Fetch all resolved Up/Down markets that closed within the last N days.

    Uses end_date_min + order=endDate&ascending=true so we page through markets
    in chronological order. Stops when we reach the end.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    markets = []
    offset = 0
    limit = 500

    while True:
        params = {
            "active": "false",
            "closed": "true",
            "limit": limit,
            "offset": offset,
            "order": "endDate",
            "ascending": "true",
            "end_date_min": cutoff_str,
        }
        try:
            resp = requests.get(_GAMMA_MARKETS, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            batch = resp.json()
        except Exception as e:
            logger.error("Gamma fetch failed at offset=%d: %s", offset, e)
            break

        if not batch:
            break

        batch_updown = 0
        for m in batch:
            title = m.get("question", "") or m.get("title", "")
            if not _is_updown_market(title):
                continue

            end_date_str = m.get("endDate", "")
            if not end_date_str:
                continue
            try:
                close_time = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            cid = m.get("conditionId", "")
            if not cid:
                continue

            # clobTokenIds is a JSON string of [up_token, down_token]
            raw_tokens = m.get("clobTokenIds", "[]")
            try:
                token_ids = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
            except (json.JSONDecodeError, TypeError):
                continue
            if len(token_ids) < 2:
                continue

            # outcomes field: ["Up", "Down"] — index matches clobTokenIds
            outcomes = m.get("outcomes", ["Up", "Down"])
            try:
                outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
            except (json.JSONDecodeError, TypeError):
                outcomes = ["Up", "Down"]

            up_idx   = next((i for i, o in enumerate(outcomes) if o.lower() == "up"),   0)
            down_idx = next((i for i, o in enumerate(outcomes) if o.lower() == "down"), 1)

            # outcomePrices: ["1","0"] = Up won, ["0","1"] = Down won (after resolution)
            raw_prices = m.get("outcomePrices", "[]")
            try:
                outcome_prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
            except (json.JSONDecodeError, TypeError):
                outcome_prices = []

            winner = None
            if len(outcome_prices) >= 2:
                try:
                    up_price = float(outcome_prices[up_idx])
                    dn_price = float(outcome_prices[down_idx])
                    if up_price >= 0.99:
                        winner = "Up"
                    elif dn_price >= 0.99:
                        winner = "Down"
                except (ValueError, IndexError):
                    pass

            # Skip markets where the measured price period is < 12 minutes
            # (e.g. "11:40AM-11:45AM ET"). These 5-min period markets can't have
            # a meaningful signal at T-10 because the period barely started.
            # We detect this by comparing startDate to endDate from the title
            # period, but since that's hard to parse, we use a proxy: if the
            # market's Gamma startDate is < 12 min before endDate, skip.
            start_date_str = m.get("startDate", "")
            if start_date_str:
                try:
                    start_time = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
                    duration_min = (close_time - start_time).total_seconds() / 60
                    if duration_min < 12:
                        continue  # market too short — no T-10 data possible
                except ValueError:
                    pass

            markets.append({
                "condition_id":  cid,
                "title":         title,
                "close_time":    close_time,
                "token_id_up":   token_ids[up_idx],
                "token_id_down": token_ids[down_idx],
                "winner":        winner,
            })
            batch_updown += 1

        logger.info("Gamma: offset=%d, batch=%d, updown=%d, total=%d",
                    offset, len(batch), batch_updown, len(markets))

        if len(batch) < limit:
            break  # last page

        offset += limit
        time.sleep(_RATE_LIMIT_S)

    logger.info("Gamma: %d qualifying Up/Down markets found over %d days", len(markets), days_back)
    return markets


# ── CLOB price history ────────────────────────────────────────────────────────

def _fetch_price_history(token_id: str) -> list[tuple[datetime, float]]:
    """
    Fetch 1-minute resolution price history for a CLOB token.
    Returns list of (utc_datetime, price) sorted ascending.
    """
    try:
        resp = requests.get(
            _CLOB_PRICES,
            params={"interval": "max", "market": token_id, "fidelity": 1},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        history = resp.json().get("history", [])
        result = [
            (datetime.fromtimestamp(pt["t"], tz=timezone.utc), float(pt["p"]))
            for pt in history
        ]
        return sorted(result, key=lambda x: x[0])
    except Exception as e:
        logger.warning("Price history failed for %s: %s", token_id[:20], e)
        return []


def _price_before(
    history: list[tuple[datetime, float]],
    close_time: datetime,
    minutes_before: float,
) -> Optional[float]:
    """Last price at or before (close_time - minutes_before)."""
    target = close_time - timedelta(minutes=minutes_before)
    for ts, price in reversed(history):
        if ts <= target:
            return price
    return None


# ── Row builder ───────────────────────────────────────────────────────────────

def _build_row(market: dict) -> Optional[dict]:
    """
    Fetch Up token price history, compute T-10/5/3/1/0 prices,
    simulate baseline and two-phase strategies.
    """
    close_time = market["close_time"]
    cid        = market["condition_id"]
    winner     = market["winner"]

    if winner is None:
        logger.debug("No winner for %s — skipping", cid[:20])
        return None

    up_hist = _fetch_price_history(market["token_id_up"])
    time.sleep(_RATE_LIMIT_S)  # per-worker rate limit

    if not up_hist:
        logger.warning("No price history for Up token %s — skipping", cid[:20])
        return None

    # Skip markets with very few price points — not enough trading history to have
    # a meaningful T-10 price (e.g., 5-minute period markets that just opened)
    if len(up_hist) < 15:
        logger.debug("Only %d price points for %s — skipping (no T-10 data possible)",
                     len(up_hist), cid[:20])
        return None

    # Up token prices at key moments
    up_t10 = _price_before(up_hist, close_time, 10)
    up_t5  = _price_before(up_hist, close_time, 5)
    up_t3  = _price_before(up_hist, close_time, 3)
    up_t1  = _price_before(up_hist, close_time, 1)
    up_t0  = _price_before(up_hist, close_time, 0)

    if up_t10 is None:
        logger.debug("No T-10 price for %s — skipping", cid[:20])
        return None

    # Down prices are 1 - Up (binary market, always sums to 1)
    dn_t10 = round(1.0 - up_t10, 4)
    dn_t5  = round(1.0 - up_t5,  4) if up_t5  is not None else None
    dn_t3  = round(1.0 - up_t3,  4) if up_t3  is not None else None

    # Signal at T-10: which outcome is >=0.95?
    threshold = 0.95
    signal_dir        = None
    signal_price_t10  = None
    signal_price_t5   = None
    signal_price_t3   = None

    if up_t10 >= threshold:
        signal_dir       = "Up"
        signal_price_t10 = round(up_t10, 4)
        signal_price_t5  = round(up_t5,  4) if up_t5  is not None else None
        signal_price_t3  = round(up_t3,  4) if up_t3  is not None else None
    elif dn_t10 >= threshold:
        signal_dir       = "Down"
        signal_price_t10 = dn_t10
        signal_price_t5  = dn_t5
        signal_price_t3  = dn_t3

    # Strategy simulations
    would_win_baseline = None
    would_win_2phase   = None
    signal_confirmed_t5 = None
    signal_confirmed_t3 = None

    if signal_dir is not None:
        would_win_baseline = (winner == signal_dir)

        signal_confirmed_t5 = (signal_price_t5 is not None and signal_price_t5 >= threshold)
        signal_confirmed_t3 = (signal_price_t3 is not None and signal_price_t3 >= threshold)

        # Two-phase: only execute if signal still holds at T-5
        if signal_confirmed_t5:
            would_win_2phase = would_win_baseline
        # else None = trade was skipped by two-phase filter

    return {
        "condition_id":       cid,
        "title":              market["title"],
        "outcome_winner":     winner,
        "close_time":         close_time.isoformat(),
        "price_up_t10":       round(up_t10, 4) if up_t10 is not None else None,
        "price_up_t5":        round(up_t5,  4) if up_t5  is not None else None,
        "price_up_t3":        round(up_t3,  4) if up_t3  is not None else None,
        "price_up_t1":        round(up_t1,  4) if up_t1  is not None else None,
        "price_up_t0":        round(up_t0,  4) if up_t0  is not None else None,
        "signal_direction":   signal_dir,
        "signal_price_t10":   signal_price_t10,
        "signal_price_t5":    signal_price_t5,
        "signal_price_t3":    signal_price_t3,
        "signal_confirmed_t5": signal_confirmed_t5,
        "signal_confirmed_t3": signal_confirmed_t3,
        "would_win_baseline": would_win_baseline,
        "would_win_2phase":   would_win_2phase,
    }


# ── Summary stats ─────────────────────────────────────────────────────────────

def _print_summary(rows: list[dict]) -> None:
    with_signal = [r for r in rows if r["signal_direction"] is not None]
    if not with_signal:
        print("\nNo markets with a signal at T-10 (>=0.95). Nothing to summarize.")
        return

    baseline = [r for r in with_signal if r["would_win_baseline"] is not None]
    b_wins   = sum(1 for r in baseline if r["would_win_baseline"])

    phase2_traded = [r for r in with_signal if r.get("signal_confirmed_t5")]
    phase2_wins   = sum(1 for r in phase2_traded if r["would_win_baseline"])
    phase2_skipped = [r for r in with_signal if not r.get("signal_confirmed_t5")]
    ps_losses = sum(1 for r in phase2_skipped if r["would_win_baseline"] is False)
    ps_wins   = sum(1 for r in phase2_skipped if r["would_win_baseline"] is True)

    print(f"\n{'='*65}")
    print(f"  BACKTEST SUMMARY -- {len(rows)} markets processed")
    print(f"{'='*65}")
    print(f"  Markets with signal at T-10 (>=0.95): {len(with_signal)}")
    print(f"  No signal at T-10:                    {len(rows) - len(with_signal)}")

    if baseline:
        wr_b = b_wins / len(baseline) * 100
        print(f"\n  BASELINE (trade all signals at T-10):")
        print(f"    Trades: {len(baseline)}  Wins: {b_wins}  Losses: {len(baseline)-b_wins}")
        print(f"    Win rate: {wr_b:.1f}%")

    if phase2_traded:
        wr_p = phase2_wins / len(phase2_traded) * 100
        print(f"\n  TWO-PHASE (confirm at T-5, skip if price fell below 0.95):")
        print(f"    Trades:  {len(phase2_traded)}  Wins: {phase2_wins}  Losses: {len(phase2_traded)-phase2_wins}")
        print(f"    Skipped: {len(phase2_skipped)}")
        print(f"    Win rate: {wr_p:.1f}%")
        print(f"\n    Of {len(phase2_skipped)} skipped by T-5 filter:")
        print(f"      Would have WON:  {ps_wins}")
        print(f"      Would have LOST: {ps_losses}")
        if len(phase2_skipped) > 0:
            skip_loss_rate = ps_losses / len(phase2_skipped) * 100
            print(f"      Loss rate in skipped: {skip_loss_rate:.1f}%  (want this HIGH -- means filter worked)")


# ── DB insert ────────────────────────────────────────────────────────────────

def _ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(_CREATE_TABLE)
    conn.commit()


def _insert_row(conn, row: dict):
    cols = list(_CSV_FIELDS) + ["token_id_up", "token_id_down"]
    vals = [row.get(c) for c in cols]
    ph   = ", ".join(["%s"] * len(cols))
    sql  = f"INSERT INTO backtest_markets ({', '.join(cols)}) VALUES ({ph}) ON CONFLICT (condition_id) DO NOTHING"
    with conn.cursor() as cur:
        cur.execute(sql, vals)
    conn.commit()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build historical backtest dataset")
    parser.add_argument("--days",    type=int, default=90,
                        help="Days of history to fetch (default: 90)")
    parser.add_argument("--out",     default="analysis/historical_dataset.csv",
                        help="Output CSV path")
    parser.add_argument("--no-db",   action="store_true",
                        help="Skip DB insert, write CSV only")
    parser.add_argument("--workers", type=int, default=10,
                        help="Parallel CLOB fetch workers (default: 10)")
    args = parser.parse_args()

    logger.info("Fetching Up/Down markets from last %d days...", args.days)
    markets = _fetch_gamma_markets(args.days)

    if not markets:
        logger.error("No markets found. Exiting.")
        sys.exit(1)

    logger.info("%d markets to process with %d workers.", len(markets), args.workers)

    conn = None
    if not args.no_db:
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=os.getenv("db_host"), port=os.getenv("db_port", 5432),
                dbname=os.getenv("db_name"), user=os.getenv("db_user"),
                password=os.getenv("db_password"),
            )
            _ensure_table(conn)
            logger.info("DB connected. Inserting into backtest_markets.")
        except Exception as e:
            logger.warning("DB connection failed (%s) -- CSV only mode", e)
            conn = None

    # ── Parallel fetch ────────────────────────────────────────────────────────
    # Each worker fetches CLOB price history for one market and sleeps _RATE_LIMIT_S.
    # With N workers we get N× throughput. Results arrive out of order; we sort
    # by close_time before writing so the CSV is chronological.
    completed = 0
    total = len(markets)
    results = []  # list of (market, row) tuples
    lock = threading.Lock()

    def _process(market: dict) -> Optional[tuple[dict, dict]]:
        row = _build_row(market)
        if row is None:
            return None
        row["token_id_up"]   = market["token_id_up"]
        row["token_id_down"] = market["token_id_down"]
        return (market, row)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_process, m): m for m in markets}
        for future in as_completed(futures):
            with lock:
                completed += 1
                if completed % 500 == 0 or completed == total:
                    logger.info("Progress: %d/%d (%.0f%%)", completed, total,
                                completed / total * 100)
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
            except Exception as e:
                market = futures[future]
                logger.warning("Worker error for %s: %s", market["condition_id"][:20], e)

    # Sort by close_time so CSV is chronological
    results.sort(key=lambda x: x[0]["close_time"])

    rows = []
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()

        for market, row in results:
            writer.writerow(row)
            rows.append(row)

            logger.info("  winner=%-4s  signal=%-4s  T10=%.3f  T5=%s  confirmed_T5=%s  baseline_win=%s",
                        row["outcome_winner"],
                        row["signal_direction"] or "none",
                        row["signal_price_t10"] or 0,
                        "{:.3f}".format(row["signal_price_t5"]) if row["signal_price_t5"] else "n/a ",
                        row["signal_confirmed_t5"],
                        row["would_win_baseline"])

            if conn:
                try:
                    _insert_row(conn, row)
                except Exception as e:
                    logger.warning("DB insert failed for %s: %s", market["condition_id"][:20], e)

    if conn:
        conn.close()

    logger.info("Done. %d rows written to %s", len(rows), args.out)
    _print_summary(rows)


if __name__ == "__main__":
    main()
