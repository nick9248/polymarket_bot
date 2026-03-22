"""
inspect_trader.py
Deep-dive analysis of a single Polymarket trader.
Accepts either a Polymarket username or a 0x-prefixed wallet address.

Usage:
    python scripts/inspect_trader.py stingo43
    python scripts/inspect_trader.py 0xf705fa045201391d9632b7f3cde06a5e24453ca7
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import datetime, timezone

from utility.logger import init_logging
from utility.constants import Category, TimePeriod, OrderBy
from core.api import polymarket_client
from service import trades_service
from analysis.analyzer import Analyzer
from analysis.strategy import StrategyAnalyzer

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def _format_hold_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _resolve_wallet(identifier: str) -> tuple[str, float, float]:
    """
    Resolve a username or wallet address to (wallet, pnl, vol).

    For a wallet address, attempts a leaderboard lookup by address.
    If not found on any leaderboard, pnl and vol are returned as 0.0.

    Returns:
        Tuple of (proxy_wallet, pnl, vol).
    """
    is_wallet = identifier.lower().startswith("0x")

    if is_wallet:
        wallet = identifier
        logger.info("Input is a wallet address — looking up leaderboard stats...")
        raw = polymarket_client.get_leaderboard(
            category=Category.OVERALL,
            time_period=TimePeriod.ALL,
            order_by=OrderBy.PNL,
            limit=1,
            user=wallet,
        )
        if raw:
            pnl = float(raw[0].get("pnl", 0))
            vol = float(raw[0].get("vol", 0))
        else:
            logger.info("Wallet not found on leaderboard — PnL/Vol will show N/A.")
            pnl, vol = None, None
        return wallet, pnl, vol

    # Username path
    logger.info("Resolving wallet address for '%s'...", identifier)
    raw = polymarket_client.get_leaderboard(
        category=Category.OVERALL,
        time_period=TimePeriod.ALL,
        order_by=OrderBy.PNL,
        limit=50,
        user_name=identifier,
    )
    if not raw:
        raw = polymarket_client.get_leaderboard(
            category=Category.OVERALL,
            time_period=TimePeriod.MONTH,
            order_by=OrderBy.PNL,
            limit=50,
            user_name=identifier,
        )
    if not raw:
        logger.error("Trader '%s' not found on any leaderboard.", identifier)
        sys.exit(1)

    trader_data = raw[0]
    wallet = trader_data["proxyWallet"]
    pnl = float(trader_data.get("pnl", 0))
    vol = float(trader_data.get("vol", 0))
    return wallet, pnl, vol


def main(identifier: str):
    logger.info("=" * 85)
    logger.info("  TRADER INSPECTION: %s", identifier)
    logger.info("=" * 85)

    wallet, pnl, vol = _resolve_wallet(identifier)

    pnl_str = f"${pnl:,.0f}" if pnl is not None else "N/A"
    vol_str = f"${vol:,.0f}" if vol is not None else "N/A"
    logger.info("Wallet: %s  |  Leaderboard PnL: %s  |  Vol: %s",
                wallet, pnl_str, vol_str)

    # ── Fetch all data in parallel-friendly order ─────────────────────────────
    logger.info("Fetching trade history (up to 1000)...")
    trades = trades_service.fetch_user_trades(wallet, limit=1000)
    logger.info("Fetched %d trades.", len(trades))

    logger.info("Fetching closed positions (up to 500)...")
    closed_positions = polymarket_client.get_user_closed_positions(wallet, max_results=500)
    logger.info("Fetched %d closed positions.", len(closed_positions))

    logger.info("Fetching open positions...")
    open_positions = polymarket_client.get_user_positions(wallet)
    logger.info("Fetched %d open positions.", len(open_positions))

    logger.info("Fetching activity feed (up to 500)...")
    activity = polymarket_client.get_user_activity(wallet, limit=500)
    logger.info("Fetched %d activity events.", len(activity))

    # ── Bot detection ─────────────────────────────────────────────────────────
    bot_check = Analyzer.detect_hft_patterns(trades)
    freq = bot_check["frequency_stats"]
    bot_tag = "[BOT]" if bot_check["is_bot_likely"] else "[HUMAN]"

    logger.info("")
    logger.info("── BOT DETECTION ──────────────────────────────────────────────────────────────")
    logger.info("  Classification : %s", bot_tag)
    logger.info("  Trades/day     : %.1f  (over %.1f days)", freq.get("trades_per_day", 0), freq.get("time_window_days", 0))
    logger.info("  Total trades   : %d", freq.get("total_trades", 0))
    if "avg_time_between_trades_sec" in bot_check:
        logger.info("  Avg gap        : %.1fs  |  Median gap: %.1fs",
                    bot_check["avg_time_between_trades_sec"],
                    bot_check["median_time_between_trades_sec"])
    for reason in bot_check.get("reasons", []):
        logger.info("  ⚠  %s", reason)

    # ── Profile classification ────────────────────────────────────────────────
    positions = StrategyAnalyzer.extract_positions(trades)
    profile = StrategyAnalyzer.determine_profile(bot_check, positions)

    logger.info("")
    logger.info("── PROFILE ─────────────────────────────────────────────────────────────────────")
    logger.info("  Algorithm  : %s", profile["classification"])
    logger.info("  Behavior   : %s", profile["description"])

    # ── Trader quality (new) ──────────────────────────────────────────────────
    quality = Analyzer.analyze_closed_positions(closed_positions)
    exposure = Analyzer.analyze_open_positions(open_positions)
    activity_stats = Analyzer.analyze_activity(activity)

    logger.info("")
    logger.info("── TRADER QUALITY ───────────────────────────────────────────────────────────────")
    unrealized_sign = "+" if exposure["total_unrealized_pnl"] >= 0 else ""
    logger.info("  Closed positions : %d  [%s]", quality["closed_position_count"], quality["confidence_tier"])
    logger.info("  Realized win rate: %.1f%%", quality["realized_win_rate"] * 100)
    logger.info("  Total realized PnL: $%s", f"{quality['total_realized_pnl']:,.0f}")
    logger.info("  Realized RoV     : %.1f%%", quality["realized_rov"])
    logger.info("  Avg ROI / position: %.1f%%   Median: %.1f%%",
                quality["avg_roi_per_position"], quality["median_roi_per_position"])
    logger.info("  Open positions   : %d  |  Exposure: $%s  |  Unrealized: %s$%s",
                exposure["open_position_count"],
                f"{exposure['total_open_exposure']:,.0f}",
                unrealized_sign,
                f"{abs(exposure['total_unrealized_pnl']):,.0f}")
    logger.info("  REDEEMs          : %d  |  Total cashed out: $%s",
                activity_stats["redeem_count"], f"{activity_stats['total_redeemed_usdc']:,.0f}")
    logger.info("  Arb signal       : %s  (merges=%d  splits=%d)",
                "YES" if activity_stats["arb_signal"] else "No",
                activity_stats["merge_count"],
                activity_stats["split_count"])

    # ── Last 20 trades ────────────────────────────────────────────────────────
    logger.info("")
    logger.info("── LAST 20 TRADES ──────────────────────────────────────────────────────────────")
    logger.info("  %-12s  %-4s  %-6s  %-6s  %-6s  %s",
                "Date (UTC)", "Side", "Size", "Price", "USDC", "Market")
    logger.info("  " + "-" * 80)
    for t in sorted(trades, key=lambda x: x.timestamp, reverse=True)[:20]:
        dt = datetime.fromtimestamp(t.timestamp, tz=timezone.utc).strftime("%m-%d %H:%M")
        market_short = t.title[:45] + "…" if len(t.title) > 45 else t.title
        logger.info("  %-12s  %-4s  %6.1f  %5.3f   $%6.2f  %s [%s]",
                    dt, t.side, t.size, t.price, t.usdc_value, market_short, t.outcome)

    # ── Top 10 closed positions by realized PnL ───────────────────────────────
    if closed_positions:
        logger.info("")
        logger.info("── TOP 10 CLOSED POSITIONS (by realized PnL) ───────────────────────────────────")
        logger.info("  %-10s  %-6s  %-6s  %-8s  %s",
                    "PnL", "Entry", "Exit", "ROI%", "Market")
        logger.info("  " + "-" * 85)
        sorted_closed = sorted(closed_positions, key=lambda x: x.get("realizedPnl", 0), reverse=True)
        for p in sorted_closed[:10]:
            invested = p.get("totalBought", 0.0) * p.get("avgPrice", 0.0)
            roi = (p.get("realizedPnl", 0.0) / invested * 100) if invested > 0 else 0.0
            title = p.get("title", "")
            market_short = (title[:45] + "…") if len(title) > 45 else title
            logger.info("  $%s  %6.3f  %6.3f  %7.1f%%  %s [%s]",
                        f"{p.get('realizedPnl', 0):>8,.0f}",
                        p.get("avgPrice", 0),
                        p.get("curPrice", 0),
                        roi,
                        market_short,
                        p.get("outcome", ""))

    # ── Open positions (top by exposure) ─────────────────────────────────────
    if open_positions:
        logger.info("")
        logger.info("── OPEN POSITIONS (%d) ──────────────────────────────────────────────────────────", len(open_positions))
        sorted_open = sorted(open_positions, key=lambda x: x.get("initialValue", 0), reverse=True)
        for p in sorted_open[:10]:
            title = p.get("title", "")
            market_short = (title[:40] + "…") if len(title) > 40 else title
            cash_pnl = p.get("cashPnl", 0)
            pnl_sign = "+" if cash_pnl >= 0 else "-"
            logger.info("  $%s invested @ $%.3f  |  PnL: %s$%s  |  %s [%s]",
                        f"{p.get('initialValue', 0):>8,.0f}",
                        p.get("avgPrice", 0),
                        pnl_sign,
                        f"{abs(cash_pnl):,.0f}",
                        market_short,
                        p.get("outcome", ""))

    logger.info("")
    logger.info("=" * 85)


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "stingo43"
    main(name)
