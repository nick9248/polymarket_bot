"""
price_flip_analysis.py

For each of the 4 April 9 losses, reconstructs the minute-by-minute price
of the underlying asset using Deribit perpetuals, then answers:

  - What was the reference price at market open?
  - At which minute did the asset cross back over (flipping the outcome)?
  - How much time remained when the flip happened?
  - Is the flip early enough to detect and cancel?

Market reference price logic (Polymarket Up/Down):
  - Market opens at time T0, closes at T1
  - Reference price = asset price at T0
  - "Up" wins if price at T1 > reference
  - "Down" wins if price at T1 < reference
  - The "Down" market price on CLOB = P(price_T1 < reference)
"""

import requests
from datetime import datetime, timezone, timedelta

_DERIBIT_OHLC = "https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
_TIMEOUT = 15


def fetch_candles(instrument: str, start_dt: datetime, end_dt: datetime, resolution: int = 1):
    """Fetch minute-level OHLC from Deribit perpetual."""
    resp = requests.get(_DERIBIT_OHLC, params={
        "instrument_name": instrument,
        "start_timestamp": int(start_dt.timestamp() * 1000),
        "end_timestamp": int(end_dt.timestamp() * 1000),
        "resolution": resolution,
    }, timeout=_TIMEOUT)
    r = resp.json().get("result", {})
    ticks = r.get("ticks", [])
    closes = r.get("close", [])
    if not ticks:
        err = resp.json().get("error", {})
        print(f"    No data for {instrument}: {err}")
        return []
    return [(datetime.fromtimestamp(t/1000, tz=timezone.utc), float(c))
            for t, c in zip(ticks, closes)]


def get_price_at(candles, target_dt):
    """Get closest price to target_dt."""
    if not candles:
        return None
    closest = min(candles, key=lambda x: abs((x[0] - target_dt).total_seconds()))
    return closest[1]


# ── Loss definitions ─────────────────────────────────────────────────────────
# For each loss: instrument, market open (T0), market close (T1), our entry, outcome bet
# "Down" = we bought the outcome that wins if price_T1 < price_T0

LOSSES = [
    {
        "id": 10484,
        "label": "Solana Down | 5AM–6AM ET (1hr market)",
        "instrument": "SOL_USDC-PERPETUAL",
        "market_open":  datetime(2026, 4, 9,  9,  0, 0, tzinfo=timezone.utc),  # 5AM ET
        "market_close": datetime(2026, 4, 9, 10,  0, 0, tzinfo=timezone.utc),  # 6AM ET
        "our_entry":    datetime(2026, 4, 9,  9, 45, 6, tzinfo=timezone.utc),
        "bet": "Down",  # we win if close < open price
        "signal_price": 0.97,
    },
    {
        "id": 10494,
        "label": "Bitcoin Down | 8:15–8:30AM ET (15min market)",
        "instrument": "BTC-PERPETUAL",
        "market_open":  datetime(2026, 4, 9, 12, 15, 0, tzinfo=timezone.utc),  # 8:15AM ET
        "market_close": datetime(2026, 4, 9, 12, 30, 0, tzinfo=timezone.utc),  # 8:30AM ET
        "our_entry":    datetime(2026, 4, 9, 12, 23, 15, tzinfo=timezone.utc),
        "bet": "Down",
        "signal_price": 0.98,
    },
    {
        "id": 10524,
        "label": "XRP Up | 6PM–7PM ET (1hr market)",
        "instrument": "XRP_USDC-PERPETUAL",
        "market_open":  datetime(2026, 4, 9, 22,  0, 0, tzinfo=timezone.utc),  # 6PM ET
        "market_close": datetime(2026, 4, 9, 23,  0, 0, tzinfo=timezone.utc),  # 7PM ET
        "our_entry":    datetime(2026, 4, 9, 22, 51, 7, tzinfo=timezone.utc),
        "bet": "Up",  # we win if close > open price
        "signal_price": 0.958,
    },
    {
        "id": 10525,
        "label": "BNB Down | 8:15–8:30PM ET (15min market)",
        "instrument": "BNB-PERPETUAL",
        "market_open":  datetime(2026, 4, 10,  0, 15, 0, tzinfo=timezone.utc),  # 8:15PM ET
        "market_close": datetime(2026, 4, 10,  0, 30, 0, tzinfo=timezone.utc),  # 8:30PM ET
        "our_entry":    datetime(2026, 4, 10,  0, 25, 4, tzinfo=timezone.utc),
        "bet": "Down",
        "signal_price": 0.96,
    },
]


