"""
loss_analysis.py
Deep analysis of all resolved yield trades from VPS DB.
Compares win vs loss features to find predictive signals.
"""
import sys, os, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
from core.database.connection import get_connection

conn = get_connection()
cur = conn.cursor()

cur.execute("""
    SELECT id, title, outcome, status, signal_price, fill_price, shares, cost_usd,
           minutes_to_close, submitted_at, resolved_at, pnl_usd,
           EXTRACT(EPOCH FROM (resolved_at - submitted_at))/60 as minutes_held,
           EXTRACT(HOUR FROM submitted_at AT TIME ZONE 'UTC') as hour_utc,
           EXTRACT(DOW FROM submitted_at AT TIME ZONE 'UTC') as dow,
           gamma_clob_spread, btc_dvol
    FROM yield_trades
    WHERE status IN ('won', 'lost', 'stopped')
    ORDER BY submitted_at ASC
""")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
conn.close()

all_trades = [dict(zip(cols, r)) for r in rows]
wins   = [t for t in all_trades if t['status'] == 'won']
losses = [t for t in all_trades if t['status'] == 'lost']

print("=" * 70)
print("YIELD TRADE LOSS ANALYSIS")
print("=" * 70)
print(f"\nTotal resolved: {len(all_trades)} | Wins: {len(wins)} | Losses: {len(losses)}")
print(f"Win rate: {len(wins)/len(all_trades)*100:.2f}%")

# ── 1. All losses detail ────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("ALL LOSSES — detail")
print(f"{'='*70}")
print(f"  {'ID':>6}  {'Asset':10}  {'Dir':4}  {'Signal':>7}  {'Fill':>6}  {'MinsLeft':>9}  {'Held':>7}  {'HourUTC':>8}  {'Date'}")
print(f"  {'-'*85}")
for t in losses:
    title = t['title']
    # Extract asset name
    asset = title.split(' Up or Down')[0].split(' ')[0] if 'Up or Down' in title else title[:10]
    direction = t['outcome']
    submitted = t['submitted_at']
    date_str = submitted.strftime('%b %d') if submitted else '?'
    signal = float(t['signal_price']) if t['signal_price'] else 0
    fill   = float(t['fill_price'])   if t['fill_price']   else 0
    mins   = float(t['minutes_to_close']) if t['minutes_to_close'] else 0
    held   = float(t['minutes_held'])     if t['minutes_held'] else 0
    hour   = int(t['hour_utc'])           if t['hour_utc'] is not None else -1
    print(f"  {t['id']:>6}  {asset:10}  {direction:4}  ${signal:.4f}  ${fill:.4f}  {mins:>8.1f}m  {held:>6.1f}m  {hour:>6}h UTC  {date_str}")

# ── 2. Feature comparison: wins vs losses ───────────────────────────────────
def safe_avg(lst):
    lst = [x for x in lst if x is not None]
    return statistics.mean(lst) if lst else None

def safe_pct(lst, cond_fn):
    lst = [x for x in lst if x is not None]
    return sum(1 for x in lst if cond_fn(x)) / len(lst) * 100 if lst else None

print(f"\n{'='*70}")
print("FEATURE COMPARISON — wins vs losses")
print(f"{'='*70}")

win_signals  = [float(t['signal_price']) for t in wins  if t['signal_price']]
loss_signals = [float(t['signal_price']) for t in losses if t['signal_price']]
win_fills    = [float(t['fill_price'])   for t in wins  if t['fill_price']]
loss_fills   = [float(t['fill_price'])   for t in losses if t['fill_price']]
win_mins     = [float(t['minutes_to_close']) for t in wins  if t['minutes_to_close']]
loss_mins    = [float(t['minutes_to_close']) for t in losses if t['minutes_to_close']]
win_held     = [float(t['minutes_held'])    for t in wins  if t['minutes_held']]
loss_held    = [float(t['minutes_held'])    for t in losses if t['minutes_held']]
win_hours    = [int(t['hour_utc'])  for t in wins  if t['hour_utc'] is not None]
loss_hours   = [int(t['hour_utc']) for t in losses if t['hour_utc'] is not None]

print(f"\n  {'Feature':<30} {'Wins (avg)':>12}  {'Losses (avg)':>14}")
print(f"  {'-'*60}")
print(f"  {'Signal price':<30} {safe_avg(win_signals):>12.4f}  {safe_avg(loss_signals):>14.4f}")
print(f"  {'Fill price (curPrice@1st)':<30} {safe_avg(win_fills):>12.4f}  {safe_avg(loss_fills):>14.4f}")
print(f"  {'Minutes to close at entry':<30} {safe_avg(win_mins):>12.2f}  {safe_avg(loss_mins):>14.2f}")
print(f"  {'Minutes held (entry→resolve)':<30} {safe_avg(win_held):>12.2f}  {safe_avg(loss_held):>14.2f}")

