"""
direction_filter_backtest.py
Backtest the impact of filtering by direction (Up vs Down) and hour-of-day.
Tests multiple filter combinations on the VPS yield_trades dataset.
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
           EXTRACT(HOUR FROM submitted_at AT TIME ZONE 'UTC') as hour_utc
    FROM yield_trades
    WHERE status IN ('won', 'lost', 'stopped')
    ORDER BY submitted_at ASC
""")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
conn.close()

all_trades = [dict(zip(cols, r)) for r in rows]

# Attach computed fields
for t in all_trades:
    t['hour'] = int(t['hour_utc']) if t['hour_utc'] is not None else -1
    t['direction'] = t['outcome']  # 'Up' or 'Down'
    t['is_win'] = t['status'] == 'won'
    t['pnl'] = float(t['pnl_usd']) if t['pnl_usd'] else 0.0
    t['cost'] = float(t['cost_usd']) if t['cost_usd'] else 0.0

def ev_summary(trades, label):
    """Compute and print EV stats for a set of trades."""
    if not trades:
        print(f"  {label}: no trades")
        return
    wins = [t for t in trades if t['is_win']]
    losses = [t for t in trades if not t['is_win']]
    total = len(trades)
    win_rate = len(wins) / total
    net_pnl = sum(t['pnl'] for t in trades)
    avg_win = statistics.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = abs(statistics.mean([t['pnl'] for t in losses])) if losses else 0
    ev_per_trade = net_pnl / total
    print(f"  {label}")
    print(f"    Trades: {total} | Wins: {len(wins)} | Losses: {len(losses)}")
    print(f"    Win rate: {win_rate*100:.2f}% | Net P&L: ${net_pnl:+.2f}")
    print(f"    Avg win: +${avg_win:.4f} | Avg loss: -${avg_loss:.4f}")
    print(f"    EV per trade: ${ev_per_trade:+.4f}")
    # Break-even win rate: EV=0 when W*avg_win = (1-W)*avg_loss → W = avg_loss/(avg_win+avg_loss)
    if avg_win > 0 and avg_loss > 0:
        be_rate = avg_loss / (avg_win + avg_loss)
        print(f"    Break-even win rate: {be_rate*100:.2f}%  ({'ABOVE' if win_rate > be_rate else 'BELOW'} break-even)")
    print()

print("=" * 70)
print("DIRECTION FILTER BACKTEST")
print("=" * 70)

# Baseline
print(f"\n{'─'*70}")
print("BASELINE (all trades)")
print(f"{'─'*70}")
ev_summary(all_trades, "All trades")

# Up only
print(f"\n{'─'*70}")
print("FILTER: Direction = Up only")
print(f"{'─'*70}")
up_trades = [t for t in all_trades if t['direction'] == 'Up']
down_trades = [t for t in all_trades if t['direction'] == 'Down']
ev_summary(up_trades, "Up bets only")
ev_summary(down_trades, "Down bets only (for reference)")

# High-loss hours filter
HIGH_LOSS_HOURS = {22, 0, 3}  # UTC hours with >10% loss rate
print(f"\n{'─'*70}")
print(f"FILTER: Avoid high-loss hours {HIGH_LOSS_HOURS} UTC (>10% loss rate)")
print(f"{'─'*70}")
no_high_hours = [t for t in all_trades if t['hour'] not in HIGH_LOSS_HOURS]
ev_summary(no_high_hours, "Avoiding 22h, 0h, 3h UTC")

# Combined: Up only + avoid high-loss hours
print(f"\n{'─'*70}")
print("FILTER: Up only + avoid high-loss hours")
print(f"{'─'*70}")
up_no_high_hours = [t for t in all_trades if t['direction'] == 'Up' and t['hour'] not in HIGH_LOSS_HOURS]
ev_summary(up_no_high_hours, "Up + avoid 22h/0h/3h UTC")

