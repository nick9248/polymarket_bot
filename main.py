"""
main.py
Entry point for the polymarket_robot application.

Usage:
    python main.py [OPTIONS]

Options:
    --top-n INT           Number of top traders to fetch per category/period (default: 5)
    --trades-limit INT    Number of trades to fetch per wallet to find active positions (default: 100)

Examples:
    python main.py
    python main.py --top-n 10 --trades-limit 50
"""

import argparse
import logging

from utility.logger import init_logging
from core.models.leaderboard import LeaderboardEntry
from utility.constants import Category, TimePeriod, OrderBy
from service import leaderboard_service, analysis_service, trades_service, db_service, telegram_service
from analysis.analyzer import Analyzer
from analysis.strategy import StrategyAnalyzer

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Polymarket leaderboard tracker",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of top traders to fetch from each leaderboard",
    )
    parser.add_argument(
        "--trades-limit",
        type=int,
        default=100,
        help="Number of recent trades to fetch per wallet to capture active positions",
    )
    parser.add_argument(
        "--wallets",
        type=str,
        default="",
        help="Comma-separated list of Proxy Wallets to exclusively track (Copy-Trading Mode).",
    )
    return parser.parse_args()


def print_leaderboard(entries, top_n: int, period: str, category: str) -> None:
    logger.info("=" * 60)
    logger.info("  TOP %d TRADERS — %s / %s", top_n, period, category)
    logger.info("=" * 60)
    for entry in entries:
        logger.info(
            "  #%-3d  %-30s  PnL: %s   Vol: %s",
            entry.rank,
            entry.user_name,
            f"${entry.pnl:,.2f}",
            f"${entry.vol:,.2f}",
        )
    logger.info("=" * 60)


def print_trades(trader_name: str, trades, limit_to_print: int = 5) -> None:
    if not trades:
        logger.info("  No recent trades found.")
        return
    
    for trade in trades[:limit_to_print]:
        logger.info(
            "    %-4s  %-45s  %-4s  @ $%.3f  size: %s  [%s]",
            trade.side,
            trade.title[:45],
            trade.outcome,
            trade.price,
            f"{trade.size:,.0f}",
            trade.datetime_utc.strftime("%Y-%m-%d %H:%M"),
        )
    
    if len(trades) > limit_to_print:
        logger.info("    ... and %d older trades.", len(trades) - limit_to_print)