def analyze_loss(loss: dict):
    print(f"\n{'='*65}")
    print(f"Trade {loss['id']}: {loss['label']}")
    print(f"  Bet: {loss['bet']} @ {loss['signal_price']} | Entry: {loss['our_entry'].strftime('%H:%M:%S UTC')}")
    print(f"  Window: {loss['market_open'].strftime('%H:%M')} → {loss['market_close'].strftime('%H:%M')} UTC")

    # Fetch 10min before open through close (for reference price context)
    fetch_start = loss["market_open"] - timedelta(minutes=10)
    fetch_end = loss["market_close"] + timedelta(minutes=2)
    candles = fetch_candles(loss["instrument"], fetch_start, fetch_end, resolution=1)

    if not candles:
        # Try alt instrument name (e.g. SOL_USDC-PERPETUAL → SOL-PERPETUAL)
        alt = loss["instrument"].replace("_USDC-PERPETUAL", "-PERPETUAL").replace("_USDC", "")
        print(f"  Trying alt instrument: {alt}")
        candles = fetch_candles(alt, fetch_start, fetch_end, resolution=1)

    if not candles:
        print("  ❌ No price data available for this asset on Deribit")
        return None

    # Reference price = asset price at market open
    ref_price = get_price_at(candles, loss["market_open"])
    close_price = get_price_at(candles, loss["market_close"])
    entry_price = get_price_at(candles, loss["our_entry"])

    if ref_price is None:
        print("  ❌ Could not get reference price")
        return None

    print(f"\n  Reference price (market open): ${ref_price:,.4f}")
    print(f"  Price at our entry:            ${entry_price:,.4f}" if entry_price else "  Entry price: N/A")
    print(f"  Price at market close:         ${close_price:,.4f}" if close_price else "  Close price: N/A")

    # Determine outcome: did we correctly predict direction?
    bet_would_win = (close_price > ref_price) if loss["bet"] == "Up" else (close_price < ref_price)
    pct_move = (close_price - ref_price) / ref_price * 100 if close_price else None
    print(f"  Total move in window:          {pct_move:+.3f}%" if pct_move else "")
    print(f"  Outcome: {'✅ Should have WON' if bet_would_win else '❌ LOST (as recorded)'}")

    # Track minute-by-minute whether our bet was winning or losing
    print(f"\n  Minute-by-minute vs reference ${ref_price:,.4f}:")
    print(f"  {'Time':>8}  {'Price':>10}  {'vs Ref':>8}  {'Our bet':>8}  {'Winning?':>10}  {'Mins left':>10}")
    print(f"  {'-'*70}")

    flip_time = None
    was_winning = True  # assume we entered when winning

    market_candles = [(dt, p) for dt, p in candles
                      if loss["market_open"] <= dt <= loss["market_close"]]

    for dt, price in market_candles:
        vs_ref_pct = (price - ref_price) / ref_price * 100
        currently_winning = (price > ref_price) if loss["bet"] == "Up" else (price < ref_price)
        mins_left = (loss["market_close"] - dt).total_seconds() / 60

        # Mark flip point (first time we go from winning→losing after entry)
        if dt >= loss["our_entry"] and was_winning and not currently_winning and flip_time is None:
            flip_time = dt

        was_winning_marker = "✅" if currently_winning else "❌"
        entry_marker = " ← ENTRY" if abs((dt - loss["our_entry"]).total_seconds()) < 90 else ""
        flip_marker = " ← FLIP" if flip_time == dt else ""

        # Only print key minutes: open, every 5 min, entry, flip, close
        is_key = (
            dt == market_candles[0][0] or
            dt.minute % 5 == 0 or
            abs((dt - loss["our_entry"]).total_seconds()) < 90 or
            flip_time == dt or
            dt == market_candles[-1][0]
        )
        if is_key:
            print(f"  {dt.strftime('%H:%M'):>8}  ${price:>9,.2f}  {vs_ref_pct:>+7.3f}%  {loss['bet']:>8}  {was_winning_marker:>10}  {mins_left:>8.1f}m{entry_marker}{flip_marker}")

        was_winning = currently_winning

    # Summary
    print()
    if flip_time:
        mins_at_flip = (loss["market_close"] - flip_time).total_seconds() / 60
        mins_since_entry = (flip_time - loss["our_entry"]).total_seconds() / 60
        print(f"  🔴 FLIP DETECTED at {flip_time.strftime('%H:%M UTC')}")
        print(f"     {mins_at_flip:.1f} min before market close")
        print(f"     {mins_since_entry:.1f} min after our entry")
        print(f"     We had {loss['minutes_to_close'] if 'minutes_to_close' in loss else '?'} min to close at entry")
        return {"id": loss["id"], "flip_mins_before_close": mins_at_flip, "mins_after_entry": mins_since_entry}
    else:
        # Check if we were already losing at entry
        entry_price_val = get_price_at(candles, loss["our_entry"])
        already_losing = not ((entry_price_val > ref_price) if loss["bet"] == "Up" else (entry_price_val < ref_price))
        if already_losing:
            print(f"  🔴 Already losing at entry — flipped BEFORE we entered!")
        else:
            print(f"  ⚠️  No single clear flip point detected (may have oscillated)")
        return {"id": loss["id"], "flip_mins_before_close": None, "already_losing_at_entry": already_losing}


def main():
    print("PRICE FLIP ANALYSIS — 4 APRIL 9 LOSSES")
    print("Reconstructing minute-by-minute asset price vs market reference")

    flip_results = []
    for loss in LOSSES:
        r = analyze_loss(loss)
        if r:
            flip_results.append(r)

    # Cluster summary
    print(f"\n{'='*65}")
    print("CLUSTER SUMMARY")
    print(f"{'='*65}")
    for r in flip_results:
        loss = next(l for l in LOSSES if l["id"] == r["id"])
        if r.get("flip_mins_before_close") is not None:
            print(f"  Trade {r['id']} ({loss['bet']:4s}): flip at {r['flip_mins_before_close']:.1f}m before close, "
                  f"{r['mins_after_entry']:.1f}m after entry")
        elif r.get("already_losing_at_entry"):
            print(f"  Trade {r['id']} ({loss['bet']:4s}): already losing at entry (no cancel window)")
        else:
            print(f"  Trade {r['id']} ({loss['bet']:4s}): no clear flip detected")

    detectable = [r for r in flip_results if r.get("flip_mins_before_close", 0) and r["flip_mins_before_close"] > 1.0]
    print(f"\n  Potentially cancellable (flip >1min before close): {len(detectable)}/{len(flip_results)}")


if __name__ == "__main__":
    main()
