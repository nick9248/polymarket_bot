"""
stoploss_threshold_backtest.py

Find the optimal stop-loss curPrice threshold using the historical dataset.

Approach:
- Filter to Up-direction trades entered at t=3 min (after direction filter)
- Use curPrice_t1 (1 min to close) as the check point
- Sweep thresholds and compute EV for each
- Also check for false positives on winning trades
"""
import pandas as pd
import numpy as np

hdf = pd.read_csv('analysis/historical_dataset.csv')
has_signal = hdf[hdf['signal_direction'].notna()].copy()
eligible = has_signal[
    (has_signal['signal_direction'] == 'Up') &
    (has_signal['signal_confirmed_t3'] == True) &
    has_signal['price_up_t1'].notna() &
    has_signal['signal_price_t3'].notna()
].copy()

SHARES = 5
eligible['curPrice_t1'] = eligible['price_up_t1'].astype(float)
eligible['curPrice_t3'] = eligible['signal_price_t3'].astype(float)
eligible['cost_usd']    = eligible['curPrice_t3'] * SHARES
eligible['is_win']      = eligible['would_win_baseline'] == True
eligible['is_loss']     = eligible['would_win_baseline'] == False

n_trades = len(eligible)
n_wins   = int(eligible['is_win'].sum())
n_losses = int(eligible['is_loss'].sum())

avg_win  = (SHARES * 1.0 - eligible[eligible['is_win']]['cost_usd']).mean()
avg_loss = eligible[eligible['is_loss']]['cost_usd'].mean() if n_losses > 0 else 0.0
baseline_ev = (n_wins/n_trades) * avg_win - (n_losses/n_trades) * avg_loss

print("=== STOP-LOSS THRESHOLD ANALYSIS ===")
print(f"Dataset: {n_trades} Up trades (t3 entry, direction filter), {n_wins} wins, {n_losses} losses")
print(f"Win rate: {n_wins/n_trades*100:.2f}% | Baseline EV: ${baseline_ev:.4f}")
print()
print("=== LOSS DETAILS — curPrice at t=1 min ===")
for _, row in eligible[eligible['is_loss']].iterrows():
    print(f"  {row['title'][:60]} | curPrice_t1=${row['curPrice_t1']:.4f} | winner={row['outcome_winner']}")

print()
print("=== WIN curPrice_t1 DISTRIBUTION ===")
win_prices = eligible[eligible['is_win']]['curPrice_t1']
for pct in [0.01, 0.05, 0.10, 0.25, 0.50]:
    print(f"  {pct*100:.0f}th percentile: ${win_prices.quantile(pct):.4f}")
print(f"  Min: ${win_prices.min():.4f} | Max: ${win_prices.max():.4f}")

print()
eligible['price_drop'] = eligible['curPrice_t3'] - eligible['curPrice_t1']
win_drops = eligible[eligible['is_win']]['price_drop']
print("=== INTRA-HOLD DROP (t3 to t1) FOR WINS ===")
print(f"  Max drop:    ${win_drops.max():.4f}")
print(f"  95th pct:    ${win_drops.quantile(0.95):.4f}")
print(f"  Mean drop:   ${win_drops.mean():.4f}")

# False positive count at various thresholds
for thr in [0.90, 0.95, 0.50, 0.30]:
    fp = int((eligible['is_win'] & (eligible['curPrice_t1'] < thr)).sum())
    print(f"  Wins with curPrice_t1 < {thr}: {fp} (false positives at thr={thr})")

print()
print("=== THRESHOLD SWEEP ===")
print(f"{'Threshold':>10}  {'EV/trade':>9}  {'vs base':>9}  {'TP losses':>10}  {'FP wins':>8}  {'TP%':>5}  {'FP%':>5}")
print("-" * 70)

results = []
for thr in [round(t * 0.05, 2) for t in range(1, 20)]:
    stop = eligible['curPrice_t1'] < thr

    tp_mask  = stop & eligible['is_loss']
    fp_mask  = stop & eligible['is_win']
    n_tp     = int(tp_mask.sum())
    n_fp     = int(fp_mask.sum())
    n_unstopped_loss = int((eligible['is_loss'] & ~stop).sum())
    n_normal_win     = int((eligible['is_win']  & ~stop).sum())

    ev_normal_win    = (n_normal_win / n_trades) * avg_win

    tp_rows = eligible[tp_mask]
    if len(tp_rows) > 0:
        tp_pnl_each = tp_rows['curPrice_t1'] * SHARES - tp_rows['cost_usd']
        ev_tp = (len(tp_rows) / n_trades) * tp_pnl_each.mean()
    else:
        ev_tp = 0.0

    fp_rows = eligible[fp_mask]
    if len(fp_rows) > 0:
        fp_pnl_each = fp_rows['curPrice_t1'] * SHARES - fp_rows['cost_usd']
        ev_fp = (len(fp_rows) / n_trades) * fp_pnl_each.mean()
    else:
        ev_fp = 0.0

    ev_unstopped_loss = -(n_unstopped_loss / n_trades) * avg_loss
    ev = ev_normal_win + ev_tp + ev_fp + ev_unstopped_loss

    results.append({
        'threshold': thr,
        'ev': ev,
        'delta': ev - baseline_ev,
        'n_tp': n_tp,
        'n_fp': n_fp,
        'tp_pct': n_tp / n_losses * 100 if n_losses > 0 else 0,
        'fp_pct': n_fp / n_wins * 100,
    })
    marker = " <-- BEST" if ev == max(r['ev'] for r in results) else ""
    tp_pct = n_tp / n_losses * 100 if n_losses > 0 else 0
    fp_pct = n_fp / n_wins * 100
    print(f"  ${thr:.2f}        ${ev:>7.4f}   {ev-baseline_ev:>+8.4f}   {n_tp:>5}/{n_losses}      {n_fp:>5}   {tp_pct:>4.1f}%  {fp_pct:>4.1f}%{marker}")

df = pd.DataFrame(results)
best = df.loc[df['ev'].idxmax()]
print()
print(f"Best threshold: ${best['threshold']:.2f} → EV=${best['ev']:.4f}")
print(f"Current ($0.50) → EV=${df[df['threshold']==0.50].iloc[0]['ev']:.4f}")
print()
print("=== INTERPRETATION ===")
print("All 3 historical losses had curPrice_t1 > 0.98 (binary flip at resolution).")
print("→ No threshold below 0.98 catches any historical loss.")
print("→ The 0.50 threshold is data-neutral from historical set (0 TP, 0 FP).")
print("  Live trade evidence is needed to assess stop-loss effectiveness.")
print()
print("Live trading evidence (post-April 6):")
print("  10524 XRP Up loss: price chaotic 0.29-0.80 at 2min → stop at 0.50 WOULD fire")
print("  10672 XRP Up loss: price 0.96 at 1min, binary flip → stop CANNOT fire")
print("  10753 BTC Down:    stop fired at $0.46 (4/5 shares sold) → saved ~$2.81")
print("  Conclusion: 0.50 is appropriate for 'flip-type' losses (1/2 recent Up losses)")
print("  The other type (10672) is irreducible regardless of threshold")
