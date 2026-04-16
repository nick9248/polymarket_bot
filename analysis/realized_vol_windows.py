"""Test BTC price move over multiple longer windows before each loss."""
import requests
from datetime import datetime, timezone, timedelta

_DERIBIT_OHLC = "https://www.deribit.com/api/v2/public/get_tradingview_chart_data"

def fetch_btc(start_dt, end_dt):
    resp = requests.get(_DERIBIT_OHLC, params={
        "instrument_name": "BTC-PERPETUAL",
        "start_timestamp": int(start_dt.timestamp() * 1000),
        "end_timestamp": int(end_dt.timestamp() * 1000),
        "resolution": 60,
    }, timeout=15)
    r = resp.json().get("result", {})
    return [(datetime.fromtimestamp(t/1000, tz=timezone.utc), float(c))
            for t, c in zip(r.get("ticks", []), r.get("close", []))]

def move_pct(candles, at, window_hours):
    start = at - timedelta(hours=window_hours)
    pts = [(dt, p) for dt, p in candles if start <= dt <= at]
    if len(pts) < 2:
        return None
    return (pts[-1][1] - pts[0][1]) / pts[0][1] * 100

LOSSES = [
    ("Mar29 ETH+BTC Down", "2026-03-29T22:55:05Z"),
    ("Mar31 XRP Down",     "2026-03-31T18:59:27Z"),
    ("Apr01 BTC Down",     "2026-04-01T00:45:01Z"),
    ("Apr02 DOGE Down",    "2026-04-02T03:47:19Z"),
    ("Apr09 SOL Down",     "2026-04-09T09:45:06Z"),
    ("Apr09 BTC Down",     "2026-04-09T12:23:15Z"),
    ("Apr09 XRP Up",       "2026-04-09T22:51:07Z"),
    ("Apr10 BNB Down",     "2026-04-10T00:25:04Z"),
]

WINDOWS = [1, 2, 4, 8, 12, 24]

print(f"{'Trade':<22}", end="")
for w in WINDOWS:
    print(f"  {w}h".rjust(8), end="")
print()
print("-" * 78)

for label, ts_str in LOSSES:
    at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    candles = fetch_btc(at - timedelta(hours=25), at + timedelta(minutes=5))
    print(f"{label:<22}", end="")
    for w in WINDOWS:
        m = move_pct(candles, at, w)
        s = f"{m:+.1f}%" if m is not None else "N/A"
        print(f"{s:>8}", end="")
    print()

# Test thresholds: what % catches what
print()
print("Threshold sensitivity (how many of 8 losses caught):")
for w in [4, 8, 12, 24]:
    for thresh in [2.0, 3.0, 4.0, 5.0]:
        caught = 0
        for label, ts_str in LOSSES:
            at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            candles = fetch_btc(at - timedelta(hours=w+1), at + timedelta(minutes=5))
            m = move_pct(candles, at, w)
            if m is not None and abs(m) > thresh:
                caught += 1
        print(f"  {w}h >{thresh}%: {caught}/8 caught")
