"""
stoploss_profitability.py

Models the EV impact of adding a stop-loss mechanism to the yield farming strategy.

Key parameters:
  - exit_price:     CLOB price at which we sell our position after a flip
  - frac_stoppable: % of losses where the flip is detectable with enough time to sell
  - false_pos_rate: % of WINNING trades where we incorrectly trigger the stop-loss

Real data (since April 6 fixes):
  - 203 trades: 199 wins (98.03%), 4 losses (1.97%)
  - avg win profit:  $0.1298
  - avg loss cost:   $4.835  (5 shares × ~$0.967 entry)
  - current EV/trade: +$0.0320

From the price flip analysis (April 9 losses):
  - SOL Down: flip 5 min before close  → possible exit at ~$0.25–0.35
  - BTC Down: flip 3 min before close  → possible exit at ~$0.15–0.25
  - XRP Up:   flip 1 min after entry, 8 min remaining → exit at ~$0.35–0.45
  - BNB Down: no underlying data
  → Estimate 60–75% of losses have a usable stop window
"""

# ── Real data (since Apr 6) ──────────────────────────────────────────────────
WIN_RATE      = 199 / 203        # 0.9803
AVG_WIN       = 0.1298           # avg profit per winning trade ($)
AVG_COST_LOSS = 4.835            # avg capital deployed on a losing trade ($)
SHARES        = 5                # shares per trade

# Current baseline EV
baseline_ev = WIN_RATE * AVG_WIN - (1 - WIN_RATE) * AVG_COST_LOSS
print("=" * 70)
print("STOP-LOSS PROFITABILITY MODEL")
print("=" * 70)
print(f"\nBase data (since Apr 6):")
print(f"  Win rate:       {WIN_RATE*100:.2f}%  (199/203 trades)")
print(f"  Avg win profit: ${AVG_WIN:.4f}")
print(f"  Avg loss:       ${AVG_COST_LOSS:.4f}")
print(f"  Break-even WR:  {AVG_COST_LOSS/(AVG_COST_LOSS+AVG_WIN)*100:.2f}%")
print(f"\n  CURRENT EV per trade: ${baseline_ev:.4f}")
print(f"  At 50 trades/day: ${baseline_ev*50:.2f}/day | ${baseline_ev*50*365:.0f}/year (at current capital)")


# ── Model ────────────────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("SCENARIO ANALYSIS — varying exit price, stoppable %, false positive %")
print(f"{'='*70}")

scenarios = [
    # (label, exit_price, frac_stoppable, false_pos_rate)
    # exit_price: CLOB price we sell our position at when stop triggers
    # frac_stoppable: % of losses where we detect the flip and can sell
    # false_pos_rate: % of wins where we accidentally stop-loss (costly!)

    # ── Optimistic ────────────────────────────────────────────────────────
    ("Optimistic  (exit=0.45, stop=75%, FP=0%)",    0.45, 0.75, 0.000),
    ("Optimistic  (exit=0.45, stop=75%, FP=0.1%)",  0.45, 0.75, 0.001),
    ("Optimistic  (exit=0.45, stop=75%, FP=0.5%)",  0.45, 0.75, 0.005),

    # ── Moderate ─────────────────────────────────────────────────────────
    ("Moderate    (exit=0.30, stop=60%, FP=0%)",    0.30, 0.60, 0.000),
    ("Moderate    (exit=0.30, stop=60%, FP=0.1%)",  0.30, 0.60, 0.001),
    ("Moderate    (exit=0.30, stop=60%, FP=0.5%)",  0.30, 0.60, 0.005),

    # ── Conservative ─────────────────────────────────────────────────────
    ("Conservative(exit=0.20, stop=50%, FP=0%)",    0.20, 0.50, 0.000),
    ("Conservative(exit=0.20, stop=50%, FP=0.1%)",  0.20, 0.50, 0.001),
    ("Conservative(exit=0.20, stop=50%, FP=0.5%)",  0.20, 0.50, 0.005),
]

print(f"\n{'Scenario':<48} {'EV/trade':>9} {'vs now':>8} {'Break-even':>11}")
print(f"{'─'*80}")

