"""
regime_backtest.py
Backtest regime-detection metrics against the full yield_trades dataset.

Tests:
  1. DVOL threshold (already stored in DB) — likely won't work but let's confirm
  2. BTC 30-min realized vol (fetched from Binance public API per trade timestamp)
  3. Combined: realized vol + DVOL

Realized vol = annualised std dev of 1-min log returns over the 30 min before entry.
"""
import sys, os, time, math, statistics, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from core.database.connection import get_connection
from datetime import timezone

# ── 1. Load trades from VPS DB ──────────────────────────────────────────────
conn = get_connection()
cur = conn.cursor()
cur.execute("""
    SELECT id, status, btc_dvol, submitted_at, outcome, cost_usd, pnl_usd
    FROM yield_trades
    WHERE status IN ('won', 'lost', 'stopped')
    ORDER BY submitted_at ASC
""")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
conn.close()

all_trades = [dict(zip(cols, r)) for r in rows]
for t in all_trades:
    t['is_win'] = t['status'] == 'won'
    t['pnl'] = float(t['pnl_usd']) if t['pnl_usd'] else 0.0
    t['cost'] = float(t['cost_usd']) if t['cost_usd'] else 0.0

print(f"Loaded {len(all_trades)} trades from VPS DB")


# ── 2. Fetch BTC realized vol from Binance ───────────────────────────────────
BINANCE_KLINE_URL = "https://api.binance.com/api/v3/klines"
_vol_cache: dict[int, float] = {}  # ts_minute → realized_vol

def fetch_realized_vol(submitted_at, window_minutes: int = 30) -> float | None:
    """
    Compute annualised realized vol of BTC over the `window_minutes` before entry.
    Uses Binance 1-minute BTCUSDT klines.
    Returns None on API failure.
    """
    if submitted_at is None:
        return None

    # Normalize to UTC
    dt = submitted_at
    if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)

    # Cache key = minute-level timestamp
    import calendar
    ts_ms = int(calendar.timegm(dt.timetuple())) * 1000
    if ts_ms in _vol_cache:
        return _vol_cache[ts_ms]

    start_ms = ts_ms - window_minutes * 60 * 1000
    end_ms   = ts_ms

    try:
        resp = requests.get(
            BINANCE_KLINE_URL,
            params={
                "symbol": "BTCUSDT",
                "interval": "1m",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": window_minutes + 2,
            },
            timeout=10,
        )
        resp.raise_for_status()
        klines = resp.json()
    except Exception as e:
        print(f"  Binance API error for {dt}: {e}")
        return None

    if len(klines) < 5:
        return None

    closes = [float(k[4]) for k in klines]
    log_returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    if len(log_returns) < 2:
        return None

    # Annualised realized vol: std(log_returns) × sqrt(525600) [minutes per year]
    rv = statistics.stdev(log_returns) * math.sqrt(525600)
    _vol_cache[ts_ms] = rv
    return rv


# ── 3. Enrich trades with realized vol ──────────────────────────────────────
print(f"Fetching BTC 30-min realized vol from Binance for {len(all_trades)} trades...")
print("(rate-limiting to 1 req/0.3s to avoid 429)")

for i, t in enumerate(all_trades):
    rv = fetch_realized_vol(t['submitted_at'], window_minutes=30)
    t['realized_vol'] = rv
    if (i + 1) % 20 == 0:
        print(f"  {i+1}/{len(all_trades)} done...")
    time.sleep(0.3)

rv_ok = sum(1 for t in all_trades if t['realized_vol'] is not None)
print(f"Realized vol fetched for {rv_ok}/{len(all_trades)} trades\n")


# ── 4. Analysis helpers ───────────────────────────────────────────────────────
def ev_summary(trades, label):
    if not trades:
        print(f"  {label}: no trades")
        return
    wins = [t for t in trades if t['is_win']]
    losses = [t for t in trades if not t['is_win']]
    net = sum(t['pnl'] for t in trades)
    wr = len(wins) / len(trades)
    ev = net / len(trades)
    avg_win  = statistics.mean([t['pnl'] for t in wins])   if wins   else 0
    avg_loss = abs(statistics.mean([t['pnl'] for t in losses])) if losses else 0
    be = avg_loss / (avg_win + avg_loss) if (avg_win + avg_loss) > 0 else None
    flag = ""
    if be:
        flag = f"  ({'ABOVE' if wr > be else 'BELOW'} BE={be*100:.2f}%)"
    print(f"  {label}")
    print(f"    Trades={len(trades)} | W={len(wins)} L={len(losses)} | WR={wr*100:.2f}% | Net=${net:+.2f} | EV=${ev:+.4f}{flag}")


