"""
hedge_bot.py
A simulated tracking environment to mimic the algorithmic "Directional Conviction / Asynchronous Hedging"
behavior executed by whales like coinman2.

Description:
This bot runs a two-phase continuous execution loop:
1. Signal Entry: Takes a massive, unhedged directional stance (Yield Farming) based on extreme confidence.
2. Synthetic Stop-Loss (Hedge Loop): Continuously monitors the intrinsic value of the open position.
   If the price turns against the bot and drops below a max-loss threshold, it mathematically
   blocks further losses by synthetically closing the position using the opposite side of the book.
"""

import sys
import os
import time
import logging
import random

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utility.logger import init_logging

init_logging(level="INFO")
logger = logging.getLogger("HedgingBot")


class AsynchronousHedgingBot:
    def __init__(self, target_market: str, entry_outcome: str, entry_price: float, size: int, stop_loss_pct: float):
        """
        Initializes the Bot with the user's high-conviction bet and risk-management parameters.
        """
        self.market = target_market
        self.outcome = entry_outcome  # "Yes" or "No"
        self.entry_price = entry_price
        self.size = size
        self.stop_loss_pct = stop_loss_pct
        
        self.capital_invested = self.size * self.entry_price
        
        # Portfolio State
        self.portfolio = {
            "Yes": 0,
            "No": 0
        }
        self.is_hedged = False

        logger.info("=" * 70)
        logger.info("[BOT INITIALIZED] Asynchronous Hedging Protocol")
        logger.info("Target Market : %s", self.market)
        logger.info("Stop-Loss     : -%d%% of Initial Capital", self.stop_loss_pct * 100)
        logger.info("=" * 70)

    def phase_1_enter_market(self):
        """Executes the high-conviction directional yield-farm bet."""
        logger.info("[PHASE 1] Executing High-Conviction Directional Bet...")
        self.portfolio[self.outcome] += self.size
        logger.info(f"  --> BOUGHT {self.size:,.0f} shares of '{self.outcome}' @ ${self.entry_price:.3f}")
        logger.info(f"  --> CAPITAL DEPLOYED: ${self.capital_invested:,.2f}")
        logger.info("-" * 70)

    def get_live_market_price(self, current_tick: int) -> float:
        """
        Simulates parsing a live Orderbook to find the best Bid/Ask.
        (In production, this would hit the Gamma API /events endpoint).
        For simulation, we smoothly decay the value of our holding to test the Stop-Loss.
        """
        # Simulate price moving against us over time.
        drop_per_tick = 0.02
        noise = random.uniform(-0.01, 0.01)
        simulated_live_price = max(0.01, self.entry_price - (current_tick * drop_per_tick) + noise)
        return simulated_live_price

    def phase_2_monitoring_loop(self):
        """The continuous asynchronous background loop testing intrinsic metrics against risk thresholds."""
        logger.info("[PHASE 2] Initializing Asynchronous Hedging Subroutine...")
        
        opposing_outcome = "No" if self.outcome == "Yes" else "Yes"
        tick = 0
        
        while not self.is_hedged:
            tick += 1
            time.sleep(1.5)  # Simulate network request delay
            
            # 1. Fetch live orderbook prices
            current_value_price = self.get_live_market_price(tick)
            opposing_price = 1.00 - current_value_price # Polymarket math guarantees P(Yes) + P(No) = 1.00
            
            # 2. Calculate Intrinsic PnL
            current_value_dollars = self.size * current_value_price
            pnl = current_value_dollars - self.capital_invested
            pnl_pct = pnl / self.capital_invested
            
            logger.info(f"  [TICK {tick:02d}] Live '{self.outcome}' Price: ${current_value_price:.3f} | Current PnL: ${pnl:,.2f} ({pnl_pct*100:+.1f}%)")
            
            # 3. Check Stop-Loss Threshold Parameter
            if pnl_pct <= -self.stop_loss_pct:
                logger.warning(f"  [!] STOP-LOSS TRIGGERED AT {pnl_pct*100:+.1f}%!")
                self._execute_hedge(opposing_outcome, opposing_price)
                break

    def _execute_hedge(self, opposing_outcome: str, live_opposing_price: float):
        """Mathematically bounds the loss by buying equal shares on the opposite side of the book."""
        logger.warning(f"  [!] Synthetically closing position. Initiating Market Buy on '{opposing_outcome}'.")
        
        # We buy the exact same amount of shares we hold, so if we win either way, we get $1.00 payout.
        cost_of_hedge = self.size * live_opposing_price
        self.portfolio[opposing_outcome] += self.size
        
        total_cost = self.capital_invested + cost_of_hedge
        guaranteed_payout = self.size * 1.00  # We hold both sides, so someone MUST win.
        final_guaranteed_pnl = guaranteed_payout - total_cost
        
        logger.info("=" * 70)
        logger.info("[HEDGE EXECUTED SUCCESSFULLY]")
        logger.info(f"  --> BOUGHT {self.size:,.0f} shares of '{opposing_outcome}' @ ${live_opposing_price:.3f}")
        logger.info(f"  --> ADDITIONAL CAPITAL DEPLOYED: ${cost_of_hedge:,.2f}")
        logger.info("-" * 70)
        logger.info("NEW PORTFOLIO STATE:")
        logger.info(f"  {self.outcome} shares: {self.portfolio[self.outcome]:,.0f}")
        logger.info(f"  {opposing_outcome} shares: {self.portfolio[opposing_outcome]:,.0f}")
        logger.info("=" * 70)
        logger.info(f"TOTAL CAPITAL AT RISK: ${total_cost:,.2f}")
        logger.info(f"GUARANTEED PAYOUT    : ${guaranteed_payout:,.2f} (Because you own both sides!)")
        logger.info(f"FINAL LOCKED PNL     : ${final_guaranteed_pnl:,.2f}")
        logger.info("=" * 70)
        logger.info("By accepting a calculated loss today, the algorithm successfully blocked the wallet from losing 100% of its capital.")
        self.is_hedged = True


def main():
    # We set up a simulation based off one of coinman2's highly conviction Yield Farms.
    # Ex: Buying NO on "Will Ethereum hit $4500?" for $0.85
    bot = AsynchronousHedgingBot(
        target_market="Will Ethereum hit $4,500 in April?",
        entry_outcome="No",
        entry_price=0.85,
        size=50000,           # $42,500 deployment
        stop_loss_pct=0.20    # Hedge out if we lose 20% of our money ($34,000 threshold)
    )
    
    # Run the architectural framework
    bot.phase_1_enter_market()
    bot.phase_2_monitoring_loop()


if __name__ == "__main__":
    main()
