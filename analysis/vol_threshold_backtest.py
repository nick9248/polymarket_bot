"""
vol_threshold_backtest.py
Deep analysis of realized vol as a trade filter.

1. Realized vol vs P&L correlation — bucket analysis to find optimal threshold
2. Up only + realized vol threshold combinations
3. Cooldown after first loss (user expects this won't help — let's confirm)

Relies on realized_vol already computed and cached by regime_backtest.py.
Re-fetches from Binance if cache is empty (rate-limited).
"""
import sys, os, time, math, statistics, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from core.database.connection import get_connection

# ── 1. Load trades ───────────────────────────────────────────────────────────
conn = get_connection()
cur = conn.cursor()
cur.execute("""
    SELECT id, status, btc_dvol, submitted_at, outcome, cost_usd, pnl_usd, signal_price
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
    t['pnl']    = float(t['pnl_usd'])  if t['pnl_usd']  else 0.0
    t['cost']   = float(t['cost_usd']) if t['cost_usd'] else 0.0

# ── 2. Fetch realized vol (Binance 1m klines, 30-min window) ─────────────────
BINANCE_URL = "https://api.binance.com/api/v3/klines"
_cache: dict = {}

def fetch_rv(submitted_at, window_minutes=30):
    import calendar
    dt = submitted_at
    if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    ts_ms = int(calendar.timegm(dt.timetuple())) * 1000
    if ts_ms in _cache:
        return _cache[ts_ms]
    start_ms = ts_ms - window_minutes * 60 * 1000
    try:
        resp = requests.get(BINANCE_URL, params={
            "symbol": "BTCUSDT", "interval": "1m",
            "startTime": start_ms, "endTime": ts_ms, "limit": window_minutes + 2,
        }, timeout=10)
        resp.raise_for_status()
        klines = resp.json()
    except Exception as e:
        return None
    if len(klines) < 5:
        return None
    closes = [float(k[4]) for k in klines]
    log_ret = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
    if len(log_ret) < 2:
        return None
    rv = statistics.stdev(log_ret) * math.sqrt(525600)
    _cache[ts_ms] = rv
    return rv

print("Fetching realized vol for all trades...")
for i, t in enumerate(all_trades):
    t['rv'] = fetch_rv(t['submitted_at'])
    if (i + 1) % 30 == 0:
        print(f"  {i+1}/{len(all_trades)}")
    time.sleep(0.3)

rv_trades = [t for t in all_trades if t['rv'] is not None]
print(f"Done. {len(rv_trades)}/{len(all_trades)} trades have rv.\n")


# ── Helpers ───────────────────────────────────────────────────────────────────
def stats(trades, label=""):
    if not trades:
        return
    wins   = [t for t in trades if t['is_win']]
    losses = [t for t in trades if not t['is_win']]
    net    = sum(t['pnl'] for t in trades)
    wr     = len(wins) / len(trades)
    ev     = net / len(trades)
    avg_w  = statistics.mean([t['pnl'] for t in wins])   if wins   else 0
    avg_l  = abs(statistics.mean([t['pnl'] for t in losses])) if losses else 0
    be     = avg_l / (avg_w + avg_l) if (avg_w + avg_l) > 0 else None
    flag   = f"  ({'▲ ABOVE' if be and wr > be else '▼ BELOW'} BE={be*100:.2f}%)" if be else ""
    print(f"  {label}")
    print(f"    Trades={len(trades):>4}  W={len(wins):>3}  L={len(losses):>2}  "
          f"WR={wr*100:>5.2f}%  Net=${net:>+8.2f}  EV=${ev:>+7.4f}{flag}")


# ════════════════════════════════════════════════════════════════════════════
# TEST A — Realized vol vs P&L: bucket analysis
# ════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("A. REALIZED VOL BUCKET ANALYSIS — win rate, avg P&L, EV per bucket")
print("=" * 70)

buckets = [
    (0.00, 0.20, "0.00–0.20"),
    (0.20, 0.30, "0.20–0.30"),
    (0.30, 0.40, "0.30–0.40"),
    (0.40, 0.50, "0.40–0.50"),
    (0.50, 0.60, "0.50–0.60"),
    (0.60, 0.70, "0.60–0.70"),
    (0.70, 0.80, "0.70–0.80"),
    (0.80, 1.00, "0.80–1.00"),
    (1.00, 1.60, "1.00–1.60"),
]

print(f"\n  {'RV bucket':12}  {'N':>5}  {'Wins':>5}  {'Loss':>5}  {'WR%':>7}  {'Avg P&L':>9}  {'EV/trade':>10}  Losses?")
print(f"  {'─'*78}")
for lo, hi, label in buckets:
    b = [t for t in rv_trades if lo <= t['rv'] < hi]
    if not b:
        print(f"  {label:12}  {'—':>5}")
        continue
    wins   = [t for t in b if t['is_win']]
    losses = [t for t in b if not t['is_win']]
    wr     = len(wins) / len(b) * 100
    avg_pnl = statistics.mean([t['pnl'] for t in b])
    ev      = sum(t['pnl'] for t in b) / len(b)
    loss_ids = [str(t['id']) for t in losses]
    print(f"  {label:12}  {len(b):>5}  {len(wins):>5}  {len(losses):>5}  {wr:>6.1f}%  {avg_pnl:>+9.4f}  {ev:>+10.4f}  {','.join(loss_ids) if loss_ids else '—'}")

# Cumulative: EV for rv < threshold at fine granularity
print(f"\n  CUMULATIVE: trades taken below rv threshold")
print(f"  {'RV < ':12}  {'N':>5}  {'WR%':>7}  {'Net':>10}  {'EV/trade':>10}")
print(f"  {'─'*50}")
thresholds = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80, 1.00, 1.50]
for th in thresholds:
    b = [t for t in rv_trades if t['rv'] < th]
    if not b:
        continue
    wins = [t for t in b if t['is_win']]
    net  = sum(t['pnl'] for t in b)
    wr   = len(wins) / len(b) * 100
    ev   = net / len(b)
    flag = " ▲" if net > 0 else ""
    print(f"  rv < {th:.2f}      {len(b):>5}  {wr:>6.1f}%  {net:>+10.2f}  {ev:>+10.4f}{flag}")


# ════════════════════════════════════════════════════════════════════════════
# TEST B — Up only + realized vol threshold
# ════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*70}")
print("B. UP ONLY + REALIZED VOL THRESHOLD COMBINATIONS")
print(f"{'='*70}")

up_trades = [t for t in rv_trades if t['outcome'] == 'Up']
print(f"\nUp-only trades with rv data: {len(up_trades)}")
print(f"Losses in Up-only set: {sum(1 for t in up_trades if not t['is_win'])}")
print()

stats(up_trades, "Up only (baseline, no rv filter)")
print()

print(f"  {'Filter':<35}  {'N':>5}  {'WR%':>7}  {'Net':>10}  {'EV/trade':>10}")
print(f"  {'─'*65}")
for th in [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70]:
    b = [t for t in up_trades if t['rv'] < th]
    if not b:
        continue
    wins = [t for t in b if t['is_win']]
    net  = sum(t['pnl'] for t in b)
    wr   = len(wins) / len(b) * 100
    ev   = net / len(b)
    flag = " ▲" if ev > 0 else ""
    print(f"  Up + rv < {th:.2f}              {len(b):>5}  {wr:>6.1f}%  {net:>+10.2f}  {ev:>+10.4f}{flag}")


# ════════════════════════════════════════════════════════════════════════════
# TEST C — Cooldown after first loss in a session
# ════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*70}")
print("C. COOLDOWN AFTER FIRST LOSS — does it help?")
print(f"{'='*70}")

from datetime import timedelta

for cooldown_h in [1, 2, 4, 6, 8]:
    last_loss_time = None
    kept, skipped_would_win, skipped_would_lose = [], 0, 0
    for t in sorted(all_trades, key=lambda x: x['submitted_at']):
        submitted = t['submitted_at']
        in_cooldown = (
            last_loss_time and submitted and
            (submitted - last_loss_time).total_seconds() < cooldown_h * 3600
        )
        if in_cooldown:
            if t['is_win']:
                skipped_would_win += 1
            else:
                skipped_would_lose += 1
        else:
            kept.append(t)
        if not t['is_win']:
            last_loss_time = submitted

    wins = sum(1 for t in kept if t['is_win'])
    losses_kept = sum(1 for t in kept if not t['is_win'])
    net  = sum(t['pnl'] for t in kept)
    wr   = wins / len(kept) * 100 if kept else 0
    ev   = net / len(kept) if kept else 0
    print(f"\n  {cooldown_h}h cooldown — kept={len(kept)} W={wins} L={losses_kept} WR={wr:.2f}% Net=${net:+.2f} EV=${ev:+.4f}")
    print(f"    Skipped: {skipped_would_win} would-be-wins, {skipped_would_lose} would-be-losses")


# ════════════════════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ════════════════════════════════════════════════════════════════════════════
print(f"\n{'='*70}")
print("FINAL COMPARISON")
print(f"{'='*70}")
print(f"\n  {'Filter':<40}  {'N':>5}  {'WR%':>7}  {'Net':>10}  {'EV/trade':>10}")
print(f"  {'─'*70}")

def row(label, trades):
    if not trades:
        return
    wins = sum(1 for t in trades if t['is_win'])
    net  = sum(t['pnl'] for t in trades)
    wr   = wins / len(trades) * 100
    ev   = net / len(trades)
    flag = " ▲" if ev > 0 else ""
    print(f"  {label:<40}  {len(trades):>5}  {wr:>6.1f}%  {net:>+10.2f}  {ev:>+10.4f}{flag}")

row("Baseline (all)", all_trades)
row("Up only", [t for t in rv_trades if t['outcome'] == 'Up'])
row("rv < 0.40", [t for t in rv_trades if t['rv'] < 0.40])
row("rv < 0.35", [t for t in rv_trades if t['rv'] < 0.35])
row("Up + rv < 0.40", [t for t in rv_trades if t['outcome'] == 'Up' and t['rv'] < 0.40])
row("Up + rv < 0.35", [t for t in rv_trades if t['outcome'] == 'Up' and t['rv'] < 0.35])
row("Up + rv < 0.50", [t for t in rv_trades if t['outcome'] == 'Up' and t['rv'] < 0.50])
print()