# signal-fill gap (how much price moved from signal to first fill detection)
win_gaps  = [float(w['signal_price']) - float(w['fill_price']) for w in wins  if w['signal_price'] and w['fill_price']]
loss_gaps = [float(l['signal_price']) - float(l['fill_price']) for l in losses if l['signal_price'] and l['fill_price']]
print(f"  {'Signal→Fill gap (drop)':<30} {safe_avg(win_gaps):>12.4f}  {safe_avg(loss_gaps):>14.4f}")

# ── 3. Minutes-to-close distribution ───────────────────────────────────────
print(f"\n{'='*70}")
print("MINUTES TO CLOSE — distribution")
print(f"{'='*70}")
buckets = [(0,3,'0-3 min'), (3,6,'3-6 min'), (6,10,'6-10 min'), (10,16,'10-16 min'), (16,999,'16+ min')]
print(f"\n  {'Bucket':12}  {'Wins':>6}  {'Losses':>7}  {'Loss rate':>10}")
print(f"  {'-'*45}")
for lo, hi, label in buckets:
    w = sum(1 for t in wins   if t['minutes_to_close'] and lo <= float(t['minutes_to_close']) < hi)
    l = sum(1 for t in losses if t['minutes_to_close'] and lo <= float(t['minutes_to_close']) < hi)
    total = w + l
    rate = l/total*100 if total else 0
    print(f"  {label:12}  {w:>6}  {l:>7}  {rate:>9.1f}%")

# ── 4. Direction analysis ───────────────────────────────────────────────────
print(f"\n{'='*70}")
print("DIRECTION — win rate by outcome")
print(f"{'='*70}")
for direction in ['Down', 'Up']:
    w = sum(1 for t in wins   if t['outcome'] == direction)
    l = sum(1 for t in losses if t['outcome'] == direction)
    total = w + l
    rate = w/total*100 if total else 0
    print(f"  {direction}: {w} wins / {l} losses / {total} total = {rate:.1f}% win rate")

# ── 5. Asset analysis ───────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("ASSET — win rate per underlying")
print(f"{'='*70}")
assets = {}
for t in all_trades:
    asset = t['title'].split(' Up or Down')[0].split(' ')[-1] if 'Up or Down' in t['title'] else 'Other'
    # Clean up asset name
    if 'Solana' in t['title']: asset = 'SOL'
    elif 'Bitcoin' in t['title']: asset = 'BTC'
    elif 'Ethereum' in t['title']: asset = 'ETH'
    elif 'XRP' in t['title']: asset = 'XRP'
    elif 'BNB' in t['title']: asset = 'BNB'
    elif 'Dogecoin' in t['title']: asset = 'DOGE'
    elif 'Hyperliquid' in t['title']: asset = 'HYPE'
    elif 'Microsoft' in t['title']: asset = 'MSFT'
    else: asset = 'Other'
    if asset not in assets:
        assets[asset] = {'won': 0, 'lost': 0}
    assets[asset][t['status'] if t['status'] in ('won','lost') else 'won'] += 1

print(f"  {'Asset':8}  {'Wins':>6}  {'Losses':>7}  {'Win rate':>10}")
print(f"  {'-'*38}")
for asset, counts in sorted(assets.items(), key=lambda x: x[1]['lost'], reverse=True):
    total = counts['won'] + counts['lost']
    rate = counts['won']/total*100 if total else 0
    print(f"  {asset:8}  {counts['won']:>6}  {counts['lost']:>7}  {rate:>9.1f}%")

# ── 6. Hour-of-day analysis ────────────────────────────────────────────────
print(f"\n{'='*70}")
print("HOUR OF DAY (UTC) — loss concentration")
print(f"{'='*70}")
hour_data = {}
for t in all_trades:
    if t['hour_utc'] is None: continue
    h = int(t['hour_utc'])
    if h not in hour_data: hour_data[h] = {'won': 0, 'lost': 0}
    hour_data[h][t['status'] if t['status'] in ('won','lost') else 'won'] += 1