def main() -> None:
    args = parse_args()

    logger.info("polymarket_robot starting...")
    logger.info("Fetching Top %d traders for Crypto (All-Time & Monthly)", args.top_n)

    # ── 0. Initialise database (creates DB + tables if they don't exist) ──────
    db_service.initialise_database()

    # Combinations to fetch per user request:
    # Categories: Crypto Only
    # Periods: All-Time and Monthly
    categories_to_check = [Category.CRYPTO]
    periods_to_check = [TimePeriod.ALL, TimePeriod.MONTH]
    
    # Track unique top traders
    # Key: proxy_wallet, Value: LeaderboardEntry
    unique_top_traders = {}
    
    if args.wallets:
        logger.info("=" * 85)
        logger.info("COPY-TRADING MODE ACTIVATED")
        logger.info("=" * 85)
        wallet_list = [w.strip() for w in args.wallets.split(",") if w.strip()]
        for item in wallet_list:
            parts = item.split(":")
            wallet = parts[0].strip()
            # Parse custom name if provided (e.g. 0x123...:coinman2), else fallback to hash
            user_name = parts[1].strip() if len(parts) > 1 else f"CopyTarget_{wallet[:6]}"
            
            # We create a mock LeaderboardEntry for our custom target
            mock_entry = LeaderboardEntry(
                rank=0,
                proxy_wallet=wallet,
                user_name=user_name,
                x_username="",
                vol=0.0,
                pnl=0.0,
                profile_image="",
                verified_badge=False
            )
            mock_entry.lists = ["COPY-TRADE"]
            unique_top_traders[wallet] = mock_entry
    else:
        # ── 1 & 2. Fetch + persist leaderboards ──────────────────────────────────
        for category in categories_to_check:
            for period in periods_to_check:
                logger.info("Fetching leaderboard for %s - %s...", period.name, category.name)
                entries = leaderboard_service.fetch_leaderboard(
                    category=category,
                    time_period=period,
                    order_by=OrderBy.PNL,
                    limit=min(args.top_n, 50),
                )
                
                top = entries[:args.top_n]
                if top:
                    print_leaderboard(top, args.top_n, period.name, category.name)
                    db_service.persist_leaderboard(top, period.name, category.name)
                    
                    # Add unique traders to our mapping and track their lists
                    for trader in top:
                        if trader.proxy_wallet not in unique_top_traders:
                            trader.lists = [period.name]
                            unique_top_traders[trader.proxy_wallet] = trader
                        else:
                            if period.name not in unique_top_traders[trader.proxy_wallet].lists:
                                unique_top_traders[trader.proxy_wallet].lists.append(period.name)

                summary = analysis_service.summarise_leaderboard(top)
                if summary:
                    logger.info(
                        "  Summary (%s/%s): %d traders | total PnL: %s | avg PnL: %s\n",
                        period.name, category.name,
                        summary["total_traders"],
                        f"${summary['total_pnl']:,.2f}",
                        f"${summary['avg_pnl']:,.2f}",
                    )

    # ── 3. Evaluate Efficiency + Fetch & Persist Trades ─────────────────
    wallets_to_check = list(unique_top_traders.values())
    alerts_sent = 0

    if wallets_to_check:
        ranked_traders = []
        if args.wallets:
            # If in copy-trade mode, bypass the efficiency calculation and just compile their data directly
            for trader in wallets_to_check:
                logger.info("Compiling deep target profile for: %s", trader.proxy_wallet)
                fetch_limit = max(args.trades_limit, 500)
                trades = trades_service.fetch_user_trades(trader.proxy_wallet, limit=fetch_limit)
                
                bot_check = Analyzer.detect_hft_patterns(trades)
                positions = StrategyAnalyzer.extract_positions(trades)
                profile = StrategyAnalyzer.determine_profile(bot_check, positions)
                

                
                ranked_traders.append({
                    "trader": trader,
                    "rov_percentage": 0.0,
                    "trades_buffer": trades,
                    "bot_check": bot_check,
                    "positions": positions,
                    "profile": profile
                })
        else:
            overlapping = Analyzer.get_overlapping_traders(wallets_to_check)
            if overlapping:
                logger.info("=" * 85)
                logger.info("OVERLAPPING SUPER-TRADERS (Present in multiple leaderboards):")
                for t in overlapping:
                    logger.info("  ⭐ %-30s in [%s]", t.user_name, ", ".join(t.lists))

            logger.info("=" * 85)
            logger.info("Evaluating Trader Efficiency (Return on Volume) & Bot Detection...")
            ranked_traders = Analyzer.get_efficiency_ranking(wallets_to_check)
            
            for res in ranked_traders:
                trader = res["trader"]
                # To build an accurate profile, if the default limit is too low, we override it to a minimum of 500
                fetch_limit = max(args.trades_limit, 500)
                trades = trades_service.fetch_user_trades(trader.proxy_wallet, limit=fetch_limit)
                res["trades_buffer"] = trades  # save so we don't have to fetch again
                
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

        logger.info("")
        logger.info("Persisting trades for %d unique Top Wallets:", len(wallets_to_check))
        if telegram_service.is_configured():
            logger.info("Telegram alerts: ENABLED")
        else:
            logger.info("Telegram alerts: DISABLED (add telegram_chat_id to .env)")

    # The list is already ranked by top RoV
    for res in ranked_traders:
        trader = res["trader"]
        trades = res["trades_buffer"]
        bot_check = res["bot_check"]
        
        logger.info("-" * 60)
        logger.info("  %s (%s)", trader.user_name, trader.proxy_wallet[:12] + "...")

        # Get hashes already in DB BEFORE fetching so we can detect new ones
        known_hashes = set(db_service.get_known_trade_hashes(trader.proxy_wallet, limit=500))

        # HFT / Bot Detection handling
        if bot_check["is_bot_likely"]:
            logger.warning("  [!] WARNING: High-Frequency Trading Bot detected.")
            for reason in bot_check["reasons"]:
                logger.warning("      - %s", reason)
            logger.warning("  Skipping Telegram alerts for this bot.")
            
            # Persist them anyway, just don't alert
            db_service.persist_trades(trades)
            continue
        
        if len(trades) >= 2:
            freq = bot_check["frequency_stats"]
            logger.info("  Trader Profile: Human / Highly Efficient (~%.1f trades/day)", freq.get("trades_per_day", 0))

        print_trades(trader.user_name, trades, limit_to_print=5)

        # Detect which trades are actually new
        new_trades = [t for t in trades if t.transaction_hash not in known_hashes]
        
        if not known_hashes:
            logger.info("  [!] Genesis Run for %s: First time seeing this wallet.", trader.proxy_wallet)
            logger.info("      Seeding database with %d historical trades without alerting/executing.", len(new_trades))
            new_trades = [] # Clear the queue to prevent blasting Telegram / executing history

        # Persist all (ON CONFLICT DO NOTHING handles duplicates)
        db_service.persist_trades(trades)

        # Alert for each new trade
        for trade in new_trades:
            if telegram_service.send_trade_alert(trade, trader.user_name):
                alerts_sent += 1
                
            # Attempt to execute copy trade
            try:
                from service.copy_trade_service import execute_copy_trade
                logger.info("Initiating automatic copy-trade execution...")
                execute_copy_trade(trade, trade_size_usd=2.0)
            except Exception as e:
                logger.error("Failed to copy trade: %s", e)

        if new_trades:
            logger.info("  → %d new trade(s) detected, %d alert(s) sent.", len(new_trades), alerts_sent)
        else:
            logger.info("  → No new trades since last run.")

    logger.info("=" * 60)
    logger.info("Done. Total alerts sent: %d", alerts_sent)
    
    # Validation step: Log our own active executions if in copy-trading mode
    if args.wallets:
        from service.validator_service import validate_own_trades
        validate_own_trades(limit=5)


if __name__ == "__main__":
    main()
