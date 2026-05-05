# Multi-Outcome Bot Idea

## The Core Idea

On Polymarket there are markets with **multiple outcomes** (not just Yes/No or Up/Down).
Example: "What will Bitcoin's price range be on March 28?" with outcomes like:
- "$80k–$85k" → 0.12
- "$85k–$90k" → 0.55
- "$90k–$95k" → 0.28
- "$95k+" → 0.05

If Bitcoin is currently at $87k and the market closes in 5 minutes, the "$85k–$90k"
outcome at 0.55 is the likely winner — but it's nowhere near 0.95. The yield farming
strategy (which requires 0.95+) doesn't apply here.

**The idea**: build a separate bot that buys the highest-probability outcome in
multi-outcome markets in the last 5 minutes, using real-time asset price data to
assess which outcome is most likely to win.

---

## Why It Needs a Separate Bot

The current yield farming strategy works because outcomes reach 0.95+ — near-certainty.
The edge comes from speed and spread capture, not prediction accuracy.

Multi-outcome markets are fundamentally different:
- The dominant outcome typically peaks at 0.50–0.70, not 0.95+
- You win 50–70% of the time, not 95%+ of the time
- Positive EV requires the odds to be mispriced vs. actual probability
- Requires real-time asset price data (e.g. BTC/USD from Binance or Coinbase)
- Different risk model, different position sizing, different threshold logic

Mixing it into the yield farming bot would muddle both strategies.

---

## How It Would Work

1. **Scan** multi-outcome crypto/stock markets closing in the next 5–15 minutes
2. **Fetch real-time price** for the underlying asset (BTC, ETH, etc.) from a price feed
3. **Determine which outcome range contains the current price**
4. **Check market price vs. true probability**:
   - If the outcome is priced at 0.60 but the real-time price puts it at ~0.85 true
     probability → there's mispricing → buy
5. **Execute** via CLOB, same mechanics as yield farming
6. **Monitor** to close/sell if the probability shifts before expiry

---

## Key Risks

- **Crypto can move fast**: Bitcoin can cross a range boundary in seconds. A market
  priced at 0.65 can drop to 0.10 in the final minute.
- **Liquidity**: Multi-outcome markets often have thinner order books than Up/Down.
- **Resolution timing**: These markets typically use an end-of-period price snapshot,
  not a continuous real-time feed — a late spike can flip the outcome.
- **Slippage**: Buying a 0.65 market means more shares at higher cost. Losses hurt more.

---

## Minimum Viable Requirements

- Price feed integration (Binance WebSocket or Coinbase API)
- Multi-outcome market scanner in Gamma API (`outcomes` field has >2 entries)
- True probability calculator: given current price + time to close, what's P(outcome wins)?
- Mispricing threshold (e.g. market price < true probability − 0.10 to have a margin)
- Separate risk guard calibrated for ~60–70% win rate, not 95%+

---

## Status

**Not started.** Tracked here as a future project.
The yield farming bot (current) must be stable and profitable first.
