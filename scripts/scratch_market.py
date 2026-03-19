import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service.trades_service import fetch_user_trades
from collections import defaultdict
import logging

logging.disable(logging.CRITICAL)

def main():
    wallet = "0xTARGET_WALLET_REDACTED"
    print(f"Fetching trades for justdance ({wallet})...")
    
    try:
        trades = fetch_user_trades(wallet, limit=3000)
    except Exception as e:
        print("Failed to fetch:", e)
        return
    
    # Group trades by Market Title
    markets = defaultdict(list)
    for t in trades:
        markets[t.title.strip()].append(t)
        
    print(f"Found {len(trades)} trades across {len(markets)} distinct markets.")
    
    # We want to show a few examples where they made multiple trades,
    # as well as the specific Bitcoin market you requested.
    target_market = "Will Bitcoin dip to $72,000 March 16-22?"
    
    for title, market_trades in markets.items():
        market_trades.sort(key=lambda x: x.timestamp)
        
        positions = defaultdict(float)
        for t in market_trades:
            if t.side.upper() == "BUY":
                positions[t.outcome] += t.size
            else:
                positions[t.outcome] -= t.size
                
        # Only print markets with multiple entries so we can see the strategy, OR the specific targeted market
        if target_market in title or len(market_trades) >= 2:
            print(f"\n{'='*90}")
            print(f"MARKET: {title}")
            print(f"{'-'*90}")
            for t in market_trades:
                print(f"  {t.datetime_utc.strftime('%m-%d %H:%M:%S')} | {t.side:4s} {t.outcome:5s} | Size: {t.size:8.2f} | Price: ${t.price:.3f}")
                
            print("-" * 50)
            print("  NET HELD POSITIONS:")
            for outcome, size in positions.items():
                if abs(size) > 0.01:
                    print(f"    {outcome:5s}: {size:8.2f} shares")

if __name__ == "__main__":
    main()
