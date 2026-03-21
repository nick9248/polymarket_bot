"""
main.py
Entry point for the polymarket_robot application.
Runs as a continuous daemon, polling for new trades on a fixed interval.

Usage:
    python main.py [OPTIONS]

Options:
    --top-n INT           Number of top traders to fetch per category/period (default: 5)
    --trades-limit INT    Number of trades to fetch per wallet (default: 100)
    --wallets STR         Comma-separated list of wallets to track in copy-trading mode
                          Format: 0xADDR:name,0xADDR2:name2

Examples:
    python main.py
    python main.py --wallets 0x123abc:coinman2
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from utility.logger import init_logging
from core.models.leaderboard import LeaderboardEntry
from utility.constants import Category, TimePeriod, OrderBy
from service import leaderboard_service, analysis_service, trades_service, db_service, telegram_service
from service.copy_trade_service import execute_copy_trade
from analysis.analyzer import Analyzer
from analysis.strategy import StrategyAnalyzer

init_logging(level="INFO")
logger = logging.getLogger(__name__)

# Seconds between each polling cycle
POLL_INTERVAL_SECONDS = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Polymarket leaderboard tracker and copy-trading daemon",
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
        help="Number of recent trades to fetch per wallet",
    )
    parser.add_argument(
        "--wallets",
        type=str,
        default="",
        help="Comma-separated wallets to exclusively track (copy-trading mode). Format: 0xADDR:name",
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


def _analyse_trader(trader: LeaderboardEntry, fetch_limit: int) -> dict:
    """Fetch trades and build a full analysis profile for a single trader."""
    trades = trades_service.fetch_user_trades(trader.proxy_wallet, limit=fetch_limit)
    bot_check = Analyzer.detect_hft_patterns(trades)
    positions = StrategyAnalyzer.extract_positions(trades)
    profile = StrategyAnalyzer.determine_profile(bot_check, positions)
    return {
        "trader": trader,
        "rov_percentage": 0.0,
        "trades_buffer": trades,
        "bot_check": bot_check,
        "positions": positions,
        "profile": profile,
    }


def _build_copy_trade_targets(wallets_arg: str) -> dict[str, LeaderboardEntry]:
    """Parse --wallets argument into a {wallet: LeaderboardEntry} map."""
    targets = {}
    for item in (w.strip() for w in wallets_arg.split(",") if w.strip()):
        parts = item.split(":")
        wallet = parts[0].strip()
        user_name = parts[1].strip() if len(parts) > 1 else f"CopyTarget_{wallet[:6]}"
        mock_entry = LeaderboardEntry(
            rank=0,
            proxy_wallet=wallet,
            user_name=user_name,
            x_username="",
            vol=0.0,
            pnl=0.0,
            profile_image="",
            verified_badge=False,
        )
        mock_entry.lists = ["COPY-TRADE"]
        targets[wallet] = mock_entry
    return targets


def _build_leaderboard_targets(args) -> dict[str, LeaderboardEntry]:
    """Fetch leaderboards, persist them, and return unique traders map."""
    unique_top_traders: dict[str, LeaderboardEntry] = {}
    categories_to_check = [Category.CRYPTO]
    periods_to_check = [TimePeriod.ALL, TimePeriod.MONTH]

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
            if not top:
                continue

            print_leaderboard(top, args.top_n, period.name, category.name)
            db_service.persist_leaderboard(top, period.name, category.name)

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

    return unique_top_traders


def _handle_test_command(args: argparse.Namespace) -> None:
    """
    Handle the /test Telegram command.
    Searches the target wallet's recent trades for a market that is both in a
    valid price range (0.15–0.85) AND still open on the CLOB, then attempts a
    live $1.50 copy-trade execution.  Tries all candidates in order until one
    succeeds (a historical trade may have a valid price but the market is now
    closed on the CLOB, so we skip those automatically).
    Reports the result back to Telegram.
    """
    telegram_service.send_message("🔧 <b>Test initiated</b> — fetching recent trades to find an open market...")
    logger.info("/test command received — running live execution test.")

    try:
        targets = _build_copy_trade_targets(args.wallets) if args.wallets else {}
        if not targets:
            telegram_service.send_test_result(False, "No target wallets configured.")
            return

        # Use the first configured wallet
        trader = next(iter(targets.values()))
        trades = trades_service.fetch_user_trades(trader.proxy_wallet, limit=50)

        # Collect all candidates in valid price range — the CLOB decides which are open
        candidates = [t for t in trades if 0.15 <= t.price <= 0.85]

        if not candidates:
            telegram_service.send_test_result(
                False,
                f"No recent trades in valid price range (0.15–0.85) found for {trader.user_name}. "
                f"All recent trades are near-expiry markets."
            )
            return

        logger.info("/test: found %d candidate(s) — trying each until one succeeds.", len(candidates))

        for candidate in candidates:
            detail = (
                f"Market: {candidate.title[:60]}\n"
                f"Side: {candidate.side} | Outcome: {candidate.outcome} | Price: ${candidate.price:.3f}"
            )
            telegram_service.send_message(f"📋 <b>Trying:</b>\n{detail}\n\n⏳ Submitting order...")

            success = execute_copy_trade(candidate)
            if success:
                telegram_service.send_test_result(True, detail)
                return

            logger.info("/test: candidate failed (market likely closed), trying next...")

        # All candidates exhausted
        telegram_service.send_test_result(
            False,
            f"Tried {len(candidates)} candidate(s) — all markets are closed on the CLOB.\n"
            f"Waiting for {trader.user_name} to trade a mid-range market."
        )

    except Exception as e:
        logger.error("/test command failed: %s", e)
        telegram_service.send_test_result(False, f"Unexpected error: {e}")


def run_cycle(args: argparse.Namespace) -> tuple[int, str | None]:
    """
    Execute one full polling cycle: fetch, analyse, alert, copy-trade.

    Returns:
        (alerts_sent, last_new_trade_at) where last_new_trade_at is a UTC
        timestamp string if a new trade was found this cycle, else None.
    """
    logger.info("=" * 60)
    logger.info("Starting polling cycle...")

    # ── Build target wallet list ──────────────────────────────────────────────
    if args.wallets:
        logger.info("COPY-TRADING MODE — targets: %s", args.wallets)
        unique_top_traders = _build_copy_trade_targets(args.wallets)
    else:
        unique_top_traders = _build_leaderboard_targets(args)

    wallets_to_check = list(unique_top_traders.values())
    if not wallets_to_check:
        logger.info("No wallets to check this cycle.")
        return 0, None

    # ── Analyse traders ───────────────────────────────────────────────────────
    fetch_limit = max(args.trades_limit, 500)

    if args.wallets:
        ranked_traders = []
        for trader in wallets_to_check:
            logger.info("Compiling deep target profile for: %s", trader.user_name)
            ranked_traders.append(_analyse_trader(trader, fetch_limit))
    else:
        overlapping = Analyzer.get_overlapping_traders(wallets_to_check)
        if overlapping:
            logger.info("=" * 85)
            logger.info("OVERLAPPING SUPER-TRADERS (present in multiple leaderboards):")
            for t in overlapping:
                logger.info("  * %-30s in [%s]", t.user_name, ", ".join(t.lists))

        logger.info("=" * 85)
        logger.info("Evaluating Trader Efficiency (Return on Volume) & Bot Detection...")
        ranked_traders = Analyzer.get_efficiency_ranking(wallets_to_check)
        for res in ranked_traders:
            analysis = _analyse_trader(res["trader"], fetch_limit)
            res.update({k: v for k, v in analysis.items() if k != "trader"})

        Analyzer.print_efficiency_table(ranked_traders, top_n=len(wallets_to_check))

    # ── Print profiles ────────────────────────────────────────────────────────
    logger.info("\n" + "=" * 115)
    logger.info("  TRADER PROFILES")
    logger.info("=" * 115)
    for i, res in enumerate(ranked_traders):
        prof = res["profile"]
        trader = res["trader"]
        bot_tag = "[BOT]" if prof["is_bot"] else "[HUMAN]"
        logger.info("  #%-2d %-25s %s", i + 1, trader.user_name, bot_tag)
        logger.info("      Algorithm : %s", prof["classification"])
        logger.info("      Behavior  : %s", prof["description"])
        logger.info("-" * 85)

    if telegram_service.is_configured():
        logger.info("Telegram alerts: ENABLED")
    else:
        logger.info("Telegram alerts: DISABLED (add telegram_chat_id to .env)")

    # ── Per-trader: detect new trades, alert, copy-trade ─────────────────────
    alerts_sent = 0
    last_new_trade_at = None

    for res in ranked_traders:
        trader = res["trader"]
        trades = res["trades_buffer"]
        bot_check = res["bot_check"]

        logger.info("-" * 60)
        logger.info("  %s (%s...)", trader.user_name, trader.proxy_wallet[:12])

        # Skip bots — don't alert or copy
        if bot_check["is_bot_likely"]:
            logger.warning("  [!] HFT Bot detected — skipping alerts and copy-trade.")
            for reason in bot_check["reasons"]:
                logger.warning("      - %s", reason)
            db_service.persist_trades(trades)
            continue

        freq = bot_check.get("frequency_stats", {})
        logger.info("  Profile: Human (~%.1f trades/day)", freq.get("trades_per_day", 0))

        print_trades(trader.user_name, trades, limit_to_print=5)

        # Fetch known hashes from DB to identify truly new trades
        known_hashes = set(db_service.get_known_trade_hashes(trader.proxy_wallet, limit=500))

        # Genesis detection: check tracked_wallets, not just known_hashes.
        # This survives DB trade-table wipes without re-executing old history.
        is_genesis = not db_service.is_wallet_tracked(trader.proxy_wallet)
        new_trades = [t for t in trades if t.transaction_hash not in known_hashes]

        if is_genesis:
            logger.info(
                "  [!] Genesis run for %s — seeding %d historical trades silently.",
                trader.user_name, len(new_trades),
            )
            new_trades = []  # Do not alert or execute on historical trades

        # Persist all fetched trades (ON CONFLICT DO NOTHING handles duplicates)
        db_service.persist_trades(trades)

        # Register wallet as tracked after first seed so next run is not genesis
        if is_genesis:
            db_service.upsert_wallet(trader.proxy_wallet, trader.user_name)

        # Alert then attempt copy-trade for each genuinely new trade
        for trade in new_trades:
            # Send Telegram alert first (notification of signal detection)
            if telegram_service.send_trade_alert(trade, trader.user_name):
                alerts_sent += 1

            # Track the time of the most recent new trade this cycle
            last_new_trade_at = trade.datetime_utc.strftime("%Y-%m-%d %H:%M UTC")

            # Attempt to copy the trade
            logger.info("Initiating copy-trade execution...")
            try:
                executed = execute_copy_trade(trade)
                if not executed:
                    logger.warning("Copy-trade was not submitted for: %s", trade.title[:60])
            except Exception as e:
                logger.error("Unexpected failure during copy-trade: %s", e)

        if new_trades:
            logger.info("  -> %d new trade(s) detected, %d alert(s) sent.", len(new_trades), alerts_sent)
        else:
            logger.info("  -> No new trades since last run.")

    logger.info("=" * 60)
    logger.info("Cycle complete. Total alerts sent this cycle: %d", alerts_sent)

    # Validation: reconcile target trades vs our executions, retry any gaps
    if args.wallets:
        from service.validator_service import find_missed_trades
        for res in ranked_traders:
            missed = find_missed_trades(res["trades_buffer"])
            if not missed:
                continue
            logger.info(
                "  [VALIDATOR] Retrying %d missed trade(s) for %s...",
                len(missed), res["trader"].user_name,
            )
            for trade in missed:
                logger.info(
                    "  [VALIDATOR] Retrying: %s %s @ $%.3f",
                    trade.side, trade.title[:55], trade.price,
                )
                try:
                    executed = execute_copy_trade(trade)
                    if not executed:
                        logger.warning(
                            "  [VALIDATOR] Retry skipped by executor: %s", trade.title[:60]
                        )
                except Exception as e:
                    logger.error("  [VALIDATOR] Retry failed: %s", e)

    return alerts_sent, last_new_trade_at


def main() -> None:
    args = parse_args()
    logger.info("polymarket_robot starting (daemon mode, polling every %ds)...", POLL_INTERVAL_SECONDS)

    # Initialise DB once at startup — safe to call repeatedly
    try:
        db_service.initialise_database()
    except Exception as e:
        logger.critical("Database initialisation failed: %s", e)
        sys.exit(1)

    # Runtime stats — updated each cycle and reported via /health
    started_at = datetime.now(timezone.utc)
    stats = {
        "cycles_completed": 0,
        "last_cycle_at": "Not run yet",
        "targets": [w.split(":")[1] if ":" in w else w for w in args.wallets.split(",") if w.strip()],
        "last_new_trade_at": None,
        "alerts_total": 0,
        "db_ok": True,
        "geo_ok": True,
    }

    while True:
        # ── Handle incoming Telegram commands ────────────────────────────────
        commands = telegram_service.get_pending_commands()
        for cmd in commands:
            if cmd == "/health":
                stats["uptime_seconds"] = (datetime.now(timezone.utc) - started_at).total_seconds()
                telegram_service.send_health_report(stats)

            elif cmd == "/test":
                _handle_test_command(args)

        # ── Run polling cycle ─────────────────────────────────────────────────
        try:
            cycle_alerts, last_trade_at = run_cycle(args)
            stats["cycles_completed"] += 1
            stats["alerts_total"] += cycle_alerts
            stats["last_cycle_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if last_trade_at:
                stats["last_new_trade_at"] = last_trade_at
            stats["db_ok"] = True
        except Exception as e:
            logger.error("Unhandled error in polling cycle: %s", e)
            stats["db_ok"] = False

        logger.info("Sleeping %ds until next cycle...", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
