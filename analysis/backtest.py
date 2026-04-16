"""
backtest.py
Simulates yield farming strategy variants against resolved trade data in the DB.

Run from project root:
    python -m analysis.backtest

Each strategy is a filter function applied to the ordered trade list.
Trades that are filtered out are counted as 'skipped' (no P&L impact).
The baseline is 'execute everything' (current behaviour).

Strategies tested:
  1. Baseline — no filters
  2. Direction momentum — skip if last 2 resolved trades for same asset went opposite direction
  3. Session hours — skip trades with close time after 8PM ET or before 9:30AM ET
  4. Higher threshold — only execute if signal_price >= 0.97
  5. Combined — momentum + session hours
  6. Combined + threshold — all three filters
"""

import csv
import os
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "yield_trades_resolved.csv")


def _parse_row(row: dict) -> dict:
    """Coerce CSV string values to appropriate Python types."""
    for field in ("signal_price", "fill_price", "cost_usd", "pnl_usd",
                  "minutes_to_close", "gamma_clob_spread", "btc_dvol", "btc_iv_percentile"):
        raw = row.get(field, "")
        row[field] = float(raw) if raw else None

    ts_raw = row.get("submitted_at", "")
    if ts_raw:
        ts_raw = ts_raw.replace(" ", "T")
        if ts_raw.endswith("+00"):
            ts_raw += ":00"
        row["submitted_at"] = datetime.fromisoformat(ts_raw)
        if row["submitted_at"].tzinfo is None:
            row["submitted_at"] = row["submitted_at"].replace(tzinfo=timezone.utc)
    return row