print(f"  {'Hour UTC':10}  {'ET equiv':10}  {'Wins':>6}  {'Losses':>7}  {'Loss rate':>10}")
print(f"  {'-'*50}")
for h in sorted(hour_data.keys()):
    counts = hour_data[h]
    total = counts['won'] + counts['lost']
    rate = counts['lost']/total*100 if total else 0
    et_hour = (h - 4) % 24  # rough ET (EDT = UTC-4)
    marker = " ◄ HIGH" if rate > 5 else ""
    print(f"  {h:>4}h UTC    {et_hour:>4}h ET    {counts['won']:>6}  {counts['lost']:>7}  {rate:>9.1f}%{marker}")

# ── 7. Loss clustering: time gaps between losses ────────────────────────────
print(f"\n{'='*70}")
print("LOSS CLUSTERING — time between consecutive losses")
print(f"{'='*70}")
loss_times = [t['submitted_at'] for t in losses if t['submitted_at']]
loss_times_sorted = sorted(loss_times)
print(f"\n  Loss timestamps (UTC):")
for i, lt in enumerate(loss_times_sorted):
    gap = ""
    if i > 0:
        delta = (lt - loss_times_sorted[i-1]).total_seconds() / 3600
        gap = f"  (+{delta:.1f}h from prev)"
    print(f"  {i+1:>2}. {lt.strftime('%Y-%m-%d %H:%M')} UTC{gap}")

# Cluster losses within 4h windows
print(f"\n  Loss clusters (within 4h of each other):")
clusters = []
current_cluster = [loss_times_sorted[0]] if loss_times_sorted else []
for lt in loss_times_sorted[1:]:
    if (lt - current_cluster[-1]).total_seconds() / 3600 <= 4:
        current_cluster.append(lt)
    else:
        if current_cluster:
            clusters.append(current_cluster)
        current_cluster = [lt]
if current_cluster:
    clusters.append(current_cluster)

for i, cluster in enumerate(clusters):
    span = (cluster[-1] - cluster[0]).total_seconds() / 3600 if len(cluster) > 1 else 0
    print(f"  Cluster {i+1}: {len(cluster)} loss(es) | {cluster[0].strftime('%b %d %H:%M')} → {cluster[-1].strftime('%b %d %H:%M')} UTC | span={span:.1f}h")

# ── 8. Signal price vs fill price gap for losses ────────────────────────────
print(f"\n{'='*70}")
print("SIGNAL→FILL PRICE DROP — potential early exit signal")
print(f"{'='*70}")
print(f"\n  (Signal=CLOB price at scan, Fill=curPrice at first position detection)")
print(f"  {'ID':>6}  {'Signal':>8}  {'Fill':>7}  {'Drop':>7}  {'Drop%':>7}  {'MinsLeft':>9}")
print(f"  {'-'*55}")
for t in losses:
    if t['signal_price'] and t['fill_price']:
        sig = float(t['signal_price'])
        fil = float(t['fill_price'])
        drop = sig - fil
        drop_pct = drop / sig * 100
        mins = float(t['minutes_to_close']) if t['minutes_to_close'] else 0
        print(f"  {t['id']:>6}  ${sig:.4f}  ${fil:.4f}  ${drop:.4f}  {drop_pct:>6.1f}%  {mins:>8.1f}m")

print(f"\n  Average signal→fill drop for WINS:")
w_drops = [(float(t['signal_price']) - float(t['fill_price'])) for t in wins if t['signal_price'] and t['fill_price']]
w_drop_pcts = [(float(t['signal_price']) - float(t['fill_price']))/float(t['signal_price'])*100 for t in wins if t['signal_price'] and t['fill_price']]
print(f"    Avg drop: ${safe_avg(w_drops):.4f} ({safe_avg(w_drop_pcts):.2f}%)")
print(f"    >5% drop: {sum(1 for p in w_drop_pcts if p > 5)}/{len(w_drop_pcts)} wins ({sum(1 for p in w_drop_pcts if p > 5)/len(w_drop_pcts)*100:.1f}%)")

l_drop_pcts = [(float(t['signal_price']) - float(t['fill_price']))/float(t['signal_price'])*100 for t in losses if t['signal_price'] and t['fill_price']]
print(f"\n  Average signal→fill drop for LOSSES:")
print(f"    Avg drop: ${safe_avg(loss_gaps):.4f} ({safe_avg(l_drop_pcts):.2f}%)")
print(f"    >5% drop: {sum(1 for p in l_drop_pcts if p > 5)}/{len(l_drop_pcts)} losses ({sum(1 for p in l_drop_pcts if p > 5)/len(l_drop_pcts)*100:.1f}% of losses)")
