"""
analyzer.py
Analyzes trader efficiency and trading patterns to distinguish highly efficient humans from high-frequency bots.
"""

import statistics
import logging

from core.models.leaderboard import LeaderboardEntry
from core.models.trades import TradeEntry

logger = logging.getLogger(__name__)

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

        # Sort highest RoV first
        results.sort(key=lambda x: x["rov_percentage"], reverse=True)
        return results

    @staticmethod
    def get_overlapping_traders(traders: list[LeaderboardEntry]) -> list[LeaderboardEntry]:
        """Returns traders who appear in 2 or more distinct leaderboards."""
        return [t for t in traders if hasattr(t, 'lists') and len(t.lists) >= 2]

    @staticmethod
    def print_efficiency_table(ranked_results: list[dict], top_n: int = 5):
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

        # Trades are assumed to be sorted most recent first, but we guarantee correct min/max
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
            "time_window_days": days_diff
        }

    @staticmethod
    def detect_hft_patterns(trades: list[TradeEntry]) -> dict:
        """
        Detects if a wallet exhibits High-Frequency Trading (HFT) robot patterns.
        """
        if len(trades) < 5:
            return {"is_bot_likely": False, "reasons": [], "frequency_stats": Analyzer.calculate_trade_frequencies(trades)}

        # Sort trades chronologically to calculate deltas
        sorted_trades = sorted(trades, key=lambda x: x.timestamp)
        
        time_deltas = []
        for i in range(1, len(sorted_trades)):
            delta = sorted_trades[i].timestamp - sorted_trades[i-1].timestamp
            time_deltas.append(delta)

        avg_delta = statistics.mean(time_deltas)
        median_delta = statistics.median(time_deltas)
        zero_delta_count = sum(1 for d in time_deltas if d == 0)

        # Basic Bot Heuristics
        is_bot_likely = False
        reasons = []

        if zero_delta_count > (len(trades) * 0.3):
            is_bot_likely = True
            reasons.append(">30% of trades execute in the exact same second (batch execution).")
        
        if avg_delta < 60 and median_delta < 10:
            is_bot_likely = True
            reasons.append(f"Extremely fast execution pace: Median {median_delta}s between trades.")

        frequency_stats = Analyzer.calculate_trade_frequencies(trades)
        if frequency_stats["trades_per_day"] > 150:
            is_bot_likely = True
            reasons.append(f"Abnormally high volume of trades ({frequency_stats['trades_per_day']:.1f} per day).")

        return {
            "is_bot_likely": is_bot_likely,
            "reasons": reasons,
            "avg_time_between_trades_sec": avg_delta,
            "median_time_between_trades_sec": median_delta,
            "frequency_stats": frequency_stats
        }