# ── 5. DVOL threshold test ───────────────────────────────────────────────────
print("=" * 70)
print("TEST 1: DVOL THRESHOLD")
print("=" * 70)
dvol_trades = [t for t in all_trades if t['btc_dvol'] is not None]
print(f"\nTrades with DVOL data: {len(dvol_trades)}")

losses_with_dvol = [t for t in dvol_trades if not t['is_win']]
print(f"\nDVOL at losses:")
for t in losses_with_dvol:
    print(f"  id={t['id']}  dvol={t['btc_dvol']:.2f}  dir={t['outcome']}  date={t['submitted_at'].strftime('%b %d')}")

print()
for threshold in [40, 45, 50, 55, 60, 65]:
    below = [t for t in dvol_trades if float(t['btc_dvol']) < threshold]
    ev_summary(below, f"DVOL < {threshold}")

print()
print("Baseline (all trades with DVOL data):")
ev_summary(dvol_trades, "All trades (DVOL present)")


# ── 6. Realized vol threshold test ───────────────────────────────────────────
print("\n" + "=" * 70)
print("TEST 2: BTC 30-MIN REALIZED VOL THRESHOLD")
print("=" * 70)
rv_trades = [t for t in all_trades if t['realized_vol'] is not None]
print(f"\nTrades with realized vol data: {len(rv_trades)}")

losses_rv = [t for t in rv_trades if not t['is_win']]
rvs = [t['realized_vol'] for t in rv_trades]
print(f"\nRealized vol stats (all trades): min={min(rvs):.3f}  max={max(rvs):.3f}  median={statistics.median(rvs):.3f}  mean={statistics.mean(rvs):.3f}")
print(f"\nRealized vol at losses:")
for t in losses_rv:
    print(f"  id={t['id']}  rv={t['realized_vol']:.4f}  dir={t['outcome']}  date={t['submitted_at'].strftime('%b %d %H:%M')}")

print()
for threshold in [0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.2, 1.5]:
    below = [t for t in rv_trades if t['realized_vol'] < threshold]
    ev_summary(below, f"Realized vol < {threshold:.1f}")

print()
print("Baseline (all trades with realized vol):")
ev_summary(rv_trades, "All (rv present)")


# ── 7. DVOL + realized vol combined ──────────────────────────────────────────
print("\n" + "=" * 70)
print("TEST 3: DVOL + REALIZED VOL COMBINED")
print("=" * 70)
both = [t for t in all_trades if t['btc_dvol'] is not None and t['realized_vol'] is not None]
print(f"\nTrades with both DVOL and realized vol: {len(both)}")
print()

for dvol_th, rv_th in [(55, 0.6), (55, 0.8), (60, 0.8), (60, 1.0), (65, 1.0)]:
    filtered = [t for t in both if float(t['btc_dvol']) < dvol_th and t['realized_vol'] < rv_th]
    ev_summary(filtered, f"DVOL < {dvol_th} AND rv < {rv_th:.1f}")


# ── 8. Summary table ─────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SUMMARY — best regime filters vs baseline")
print("=" * 70)
print(f"\n  {'Filter':<45} {'Trades':>7} {'WR%':>7} {'Net P&L':>10} {'EV/trade':>10}")
print(f"  {'─'*82}")

def row(label, trades):
    if not trades:
        return
    wins = sum(1 for t in trades if t['is_win'])
    net = sum(t['pnl'] for t in trades)
    wr = wins / len(trades) * 100
    ev = net / len(trades)
    print(f"  {label:<45} {len(trades):>7} {wr:>6.1f}% {net:>+10.2f} {ev:>+10.4f}")

row("Baseline (all)", all_trades)
row("DVOL < 50", [t for t in dvol_trades if float(t['btc_dvol']) < 50])
row("DVOL < 55", [t for t in dvol_trades if float(t['btc_dvol']) < 55])
row("DVOL < 60", [t for t in dvol_trades if float(t['btc_dvol']) < 60])

if rv_trades:
    for th in [0.6, 0.8, 1.0]:
        row(f"Realized vol < {th:.1f}", [t for t in rv_trades if t['realized_vol'] < th])

if both:
    row("DVOL < 55 + rv < 0.8", [t for t in both if float(t['btc_dvol']) < 55 and t['realized_vol'] < 0.8])
    row("DVOL < 60 + rv < 1.0", [t for t in both if float(t['btc_dvol']) < 60 and t['realized_vol'] < 1.0])

print()
