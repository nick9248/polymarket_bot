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
from core.api import polymarket_client
from analysis.analyzer import Analyzer
from analysis.strategy import StrategyAnalyzer

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def main():
    logger.info("Starting Analyzer Report...")

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
            logger.info("  * %-30s in [%s]", t.user_name, ", ".join(t.lists))

    logger.info("=" * 85)
    logger.info("Evaluating Trader Efficiency, Bot Signals & Quality Metrics...")
    ranked_traders = Analyzer.get_efficiency_ranking(wallets_to_check)

    for res in ranked_traders:
        trader = res["trader"]
        wallet = trader.proxy_wallet

        logger.info("  Analysing %s (%s)...", trader.user_name, wallet[:10])

        trades = trades_service.fetch_user_trades(wallet, limit=1000)
        bot_check = Analyzer.detect_hft_patterns(trades)
        res["bot_check"] = bot_check
        res["positions"] = StrategyAnalyzer.extract_positions(trades)
        res["profile"] = StrategyAnalyzer.determine_profile(bot_check, res["positions"])

        closed = polymarket_client.get_user_closed_positions(wallet, max_results=500)
        open_pos = polymarket_client.get_user_positions(wallet)
        activity = polymarket_client.get_user_activity(wallet, limit=500)

        res["quality"] = Analyzer.analyze_closed_positions(closed)
        res["open_stats"] = Analyzer.analyze_open_positions(open_pos)
        res["activity_stats"] = Analyzer.analyze_activity(activity)

    Analyzer.print_efficiency_table(ranked_traders, top_n=len(wallets_to_check))
    Analyzer.print_quality_table(ranked_traders, top_n=len(wallets_to_check))

    logger.info("\n" + "=" * 115)
    logger.info("  TRADER PROFILES")
    logger.info("=" * 115)

    for i, res in enumerate(ranked_traders):
        prof = res["profile"]
        trader = res["trader"]
        q = res["quality"]
        open_s = res["open_stats"]
        act = res["activity_stats"]
        bot_tag = "[BOT]" if prof["is_bot"] else "[HUMAN]"

        logger.info("  #%-2d %-25s %s", i + 1, trader.user_name, bot_tag)
        logger.info("      Algorithm : %s", prof["classification"])
        logger.info("      Behavior  : %s", prof["description"])
        logger.info(
            "      Quality   : %d closed [%s] | Win rate: %.1f%% | Realized RoV: %.1f%% | Median ROI: %.1f%%",
            q["closed_position_count"],
            q["confidence_tier"],
            q["realized_win_rate"] * 100,
            q["realized_rov"],
            q["median_roi_per_position"],
        )
        logger.info(
            "      Exposure  : %d open / $%s invested | Unrealized: %s$%s",
            open_s["open_position_count"],
            f"{open_s['total_open_exposure']:,.0f}",
            "+" if open_s["total_unrealized_pnl"] >= 0 else "-",
            f"{abs(open_s['total_unrealized_pnl']):,.0f}",
        )
        logger.info(
            "      Activity  : %d REDEEMs ($%s) | Arb signal: %s (merges=%d)",
            act["redeem_count"],
            f"{act['total_redeemed_usdc']:,.0f}",
            "YES" if act["arb_signal"] else "No",
            act["merge_count"],
        )
        logger.info("-" * 85)


if __name__ == "__main__":
    main()
