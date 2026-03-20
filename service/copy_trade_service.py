"""
copy_trade_service.py
Service for executing automated copy-trades using Polymarket CLOB.
Mirrors signals at a fixed initial entry size of exactly $2.00.
"""

import os
import logging
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY, SELL

from core.models.trades import TradeEntry
from core.api.polymarket_client import get_market_token_id
from utility.geo import is_in_spain

load_dotenv()
logger = logging.getLogger(__name__)

pk = os.getenv("poly_private_key", "").strip(" '\"")
funder_address = os.getenv("poly_address", "").strip(" '\"").lower()

def _get_client() -> ClobClient:
    """Returns an authenticated ClobClient for the proxy wallet."""
    if not pk or not funder_address:
        raise ValueError("Missing poly_private_key or poly_address in .env")
        
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=pk,
        chain_id=137,
        signature_type=1, # Magic/Email Proxy Wallet type
        funder=funder_address
    )
    
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client

def execute_copy_trade(trade: TradeEntry, trade_size_usd: float = 2.0) -> bool:
    """
    Executes a mirrored trade on the Polymarket CLOB API.
    
    Args:
        trade: The parsed TradeEntry to copy.
        trade_size_usd: Fixed flat USD size to deploy on the trade.
        
    Returns:
        True if order successfully submitted, False otherwise.
    """
    logger.info("=== PREPARING COPY TRADE ===")
    logger.info(f"Target: {trade.title} | {trade.side} | {trade.outcome} @ ${trade.price}")
    
    if not is_in_spain():
        logger.error("Execution blocked: Geo location is not Spain (ES).")
        return False
        
    token_id = get_market_token_id(trade.condition_id, trade.outcome_index)
    if not token_id:
        logger.error("Execution blocked: Could not resolve token_id.")
        return False
        
    try:
        client = _get_client()
        
        # Determine exact order size in shares based on fixed total USD execution
        if trade.price <= 0.0 or trade.price >= 1.0:
            logger.error("Execution blocked: Invalid trade price %s", trade.price)
            return False
            
        shares_to_buy = round(trade_size_usd / trade.price, 2)
        if shares_to_buy < 1.0:
            logger.error("Execution blocked: Calculated shares (%.2f) too small.", shares_to_buy)
            return False
            
        order_side = BUY if trade.side.upper() == "BUY" else SELL
        
        args = OrderArgs(
            token_id=token_id,
            price=round(trade.price, 3), # Match exact price of the copied trader
            size=shares_to_buy,
            side=order_side
        )
        
        logger.info("Submitting Limit Order: Side=%s, Price=$%.3f, Shares=%.2f (~$%.2f)", 
                    order_side, args.price, args.size, (args.price * args.size))
                    
        signed_order = client.create_order(args)
        resp = client.post_order(signed_order)
        
        if resp.get("success"):
            order_id = resp.get("orderID")
            logger.info("✅ COPY TRADE SUBMITTED! OrderID: %s", order_id)
            return True
        else:
            logger.error("❌ COPY TRADE REJECTED: %s", resp)
            return False
            
    except Exception as e:
        logger.error("Exception during copy trade execution: %s", e)
        return False