def _load_trades(csv_path: str = _DEFAULT_CSV) -> list[dict]:
    """Load resolved trades from a CSV export. Falls back to DB if no CSV found."""
    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            return [_parse_row(row) for row in csv.DictReader(f)]

    # DB fallback (requires VPS tunnel or local Postgres)
    import psycopg2
    conn = psycopg2.connect(
        host=os.getenv("db_host"), port=os.getenv("db_port", 5432),
        dbname=os.getenv("db_name"), user=os.getenv("db_user"),
        password=os.getenv("db_password"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, outcome, signal_price, fill_price,
                       cost_usd, pnl_usd, status,
                       minutes_to_close, gamma_clob_spread,
                       btc_dvol, btc_iv_percentile, submitted_at
                FROM yield_trades
                WHERE status IN ('won', 'lost')
                ORDER BY submitted_at ASC
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ── Asset extraction ──────────────────────────────────────────────────────────

_ASSET_KEYWORDS = {
    "Bitcoin": ["Bitcoin", "BTC"],
    "Ethereum": ["Ethereum", "ETH"],
    "XRP": ["XRP"],
    "Solana": ["Solana", "SOL"],
    "Dogecoin": ["Dogecoin", "DOGE"],
    "BNB": ["BNB"],
    "Hyperliquid": ["Hyperliquid", "HYPE"],
}


def _asset_from_title(title: str) -> str:
    for asset, keywords in _ASSET_KEYWORDS.items():
        if any(k.lower() in title.lower() for k in keywords):
            return asset
    return "Other"


# ── ET close-hour extraction ──────────────────────────────────────────────────

_ET_OFFSET = timedelta(hours=-4)  # EDT (UTC-4); use -5 for EST. Close enough for filter.


def _et_hour(submitted_at: datetime) -> float:
    """Return ET hour (decimal) from a UTC timestamp."""
    et = submitted_at + _ET_OFFSET
    return et.hour + et.minute / 60.0


# ── Strategy filters ──────────────────────────────────────────────────────────

def _filter_none(_trade: dict, _state: dict) -> bool:
    """Baseline: execute everything."""
    return True


def _filter_momentum(trade: dict, state: dict) -> bool:
    """
    Skip if the last 2 resolved trades for the same asset both went in the
    OPPOSITE direction to this trade's outcome.

    e.g. trade says "Down" but last 2 BTC outcomes were both "Up" → skip.
    """
    asset = _asset_from_title(trade["title"])
    history: deque = state["asset_history"][asset]
    if len(history) >= 2:
        opposite = [h for h in list(history)[-2:] if h != trade["outcome"]]
        if len(opposite) == 2:
            return False  # both recent outcomes were opposite → skip
    return True


def _filter_session(trade: dict, _state: dict) -> bool:
    """
    Skip markets whose close time is outside regular trading hours:
    9:30AM–8:00PM ET.
    Markets are submitted roughly at close time, so submitted_at is a proxy.
    """
    hour_et = _et_hour(trade["submitted_at"])
    return 9.5 <= hour_et <= 20.0


def _filter_threshold_97(trade: dict, _state: dict) -> bool:
    """Skip if signal_price < 0.97."""
    price = float(trade["signal_price"] or 0)
    return price >= 0.97


def _filter_threshold_975(trade: dict, _state: dict) -> bool:
    """Skip if signal_price < 0.975."""
    price = float(trade["signal_price"] or 0)
    return price >= 0.975


def _filter_combined_momentum_session(trade: dict, state: dict) -> bool:
    return _filter_momentum(trade, state) and _filter_session(trade, state)


def _filter_all(trade: dict, state: dict) -> bool:
    return (
        _filter_momentum(trade, state)
        and _filter_session(trade, state)
        and _filter_threshold_97(trade, state)
    )


# ── Simulation engine ─────────────────────────────────────────────────────────

def _simulate(trades: list[dict], filter_fn) -> dict:
    """
    Apply a filter function to each trade in chronological order.
    Returns P&L stats.
    """
    state = {"asset_history": defaultdict(lambda: deque(maxlen=5))}

    wins = 0
    losses = 0
    skipped = 0
    total_pnl = 0.0
    total_invested = 0.0
    win_pnl = 0.0     # gross profit on wins only
    loss_pnl = 0.0    # gross loss on losses only (stored as positive number)
    max_consecutive_loss = 0
    cur_consecutive_loss = 0
    peak_pnl = 0.0
    max_drawdown = 0.0

    for trade in trades:
        asset = _asset_from_title(trade["title"])
        execute = filter_fn(trade, state)

        # Always update history AFTER deciding, so we don't use the current trade
        # to filter itself — only past outcomes inform the filter.
        outcome_after = trade["outcome"]
        cost = float(trade["cost_usd"] or 0)
        pnl = float(trade["pnl_usd"] or 0)
        won = trade["status"] == "won"

        if execute:
            total_pnl += pnl
            total_invested += cost

            if won:
                wins += 1
                win_pnl += pnl
                cur_consecutive_loss = 0
            else:
                losses += 1
                loss_pnl += abs(pnl)
                cur_consecutive_loss += 1
                max_consecutive_loss = max(max_consecutive_loss, cur_consecutive_loss)

            # Track drawdown
            peak_pnl = max(peak_pnl, total_pnl)
            dd = peak_pnl - total_pnl
            max_drawdown = max(max_drawdown, dd)
        else:
            skipped += 1

        # Update asset history unconditionally (history reflects what happened in the
        # market regardless of whether we traded it).
        state["asset_history"][asset].append(outcome_after)

    total_resolved = wins + losses
    win_rate = wins / total_resolved if total_resolved > 0 else 0.0
    roi = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0

    # Break-even win rate: p* = avg_loss / (avg_win + avg_loss)
    avg_win  = win_pnl  / wins   if wins   > 0 else 0.0
    avg_loss = loss_pnl / losses if losses > 0 else 0.0
    be_win_rate = avg_loss / (avg_win + avg_loss) if (avg_win + avg_loss) > 0 else None

    return {
        "wins": wins,
        "losses": losses,
        "skipped": skipped,
        "total_invested": round(total_invested, 2),
        "total_pnl": round(total_pnl, 4),
        "win_rate": round(win_rate * 100, 1),
        "roi_pct": round(roi, 3),
        "max_consecutive_loss": max_consecutive_loss,
        "max_drawdown": round(max_drawdown, 2),
        "break_even_win_rate": round(be_win_rate * 100, 1) if be_win_rate else None,
    }


# ── Per-trade detail for the momentum filter ─────────────────────────────────

def _show_momentum_detail(trades: list[dict]) -> None:
    """Print which trades the momentum filter would have skipped."""
    state = {"asset_history": defaultdict(lambda: deque(maxlen=5))}
    print("\n  Momentum filter — trade-by-trade:")
    print(f"  {'ID':>6}  {'Asset':12} {'Dir':5} {'Price':6} {'Result':6} {'Action':8}  {'History'}")
    print("  " + "-" * 75)
    for trade in trades:
        asset = _asset_from_title(trade["title"])
        history = list(state["asset_history"][asset])
        execute = _filter_momentum(trade, state)
        action = "TRADE " if execute else "SKIP  "
        result = "WON  " if trade["status"] == "won" else "LOST "
        pnl = float(trade["pnl_usd"] or 0)
        pnl_str = f"+{pnl:.2f}" if pnl > 0 else f"{pnl:.2f}"
        hist_str = str(history[-2:]) if history else "[]"
        print(f"  {trade['id']:>6}  {asset:12} {trade['outcome']:5} "
              f"{float(trade['signal_price']):.3f}  {result} {action}  last={hist_str} -> {pnl_str}")
        state["asset_history"][asset].append(trade["outcome"])


# ── Main ──────────────────────────────────────────────────────────────────────

STRATEGIES = [
    ("Baseline (no filter)",                _filter_none),
    ("Momentum only",                        _filter_momentum),
    ("Session hours only (9:30AM-8PM ET)",   _filter_session),
    ("Threshold >= 0.970 only",              _filter_threshold_97),
    ("Threshold >= 0.975 only",             _filter_threshold_975),
    ("Momentum + Session hours",             _filter_combined_momentum_session),
    ("Momentum + Session + Threshold 0.97", _filter_all),
]


def _fmt(val, fmt=".2f", prefix="$"):
    if val is None:
        return "  n/a"
    if isinstance(val, float):
        return f"{prefix}{val:{fmt}}"
    return str(val)


def main():
    print("Loading resolved trades from DB...")
    trades = _load_trades()
    print(f"Loaded {len(trades)} resolved trades "
          f"({sum(1 for t in trades if t['status']=='won')} won, "
          f"{sum(1 for t in trades if t['status']=='lost')} lost)\n")

    # Header
    col_w = [36, 5, 5, 7, 10, 8, 6, 8, 9, 10]
    headers = ["Strategy", "W", "L", "Skip", "PNL", "WinRate", "ROI%", "MaxCL", "MaxDD", "BE Rate"]
    header_line = "  ".join(h.ljust(col_w[i]) for i, h in enumerate(headers))
    sep = "-" * len(header_line)
    print(header_line)
    print(sep)

    results = {}
    for name, fn in STRATEGIES:
        r = _simulate(trades, fn)
        results[name] = r
        row = [
            name,
            str(r["wins"]),
            str(r["losses"]),
            str(r["skipped"]),
            f"${r['total_pnl']:+.2f}",
            f"{r['win_rate']}%",
            f"{r['roi_pct']:+.3f}%",
            str(r["max_consecutive_loss"]),
            f"${r['max_drawdown']:.2f}",
            f"{r['break_even_win_rate']}%" if r['break_even_win_rate'] else "n/a",
        ]
        print("  ".join(str(v).ljust(col_w[i]) for i, v in enumerate(row)))

    print(sep)

    # Show detail for the most impactful single filter
    print("\n\n--- Momentum filter: which specific trades get skipped? ---")
    _show_momentum_detail(trades)

    print("\n\n--- Session hours filter: losses by ET hour ---")
    et_hour_data = [
        (trade, _et_hour(trade["submitted_at"]))
        for trade in trades if trade["status"] == "lost"
    ]
    print(f"  {'ID':>6}  {'ET Hour':10} {'Title'[:50]:50}  {'Loss':>7}")
    for trade, hour in sorted(et_hour_data, key=lambda x: x[1]):
        h = int(hour)
        m = int((hour - h) * 60)
        print(f"  {trade['id']:>6}  {h:02d}:{m:02d} ET    {trade['title'][:50]:50}  ${float(trade['pnl_usd']):.2f}")

    print(f"\n  Total trades after 8PM ET (20:00): "
          f"{sum(1 for t in trades if _et_hour(t['submitted_at']) > 20.0)}")
    print(f"  Total losses after 8PM ET: "
          f"{sum(1 for t in trades if t['status']=='lost' and _et_hour(t['submitted_at']) > 20.0)}")


if __name__ == "__main__":
    main()