for label, exit_p, fs, fp in scenarios:
    # On a stopped loss: we recover exit_p × shares instead of losing all
    net_loss_stopped    = SHARES * exit_p - AVG_COST_LOSS   # e.g. 5×0.30 - 4.835 = -3.335
    net_loss_unstopped  = -AVG_COST_LOSS                    # -4.835

    # On a false positive (winning trade incorrectly stopped):
    # We paid avg_cost, receive back exit_p × shares
    net_false_positive  = SHARES * exit_p - AVG_COST_LOSS   # same formula, but we were going to win

    # Probabilities for each outcome:
    p_true_win          = WIN_RATE * (1 - fp)
    p_false_pos         = WIN_RATE * fp
    p_loss_stopped      = (1 - WIN_RATE) * fs
    p_loss_unstopped    = (1 - WIN_RATE) * (1 - fs)

    ev = (p_true_win      * AVG_WIN          +
          p_false_pos     * net_false_positive +
          p_loss_stopped  * net_loss_stopped  +
          p_loss_unstopped * net_loss_unstopped)

    # Effective break-even win rate
    # With stop-loss: break-even when avg_win × WR = effective_avg_loss × (1-WR)
    effective_avg_loss = fs * (-net_loss_stopped) + (1-fs) * AVG_COST_LOSS
    be = effective_avg_loss / (effective_avg_loss + AVG_WIN) * 100

    delta = ev - baseline_ev
    improvement = delta / abs(baseline_ev) * 100
    sign = "+" if delta >= 0 else ""
    print(f"  {label:<46} ${ev:>7.4f}  {sign}{improvement:>5.0f}%  {be:>9.2f}%")

# ── Key insight table ────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("BREAK-EVEN WIN RATE — no false positives, varying exit price")
print(f"{'='*70}")
print(f"\n  {'Exit price':>12}  {'Stoppable':>10}  {'Eff avg loss':>13}  {'Break-even WR':>14}")
print(f"  {'─'*55}")
for exit_p, fs in [(0.50, 0.75), (0.40, 0.75), (0.35, 0.65), (0.30, 0.60), (0.20, 0.50)]:
    eff_loss = fs * (AVG_COST_LOSS - SHARES * exit_p) + (1-fs) * AVG_COST_LOSS
    be = eff_loss / (eff_loss + AVG_WIN) * 100
    print(f"  ${exit_p:.2f}              {fs*100:.0f}%        ${eff_loss:.3f}        {be:.2f}%")
print(f"  (no stop-loss)        —          ${AVG_COST_LOSS:.3f}        {AVG_COST_LOSS/(AVG_COST_LOSS+AVG_WIN)*100:.2f}%")

# ── Annual projection ────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("ANNUAL PROFIT PROJECTION — at current win rate (98.03%), 50 trades/day")
print(f"{'='*70}")
DAILY_TRADES = 50
print(f"\n  {'Scenario':<35}  {'EV/trade':>9}  {'Daily':>8}  {'Annual':>10}")
print(f"  {'─'*65}")

key_scenarios = [
    ("No stop-loss (current)",          0.00,  0.00, 0.00),
    ("Conservative (exit=0.20, 50%)",   0.20,  0.50, 0.00),
    ("Moderate    (exit=0.30, 60%)",    0.30,  0.60, 0.00),
    ("Optimistic  (exit=0.45, 75%)",    0.45,  0.75, 0.00),
    ("Moderate + 0.1% FP (exit=0.30)", 0.30,  0.60, 0.001),
    ("Optimistic + 0.1% FP (exit=0.45)", 0.45, 0.75, 0.001),
]
for label, exit_p, fs, fp in key_scenarios:
    if exit_p == 0.00:
        ev = baseline_ev
    else:
        net_loss_stopped   = SHARES * exit_p - AVG_COST_LOSS
        net_false_positive = SHARES * exit_p - AVG_COST_LOSS
        ev = (WIN_RATE*(1-fp)*AVG_WIN +
              WIN_RATE*fp*net_false_positive +
              (1-WIN_RATE)*fs*net_loss_stopped +
              (1-WIN_RATE)*(1-fs)*(-AVG_COST_LOSS))
    daily = ev * DAILY_TRADES
    annual = daily * 365
    print(f"  {label:<35}  ${ev:>7.4f}  ${daily:>6.2f}  ${annual:>8.0f}")

print(f"\n  Note: projections assume same win rate, capital, and trade volume.")
print(f"  Current capital is ~$50-60. Scale increases profit linearly.")
