"""
strategy.py
Contains tools to reverse engineer bot trading strategies by matching orders.
"""

from collections import defaultdict
import logging
from typing import List

from core.models.trades import TradeEntry

logger = logging.getLogger(__name__)

class RoundTrip:
    """Represents a matched pair of BUY and SELL for the exact same market outcome."""
    def __init__(self, condition_id: str, title: str, outcome: str):
        self.condition_id = condition_id
        self.title = title
        self.outcome = outcome
        self.buys = []
        self.sells = []
        self.is_closed = False

    def add_buy(self, trade: TradeEntry):
        self.buys.append(trade)

    def add_sell(self, trade: TradeEntry):
        self.sells.append(trade)

    def total_bought(self) -> float:
        return sum(t.size for t in self.buys)

    def total_sold(self) -> float:
        return sum(t.size for t in self.sells)

    def is_fully_closed(self) -> bool:
        """Determines if the position has been flattened back to 0."""
        bought = self.total_bought()
        sold = self.total_sold()
        
        if bought == 0 or sold == 0:
            return False
            
        # Polymarket sizes return as floats, allow very small margin of drift (.001)
        return abs(bought - sold) < 0.001

    def calculate_metrics(self) -> dict:
        """Returns the final PnL and Hold Time stats for this round trip flip."""
        total_bought = self.total_bought()
        total_sold = self.total_sold()
        
        avg_entry = sum(t.size * t.price for t in self.buys) / total_bought if total_bought else 0
        avg_exit = sum(t.size * t.price for t in self.sells) / total_sold if total_sold else 0
        
        # Approximate hold time by taking time between very first buy and very last sell
        if self.buys and self.sells:
            first_buy = min(t.timestamp for t in self.buys)
            last_sell = max(t.timestamp for t in self.sells)
            hold_time_seconds = last_sell - first_buy
        else:
            hold_time_seconds = 0
            
        realized_profit = sum(t.size * t.price for t in self.sells) - sum(t.size * t.price for t in self.buys)
        
        money_invested = total_bought * avg_entry

        return {
            "title": self.title,
            "outcome": self.outcome,
            "avg_entry_price": avg_entry,
            "avg_exit_price": avg_exit,
            "volume_usd": money_invested + (total_sold * avg_exit),
            "realized_profit": realized_profit,
            "roi_percentage": (realized_profit / money_invested) * 100 if money_invested > 0 else 0.0,
            "hold_time_seconds": hold_time_seconds,
            "total_bought": total_bought,
            "total_sold": total_sold,
            "current_unrealized_size": total_bought - total_sold
        }

class StrategyAnalyzer:
    @staticmethod
    def extract_positions(trades: List[TradeEntry]) -> dict:
        """
        Reverse-engineers a trader's executed positions.
        Returns a dict containing both perfectly closed flips and currently open accumulations.
        """
        sorted_trades = sorted(trades, key=lambda x: x.timestamp)
        
        active_positions = defaultdict(list)
        completed_trips = []

        for trade in sorted_trades:
            key = f"{trade.condition_id}_{trade.outcome_index}"
            
            if not active_positions[key]:
                active_positions[key].append(RoundTrip(trade.condition_id, trade.title, trade.outcome))
                
            current_trip = active_positions[key][-1]
            
            if trade.side.upper() == "BUY":
                current_trip.add_buy(trade)
            elif trade.side.upper() == "SELL":
                current_trip.add_sell(trade)
                
            if current_trip.is_fully_closed():
                current_trip.is_closed = True
                completed_trips.append(current_trip.calculate_metrics())
                active_positions[key].append(RoundTrip(trade.condition_id, trade.title, trade.outcome))

        open_trips = []
        for trips in active_positions.values():
            for trip in trips:
                if not trip.is_closed and (trip.buys or trip.sells):
                    open_trips.append(trip.calculate_metrics())

        return {
            "closed": completed_trips[::-1],
            "open": open_trips[::-1]
        }
        
    @staticmethod
    def determine_profile(bot_check: dict, positions: dict) -> dict:
        """
        Calculates an automated profile classification string based on the player's trades.
        """
        is_bot = bot_check.get("is_bot_likely", False)
        tpd = bot_check.get("frequency_stats", {}).get("trades_per_day", 0)
        
        flips = positions.get("closed", [])
        open_pos = positions.get("open", [])
        
        # Check if they hold massive open positions on opposing sides of the exact same market
        market_sides = defaultdict(set)
        for p in open_pos:
            size_held = abs(p.get("current_unrealized_size", 0))
            if size_held > 100:  # significant commitment
                outcome = p.get("outcome", "").lower()
                market_sides[p.get("title", "")].add(outcome)
                
        cross_market_arb_count = sum(1 for sides in market_sides.values() if len(sides) >= 2)
        
        if is_bot:
            if cross_market_arb_count >= 1:
                classification = "Delta-Neutral Arbitrage Bot"
                description = "Algorithmic trader that mathematically eliminates risk by buying opposing shares on the same market to guarantee a risk-free payout."
            elif len(flips) > len(open_pos) * 2 and len(flips) > 50:
                classification = "High-Frequency Scalping Bot"
                description = "Algorithmic day-trader that executes thousands of micro-flips per day to capture tiny spreads, rarely holding long-term positions."
            else:
                classification = "Automated Accumulation Bot"
                description = "Algorithmic trader systematically building massive directional positions and holding them to market resolution."
        else:
            if len(flips) > len(open_pos) and len(flips) > 20:
                classification = "Active Human Day-Trader"
                description = "Human trader actively speculating and flipping contracts for short-term profit."
            else:
                classification = "Directional Conviction Whale"
                description = "Human trader taking massive, unhedged directional stances (Yield Farming) on highly likely macro events and holding to resolution."

        return {
            "classification": classification,
            "description": description,
            "tpd": tpd,
            "is_bot": is_bot
        }
