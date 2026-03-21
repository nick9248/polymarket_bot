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