# Extended hour filter (all hours with >5% loss rate)
HIGH_LOSS_HOURS_EXT = {22, 0, 3, 13, 15, 16, 18}
print(f"\n{'─'*70}")
print(f"FILTER: Avoid hours with >5% loss rate {HIGH_LOSS_HOURS_EXT} UTC")
print(f"{'─'*70}")
no_ext_hours = [t for t in all_trades if t['hour'] not in HIGH_LOSS_HOURS_EXT]
ev_summary(no_ext_hours, "Avoiding all >5% loss-rate hours")

# Up only + extended hour filter
print(f"\n{'─'*70}")
print("FILTER: Up only + avoid all >5% loss-rate hours")
print(f"{'─'*70}")
up_no_ext = [t for t in all_trades if t['direction'] == 'Up' and t['hour'] not in HIGH_LOSS_HOURS_EXT]
ev_summary(up_no_ext, "Up + avoid all high-loss hours")

# Loss cooldown: skip N minutes after a loss
print(f"\n{'─'*70}")
print("FILTER: 4-hour cooldown after any loss")
print(f"{'─'*70}")
from datetime import timedelta
cooldown_h = 4
last_loss_time = None
cooldown_trades = []
for t in sorted(all_trades, key=lambda x: x['submitted_at']):
    submitted = t['submitted_at']
    if last_loss_time and submitted and (submitted - last_loss_time).total_seconds() < cooldown_h * 3600:
        # Skipped — within cooldown window
        continue
    cooldown_trades.append(t)
    if not t['is_win']:
        last_loss_time = submitted

ev_summary(cooldown_trades, f"{cooldown_h}h post-loss cooldown")

# Best combination: Up + 4h cooldown
print(f"\n{'─'*70}")
print("FILTER: Up only + 4h post-loss cooldown")
print(f"{'─'*70}")
last_loss_time = None
up_cooldown_trades = []
for t in sorted(all_trades, key=lambda x: x['submitted_at']):
    if t['direction'] != 'Up':
        # Even if skipped, if it would have been a loss, reset cooldown
        # (we don't know outcome at entry, so don't update cooldown for non-Up)
        continue
    submitted = t['submitted_at']
    if last_loss_time and submitted and (submitted - last_loss_time).total_seconds() < cooldown_h * 3600:
        continue
    up_cooldown_trades.append(t)
    if not t['is_win']:
        last_loss_time = submitted

ev_summary(up_cooldown_trades, "Up only + 4h post-loss cooldown")

# ── Per-filter volume impact ────────────────────────────────────────────────
print(f"\n{'='*70}")
print("FILTER COMPARISON SUMMARY")
print(f"{'='*70}")
print(f"\n  {'Filter':<40} {'Trades':>7} {'Win%':>7} {'Net P&L':>10} {'EV/trade':>10}")
print(f"  {'─'*78}")

def summary_row(label, trades):
    if not trades:
        print(f"  {label:<40} {'0':>7} {'N/A':>7} {'N/A':>10} {'N/A':>10}")
        return
    wins = sum(1 for t in trades if t['is_win'])
    net = sum(t['pnl'] for t in trades)
    wr = wins/len(trades)*100
    ev = net/len(trades)
    print(f"  {label:<40} {len(trades):>7} {wr:>6.1f}% {net:>+10.2f} {ev:>+10.4f}")

summary_row("Baseline (all)", all_trades)
summary_row("Up only", up_trades)
summary_row("Down only", down_trades)
summary_row("Avoid 22h/0h/3h UTC", no_high_hours)
summary_row("Up + avoid 22h/0h/3h UTC", up_no_high_hours)
summary_row("Avoid all >5% loss hours", no_ext_hours)
summary_row("Up + avoid all >5% loss hours", up_no_ext)
summary_row("4h cooldown after loss", cooldown_trades)
summary_row("Up + 4h cooldown", up_cooldown_trades)
print()
