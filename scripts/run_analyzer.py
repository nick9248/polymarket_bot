"""
run_analyzer.py
Stand-alone script to run the Analyzer on the top Polymarket traders.
It will fetch the top traders, rank their efficiency, and test them for bot patterns,
without modifying the database or sending any Telegram alerts.
"""
import sys
import os

# Add the project root to the Python path if run from the scripts directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from utility.logger import init_logging
from utility.constants import Category, TimePeriod, OrderBy
from service import leaderboard_service, trades_service
from analysis.analyzer import Analyzer
from analysis.strategy import StrategyAnalyzer

init_logging(level="INFO")
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting Analyzer Report...")
    
    # Fetch combinations
    categories = [Category.CRYPTO]
    periods = [TimePeriod.ALL, TimePeriod.MONTH]
    
    unique_traders = {}
    
    logger.info("Fetching Top 10 traders for Crypto (All-time & Monthly)...")
    for cat in categories:
        for p in periods:
            entries = leaderboard_service.fetch_leaderboard(
                category=cat, time_period=p, order_by=OrderBy.PNL, limit=10
            )
            for e in entries:
                if e.proxy_wallet not in unique_traders:
                    e.lists = [p.name]
                    unique_traders[e.proxy_wallet] = e
                else:
                    if p.name not in unique_traders[e.proxy_wallet].lists:
                        unique_traders[e.proxy_wallet].lists.append(p.name)
                    
    wallets_to_check = list(unique_traders.values())
    
    if not wallets_to_check:
        logger.info("No traders found.")
        return
        
    overlapping = Analyzer.get_overlapping_traders(wallets_to_check)
    if overlapping:
        logger.info("=" * 85)
        logger.info("OVERLAPPING SUPER-TRADERS (Present in multiple leaderboards):")
        for t in overlapping:
            logger.info("  ⭐ %-30s in [%s]", t.user_name, ", ".join(t.lists))

    logger.info("=" * 85)
    logger.info("Evaluating Trader Efficiency & Fetching Bot Signals...")
    ranked_traders = Analyzer.get_efficiency_ranking(wallets_to_check)
    
    for res in ranked_traders:
        trader = res["trader"]
        trades = trades_service.fetch_user_trades(trader.proxy_wallet, limit=1000)
        bot_check = Analyzer.detect_hft_patterns(trades)
        res["bot_check"] = bot_check
        res["positions"] = StrategyAnalyzer.extract_positions(trades)
        res["profile"] = StrategyAnalyzer.determine_profile(bot_check, res["positions"])
        
    Analyzer.print_efficiency_table(ranked_traders, top_n=len(wallets_to_check))
    
    logger.info("\n" + "=" * 115)
    logger.info("  TRADER PROFILES")
    logger.info("=" * 115)
    
    for i, res in enumerate(ranked_traders):
        prof = res["profile"]
        trader = res["trader"]
        bot_tag = "[BOT]" if prof["is_bot"] else "[HUMAN]"
        logger.info("  #%-2d %-25s %s", i+1, trader.user_name, bot_tag)
        logger.info("      Algorithm : %s", prof["classification"])
        logger.info("      Behavior  : %s", prof["description"])
        logger.info("-" * 85)

if __name__ == "__main__":
    main()
