"""
analyzer.py
Analyzes trader efficiency and trading patterns to distinguish highly efficient humans from high-frequency bots.
"""

import statistics
import logging

from core.models.leaderboard import LeaderboardEntry
from core.models.trades import TradeEntry

logger = logging.getLogger(__name__)

# ── HFT Detection Thresholds ──────────────────────────────────────────────────
# Fraction of trades that must share the exact same timestamp to flag batch execution.
# 0.50 = more than half of trades fire simultaneously — almost certainly automated.
_SAME_SECOND_BATCH_THRESHOLD = 0.50

# Trades per day above this rate suggest fully automated execution.
# Professional human day-traders rarely exceed 500 trades/day.
_MAX_HUMAN_TRADES_PER_DAY = 500

# Speed thresholds: avg AND median both extremely tight, on a large sample.
# A human physically cannot sustain <10s average with <5s median across 100+ trades.
_SPEED_AVG_THRESHOLD_SEC = 10
_SPEED_MEDIAN_THRESHOLD_SEC = 5
_SPEED_MIN_TRADE_COUNT = 100


class Analyzer:
    @staticmethod
    def get_efficiency_ranking(entries: list[LeaderboardEntry]) -> list[dict]:
        """
        Sorts the given traders by their Return on Volume (PnL / Vol).
        Returns a list of dicts with the analysis results.
        """
        results = []
        for entry in entries:
            rov = (entry.pnl / entry.vol * 100) if entry.vol else 0.0
            results.append({
                "trader": entry,
                "rov_percentage": rov
            })

        results.sort(key=lambda x: x["rov_percentage"], reverse=True)
        return results

    @staticmethod
    def get_overlapping_traders(traders: list[LeaderboardEntry]) -> list[LeaderboardEntry]:
        """Returns traders who appear in 2 or more distinct leaderboards."""
        return [t for t in traders if hasattr(t, 'lists') and len(t.lists) >= 2]

    @staticmethod
    def print_efficiency_table(ranked_results: list[dict], top_n: int = 5) -> None:
        """Helper to print the efficiency ranking."""
        logger.info("=" * 115)
        logger.info("  TRADER EFFICIENCY RANKING (Return on Volume)")
        logger.info("=" * 115)
        for i, res in enumerate(ranked_results[:top_n], 1):
            trader = res["trader"]
            rov = res["rov_percentage"]
            lists_str = f"[{', '.join(trader.lists)}]" if hasattr(trader, 'lists') and trader.lists else ""

            bot_tag = ""
            if "bot_check" in res:
                bot_tag = "[BOT]" if res["bot_check"]["is_bot_likely"] else "[HUMAN]"

            tpd = res.get("bot_check", {}).get("frequency_stats", {}).get("trades_per_day", 0.0)

            logger.info(
                "  #%-3d %-25s %-15s %-7s | ~%-8.1f tpd | RoV: %6.2f%% | PnL: %10s | Vol: %10s",
                i,
                trader.user_name,
                lists_str,
                bot_tag,
                tpd,
                rov,
                f"${trader.pnl:,.0f}",
                f"${trader.vol:,.0f}",
            )
        logger.info("=" * 115)

    @staticmethod
    def print_quality_table(ranked_results: list[dict], top_n: int = 25) -> None:
        """
        Prints a realized-quality ranking table for traders who have quality data.
        Expects each result dict to contain a 'quality', 'activity_stats' key
        populated by analyze_closed_positions() and analyze_activity().
        """
        results_with_quality = [r for r in ranked_results if "quality" in r]
        if not results_with_quality:
            logger.info("No quality data available — skipping quality table.")
            return

        logger.info("=" * 115)
        logger.info("  TRADER QUALITY RANKING (Realized Metrics)")
        logger.info("=" * 115)
        logger.info(
            "  %-3s %-25s %-10s %-8s %-13s %-11s %-6s %-12s",
            "#", "Trader", "Confidence", "WinRate", "Realized RoV", "Median ROI", "Arb", "Realized PnL",
        )
        logger.info("  " + "-" * 100)

        sorted_by_quality = sorted(
            results_with_quality,
            key=lambda r: (
                r["quality"]["realized_win_rate"],
                r["quality"]["realized_rov"],
            ),
            reverse=True,
        )

        for i, res in enumerate(sorted_by_quality[:top_n], 1):
            trader = res["trader"]
            q = res["quality"]
            arb = "YES" if res.get("activity_stats", {}).get("arb_signal", False) else "No"

            logger.info(
                "  %-3d %-25s %-10s %7.1f%%  %12.1f%%  %10.1f%%  %-6s %s",
                i,
                trader.user_name,
                f"[{q['confidence_tier']}]",
                q["realized_win_rate"] * 100,
                q["realized_rov"],
                q["median_roi_per_position"],
                arb,
                f"${q['total_realized_pnl']:,.0f}",
            )
        logger.info("=" * 115)

    @staticmethod
    def calculate_trade_frequencies(trades: list[TradeEntry]) -> dict:
        """
        Calculates trade frequencies (trades per hour/day) based on a list of historical trades.
        """
        if not trades or len(trades) < 2:
            return {"trades_per_hour": 0.0, "trades_per_day": 0.0, "total_trades": len(trades)}

        timestamps = [t.timestamp for t in trades]
        min_ts = min(timestamps)
        max_ts = max(timestamps)

        time_diff_seconds = max_ts - min_ts
        if time_diff_seconds == 0:
            return {"trades_per_hour": 0.0, "trades_per_day": 0.0, "total_trades": len(trades)}

        hours_diff = time_diff_seconds / 3600.0
        days_diff = time_diff_seconds / 86400.0

        return {
            "total_trades": len(trades),
            "trades_per_hour": len(trades) / hours_diff if hours_diff > 0 else 0,
            "trades_per_day": len(trades) / days_diff if days_diff > 0 else 0,
            "time_window_days": days_diff,
        }

    @staticmethod
    def detect_hft_patterns(trades: list[TradeEntry]) -> dict:
        """
        Detects if a wallet exhibits High-Frequency Trading (HFT) robot patterns.
        Uses conservative thresholds to avoid false positives on fast human traders.
        """
        frequency_stats = Analyzer.calculate_trade_frequencies(trades)

        if len(trades) < 5:
            return {"is_bot_likely": False, "reasons": [], "frequency_stats": frequency_stats}

        sorted_trades = sorted(trades, key=lambda x: x.timestamp)

        time_deltas = []
        for i in range(1, len(sorted_trades)):
            delta = sorted_trades[i].timestamp - sorted_trades[i - 1].timestamp
            time_deltas.append(delta)

        avg_delta = statistics.mean(time_deltas)
        median_delta = statistics.median(time_deltas)
        zero_delta_count = sum(1 for d in time_deltas if d == 0)

        is_bot_likely = False
        reasons = []

        # Check 1: Majority of trades fire in the exact same second (batch/scripted execution)
        if zero_delta_count > (len(trades) * _SAME_SECOND_BATCH_THRESHOLD):
            is_bot_likely = True
            reasons.append(
                f">{int(_SAME_SECOND_BATCH_THRESHOLD * 100)}% of trades execute in the exact same second "
                f"({zero_delta_count}/{len(trades)} — batch execution pattern)."
            )

        # Check 2: Sustained superhuman speed across a large sample
        if (len(trades) >= _SPEED_MIN_TRADE_COUNT
                and avg_delta < _SPEED_AVG_THRESHOLD_SEC
                and median_delta < _SPEED_MEDIAN_THRESHOLD_SEC):
            is_bot_likely = True
            reasons.append(
                f"Superhuman execution speed across {len(trades)} trades: "
                f"avg {avg_delta:.1f}s, median {median_delta:.1f}s between trades."
            )

        # Check 3: Trade volume far exceeds any realistic human capacity
        if frequency_stats["trades_per_day"] > _MAX_HUMAN_TRADES_PER_DAY:
            is_bot_likely = True
            reasons.append(
                f"Trade rate of {frequency_stats['trades_per_day']:.0f}/day exceeds "
                f"human capacity (threshold: {_MAX_HUMAN_TRADES_PER_DAY}/day)."
            )

        return {
            "is_bot_likely": is_bot_likely,
            "reasons": reasons,
            "avg_time_between_trades_sec": avg_delta,
            "median_time_between_trades_sec": median_delta,
            "frequency_stats": frequency_stats,
        }

    @staticmethod
    def analyze_closed_positions(closed: list[dict]) -> dict:
        """
        Computes realized performance metrics from /closed-positions API data.

        Uses Polymarket's own ground-truth realizedPnl values — no reconstruction
        from raw trades needed. totalBought is in shares; multiply by avgPrice
        for USDC invested.

        Args:
            closed: Raw list of dicts from get_user_closed_positions().

        Returns:
            Dict with realized win rate, RoV, total PnL, avg/median ROI per
            position, closed count, and a confidence tier label.
        """
        if not closed:
            return {
                "closed_position_count": 0,
                "confidence_tier": "insufficient",
                "realized_win_rate": 0.0,
                "total_realized_pnl": 0.0,
                "total_invested_closed": 0.0,
                "realized_rov": 0.0,
                "avg_roi_per_position": 0.0,
                "median_roi_per_position": 0.0,
            }

        count = len(closed)

        if count < 5:
            confidence_tier = "insufficient"
        elif count < 15:
            confidence_tier = "low"
        elif count < 50:
            confidence_tier = "moderate"
        else:
            confidence_tier = "high"

        wins = sum(1 for p in closed if p.get("realizedPnl", 0) > 0)
        realized_win_rate = wins / count

        total_realized_pnl = sum(p.get("realizedPnl", 0.0) for p in closed)

        total_invested = sum(
            p.get("totalBought", 0.0) * p.get("avgPrice", 0.0) for p in closed
        )

        realized_rov = (total_realized_pnl / total_invested * 100) if total_invested > 0 else 0.0

        per_position_rois = [
            p.get("realizedPnl", 0.0) / (p.get("totalBought", 0.0) * p.get("avgPrice", 1.0)) * 100
            for p in closed
            if p.get("totalBought", 0.0) * p.get("avgPrice", 0.0) > 0
        ]

        avg_roi = statistics.mean(per_position_rois) if per_position_rois else 0.0
        median_roi = statistics.median(per_position_rois) if per_position_rois else 0.0

        return {
            "closed_position_count": count,
            "confidence_tier": confidence_tier,
            "realized_win_rate": realized_win_rate,
            "total_realized_pnl": total_realized_pnl,
            "total_invested_closed": total_invested,
            "realized_rov": realized_rov,
            "avg_roi_per_position": avg_roi,
            "median_roi_per_position": median_roi,
        }

    @staticmethod
    def analyze_open_positions(open_positions: list[dict]) -> dict:
        """
        Computes current exposure metrics from /positions API data.

        Args:
            open_positions: Raw list of dicts from get_user_positions().

        Returns:
            Dict with open count, total USDC exposure, unrealized P&L,
            and counts of redeemable and mergeable positions.
        """
        count = len(open_positions)
        total_exposure = sum(p.get("initialValue", 0.0) for p in open_positions)
        total_unrealized = sum(p.get("cashPnl", 0.0) for p in open_positions)
        redeemable_count = sum(1 for p in open_positions if p.get("redeemable", False))
        mergeable_count = sum(1 for p in open_positions if p.get("mergeable", False))

        return {
            "open_position_count": count,
            "total_open_exposure": total_exposure,
            "total_unrealized_pnl": total_unrealized,
            "redeemable_count": redeemable_count,
            "mergeable_count": mergeable_count,
        }

    @staticmethod
    def analyze_activity(activity_events: list[dict]) -> dict:
        """
        Computes confirmed win and arbitrage signals from /activity feed data.

        REDEEM events confirm a market resolved and the trader cashed out.
        MERGE events indicate the trader held opposing YES+NO shares and merged
        them for USDC — a classic arbitrage pattern.

        Args:
            activity_events: Raw list of dicts from get_user_activity().

        Returns:
            Dict with REDEEM/MERGE/SPLIT counts, total USDC redeemed,
            and a boolean arb_signal flag.
        """
        redeems = [e for e in activity_events if e.get("type") == "REDEEM"]
        merges = [e for e in activity_events if e.get("type") == "MERGE"]
        splits = [e for e in activity_events if e.get("type") == "SPLIT"]

        total_redeemed = sum(e.get("usdcSize", 0.0) for e in redeems)

        return {
            "redeem_count": len(redeems),
            "total_redeemed_usdc": total_redeemed,
            "merge_count": len(merges),
            "split_count": len(splits),
            "arb_signal": len(merges) > 0,
        }
