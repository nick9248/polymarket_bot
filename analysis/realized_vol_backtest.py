"""
realized_vol_backtest.py

Tests whether a BTC realized volatility guard (monitoring BTC-PERPETUAL price
movement over a rolling window) would have fired before each of our 9 live losses.

Data source: Deribit BTC-PERPETUAL OHLC via get_tradingview_chart_data.
No local data needed — fetches directly from Deribit.

Usage:
    python analysis/realized_vol_backtest.py

Output:
    - For each loss: BTC % move in the N minutes before trade submission
    - Whether the guard would have fired (True/False) at each threshold
    - False positive check: how often the guard would have paused during winning periods
"""

import requests
from datetime import datetime, timezone, timedelta

_DERIBIT_OHLC = "https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
_RESOLUTION_MIN = 5  # 5-minute candles
_TIMEOUT = 15


# All 9 live losses: (id, title, direction, submitted_at UTC ISO)
LOSSES = [
    (9963,  "ETH Down  Mar 29 6:45PM ET",  "Down", "2026-03-29T22:55:05Z"),
    (9964,  "BTC Down  Mar 29 6:45PM ET",  "Down", "2026-03-29T22:55:06Z"),
    (10170, "XRP Down  Mar 31 2:45PM ET",  "Down", "2026-03-31T18:59:27Z"),
    (10200, "BTC Down  Mar 31 8PM ET",     "Down", "2026-04-01T00:45:01Z"),
    (10232, "DOGE Down Apr 1  11PM ET",    "Down", "2026-04-02T03:47:19Z"),
    (10484, "SOL Down  Apr 9  5AM ET",     "Down", "2026-04-09T09:45:06Z"),
    (10494, "BTC Down  Apr 9  8:15AM ET",  "Down", "2026-04-09T12:23:15Z"),
    (10524, "XRP Up    Apr 9  6PM ET",     "Up",   "2026-04-09T22:51:07Z"),
    (10525, "BNB Down  Apr 9  8:15PM ET",  "Down", "2026-04-10T00:25:04Z"),
]

# Thresholds to test: (window_minutes, pct_threshold, pause_hours)
GUARDS = [
    (30, 2.0),
    (30, 3.0),
    (30, 5.0),
    (60, 3.0),
    (60, 5.0),
]


def fetch_btc_candles(start_dt: datetime, end_dt: datetime) -> list[tuple[datetime, float]]:
    """Fetch BTC-PERPETUAL 5-min close prices from Deribit."""
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    resp = requests.get(
        _DERIBIT_OHLC,
        params={
            "instrument_name": "BTC-PERPETUAL",
            "start_timestamp": start_ms,
            "end_timestamp": end_ms,
            "resolution": _RESOLUTION_MIN,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json().get("result", {})
    ticks = result.get("ticks", [])
    closes = result.get("close", [])
    return [
        (datetime.fromtimestamp(t / 1000, tz=timezone.utc), float(c))
        for t, c in zip(ticks, closes)
    ]


def btc_move_pct(candles: list[tuple[datetime, float]], at: datetime, window_min: int) -> float | None:
    """
    Calculate BTC % price change in the `window_min` minutes ending at `at`.
    Returns None if not enough data.
    """
    window_start = at - timedelta(minutes=window_min)
    in_window = [(dt, p) for dt, p in candles if window_start <= dt <= at]
    if len(in_window) < 2:
        return None
    price_start = in_window[0][1]
    price_end = in_window[-1][1]
    return (price_end - price_start) / price_start * 100


def main():
    print("=" * 72)
    print("BTC REALIZED VOL GUARD — BACKTEST AGAINST ALL 9 LOSSES")
    print("=" * 72)

    # ── Per-loss analysis ────────────────────────────────────────────────────
    results = []
    for loss_id, label, direction, ts_str in LOSSES:
        submitted = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        fetch_start = submitted - timedelta(hours=2)
        fetch_end = submitted + timedelta(minutes=15)

        print(f"\n{'─'*60}")
        print(f"Trade {loss_id}: {label} | {submitted.strftime('%Y-%m-%d %H:%M UTC')}")

        try:
            candles = fetch_btc_candles(fetch_start, fetch_end)
        except Exception as e:
            print(f"  ERROR fetching data: {e}")
            continue

        if not candles:
            print("  No candle data available.")
            continue

        price_at_trade = next((p for dt, p in candles if dt <= submitted), None)
        print(f"  BTC price at submission: ${price_at_trade:,.0f}" if price_at_trade else "  BTC price: N/A")

        row = {"id": loss_id, "label": label, "direction": direction}
        for window_min, threshold in GUARDS:
            move = btc_move_pct(candles, submitted, window_min)
            fired = abs(move) > threshold if move is not None else False
            row[f"{window_min}m_{threshold}%"] = (move, fired)
            print(f"  {window_min:2d}-min move: {move:+.2f}%  |  guard >{threshold}%: {'🔴 FIRED' if fired else '⚪ miss'}")
        results.append(row)

    # ── Summary table ────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("SUMMARY — guard catch rate per threshold")
    print(f"{'='*72}")
    print(f"{'Guard':<20} {'Caught':>7} {'Missed':>7} {'Catch%':>8}")
    print(f"{'─'*50}")
    for window_min, threshold in GUARDS:
        key = f"{window_min}m_{threshold}%"
        caught = sum(1 for r in results if key in r and r[key][1])
        missed = sum(1 for r in results if key in r and not r[key][1])
        total = caught + missed
        pct = caught / total * 100 if total else 0
        print(f"{key:<20} {caught:>7} {missed:>7} {pct:>7.0f}%")

    # ── False positive check: sample winning hours ───────────────────────────
    print(f"\n{'='*72}")
    print("FALSE POSITIVE CHECK — normal winning periods (should NOT fire)")
    print(f"{'='*72}")

    winning_samples = [
        ("Apr 7 10:00 UTC (quiet day)", "2026-04-07T10:00:00Z"),
        ("Apr 8 14:00 UTC (quiet day)", "2026-04-08T14:00:00Z"),
        ("Apr 9 08:00 UTC (pre-tariff)", "2026-04-09T08:00:00Z"),
        ("Apr 9 07:00 UTC (pre-tariff)", "2026-04-09T07:00:00Z"),
        ("Apr 10 05:00 UTC (post-event)", "2026-04-10T05:00:00Z"),
        ("Mar 30 12:00 UTC (post-loss)", "2026-03-30T12:00:00Z"),
    ]

    for label, ts_str in winning_samples:
        check_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        try:
            candles = fetch_btc_candles(check_dt - timedelta(hours=1, minutes=30), check_dt + timedelta(minutes=5))
        except Exception as e:
            print(f"  {label}: ERROR {e}")
            continue
        print(f"\n  {label}")
        for window_min, threshold in [(30, 3.0), (60, 3.0)]:
            move = btc_move_pct(candles, check_dt, window_min)
            fired = abs(move) > threshold if move is not None else False
            print(f"    {window_min}m move: {move:+.2f}%  |  guard >{threshold}%: {'🔴 FALSE POSITIVE' if fired else '✅ ok'}")

    print(f"\n{'='*72}")
    print("Done.")


if __name__ == "__main__":
    main()
