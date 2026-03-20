"""
validator_service.py
Validation service to verify that our mirrored trades successfully landed 
on our own proxy wallet.
"""

import logging
import os
from dotenv import load_dotenv
from service.trades_service import fetch_user_trades

load_dotenv()
logger = logging.getLogger(__name__)

def validate_own_trades(limit: int = 5):
    """
    Fetches the recent trades from our OWN proxy wallet to validate execution.
    Prints out exactly what our bot has executed on-chain.
    """
    wallet = os.getenv("poly_address", "").strip(" '\"")
    if not wallet:
        logger.error("Validation skipped: No poly_address found in .env")
        return
        
    logger.info("=" * 60)
    logger.info("  VALIDATOR: Checking Our Own Proxy Wallet Executions")
    logger.info("=" * 60)
    
    try:
        our_trades = fetch_user_trades(wallet, limit=limit)
        if not our_trades:
            logger.info("  No executed trades found on our proxy wallet yet.")
            return
            
        for trade in our_trades:
            usdc_value = trade.size * trade.price
            logger.info(
                "  [EXECUTED] %s %-30s | Size: %.0f | Price: $%.3f | Total: $%.2f",
                trade.side,
                trade.title[:30],
                trade.size,
                trade.price,
                usdc_value
            )
    except Exception as e:
        logger.error("Validation failed to fetch our own trades: %s", e)
    
    logger.info("=" * 60)
