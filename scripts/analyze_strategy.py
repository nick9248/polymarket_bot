"""
analyze_strategy.py
Stand-alone tool used to reverse engineer an algorithmic trader by pointing
it at their wallet and decoding their completed Round-Trip flips.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging
from utility.logger import init_logging
from service import trades_service
from analysis.strategy import StrategyAnalyzer

init_logging(level="INFO")
logger = logging.getLogger(__name__)

def print_flip(flip: dict):
    hold_time = flip['hold_time_seconds']
    if hold_time < 60:
        ht_str = f"{hold_time}s"
    elif hold_time < 3600:
        ht_str = f"{hold_time/60:.1f}m"
    elif hold_time < 86400:
        ht_str = f"{hold_time/3600:.1f}h"
    else:
        ht_str = f"{hold_time/86400:.1f}d"

    profit = flip['realized_profit']
    color_prefix = "[+]" if profit > 0 else "[-]"
    
    logger.info("%s %-45s | Outcome: %-4s | Size: %7.0f | In: $%.3f | Out: $%.3f | Hold: %-5s | ROI: %6.2f%% | Profit: $%.2f",
                color_prefix,
                flip['title'][:45].strip(),
                flip['outcome'][:4],
                flip['total_bought'],
                flip['avg_entry_price'],
                flip['avg_exit_price'],
                ht_str,
                flip['roi_percentage'],
                profit)

def main():
    parser = argparse.ArgumentParser(description="Polymarket Trader Strategy Analyzer")
    parser.add_argument("wallet", type=str, help="Proxy wallet address to analyze")
    parser.add_argument("--limit", type=int, default=1000, help="Number of recent trades to fetch to reconstruct history")
    args = parser.parse_args()

    logger.info("Fetching last %d trades for wallet %s...", args.limit, args.wallet)
    try:
        trades = trades_service.fetch_user_trades(args.wallet, limit=args.limit)
    except Exception as e:
        logger.error("Failed to fetch trades: %s", e)
        return

    logger.info("Found %d trades. Reconstructing positions...", len(trades))
    results = StrategyAnalyzer.extract_positions(trades)
    
    flips = results["closed"]
    open_pos = results["open"]
    
    if flips:
        logger.info("=" * 140)
        logger.info("  COMPLETED ROUND TRIPS (Flips)")
        logger.info("=" * 140)
        
        total_profit = sum(f['realized_profit'] for f in flips)
        for f in flips:
            print_flip(f)
            
        logger.info("=" * 140)
        logger.info("Total Flips: %d  |  Total Realized Scalping PnL: $%.2f", len(flips), total_profit)
    else:
        logger.info("No perfectly closed round-trips found (they don't scalp, they hold).")
        
    if open_pos:
        logger.info("\n" + "=" * 140)
        logger.info("  CURRENTLY OPEN / ACCUMULATING POSITIONS")
        logger.info("=" * 140)
        open_pos.sort(key=lambda x: abs(x["current_unrealized_size"]), reverse=True)
        for p in open_pos:
            side = "LONG" if p["current_unrealized_size"] > 0 else "SHORT"
            logger.info("Open Size: %10.0f | Avg Entry: $%5.3f | Outcome: %-4s | Action: %-5s | Market: %s",
                        abs(p["current_unrealized_size"]),
                        p["avg_entry_price"] if side == "LONG" else p["avg_exit_price"],
                        p["outcome"][:4],
                        side,
                        p["title"][:70].strip())

if __name__ == "__main__":
    main()
