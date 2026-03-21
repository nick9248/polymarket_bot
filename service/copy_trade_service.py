"""
copy_trade_service.py
Service for executing automated copy-trades using Polymarket CLOB.
Mirrors signals at a fixed initial entry size of exactly $1.50.
"""

import logging
import os

import requests
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.exceptions import PolyApiException
from py_clob_client.order_builder.constants import BUY, SELL

from core.api.polymarket_client import get_market_token_id
from core.models.trades import TradeEntry
from utility.geo import is_in_spain

load_dotenv()
logger = logging.getLogger(__name__)

# Signature type 1 = Magic/Email Proxy Wallet
_SIGNATURE_TYPE_PROXY = 1
_CLOB_HOST = "https://clob.polymarket.com"
_POLYGON_CHAIN_ID = 137


def _get_client() -> ClobClient:
    """Returns an authenticated ClobClient. Credentials are read fresh each call."""
    pk = os.getenv("poly_private_key", "").strip(" '\"")
    funder_address = os.getenv("poly_address", "").strip(" '\"").lower()

    if not pk or not funder_address:
        raise ValueError("Missing poly_private_key or poly_address in .env")

    client = ClobClient(
        host=_CLOB_HOST,
        key=pk,
        chain_id=_POLYGON_CHAIN_ID,
        signature_type=_SIGNATURE_TYPE_PROXY,
        funder=funder_address,
    )
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client


def _get_usdc_balance(client: ClobClient) -> float:
    """
    Fetch available USDC balance for the proxy wallet.
    Returns 0.0 if the balance cannot be determined.
    """
    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp = client.get_balance_allowance(params)
        raw = resp.get("balance", "0")
        return float(raw) / 1_000_000  # USDC has 6 decimals on Polygon
    except Exception as e:
        logger.warning("Could not fetch USDC balance: %s", e)
        return 0.0


def execute_copy_trade(trade: TradeEntry, trade_size_usd: float = 1.5) -> bool:
    """
    Executes a mirrored trade on the Polymarket CLOB API.

    Args:
        trade: The parsed TradeEntry to copy.
        trade_size_usd: Fixed flat USD size to deploy on the trade (default $1.50).

    Returns:
        True if order successfully submitted, False otherwise.
    """
    logger.info("=== PREPARING COPY TRADE ===")
    logger.info("Target: %s | %s | %s @ $%.4f", trade.title, trade.side, trade.outcome, trade.price)

    if not is_in_spain():
        logger.error("Execution blocked: Geo location is not Spain (ES).")
        return False

    token_id = get_market_token_id(trade.condition_id, trade.outcome_index)
    if not token_id:
        logger.error("Execution blocked: Could not resolve token_id for condition_id=%s", trade.condition_id)
        return False

    # Price must be in the valid Polymarket range (exclusive of 0, inclusive of 1)
    if trade.price <= 0.0 or trade.price > 1.0:
        logger.error("Execution blocked: Invalid trade price %.6f (must be 0 < price <= 1.0)", trade.price)
        return False

    # Skip near-expiry markets: price > 0.85 or < 0.15 means the market is almost resolved.
    # Polymarket's CLOB closes order submission on these markets before resolution,
    # causing guaranteed 404 "market not found" rejections.
    if trade.price > 0.85 or trade.price < 0.15:
        logger.warning(
            "Skipping near-expiry market (price=%.3f). CLOB likely closed for new orders: %s",
            trade.price, trade.title[:60],
        )
        return False

    shares_to_buy = round(trade_size_usd / trade.price, 4)
    if shares_to_buy < 0.01:
        logger.error("Execution blocked: Calculated shares %.4f too small for $%.2f at price %.4f",
                     shares_to_buy, trade_size_usd, trade.price)
        return False

    try:
        client = _get_client()

        # Balance check — ensure we have enough USDC before placing the order
        balance = _get_usdc_balance(client)
        if balance < trade_size_usd:
            logger.error(
                "Execution blocked: Insufficient USDC balance. Need $%.2f, have $%.2f",
                trade_size_usd, balance,
            )
            return False
        logger.info("Balance check passed: $%.2f available, deploying $%.2f", balance, trade_size_usd)

        order_side = BUY if trade.side.upper() == "BUY" else SELL

        args = OrderArgs(
            token_id=token_id,
            price=round(trade.price, 3),
            size=shares_to_buy,
            side=order_side,
        )

        logger.info(
            "Submitting Limit Order: Side=%s, Price=$%.3f, Shares=%.4f (~$%.2f)",
            order_side, args.price, args.size, args.price * args.size,
        )

        signed_order = client.create_order(args)
        resp = client.post_order(signed_order)

        if resp.get("success"):
            logger.info("COPY TRADE SUBMITTED! OrderID: %s", resp.get("orderID"))
            return True
        else:
            logger.error("COPY TRADE REJECTED: %s", resp)
            return False

    except PolyApiException as e:
        if e.status_code == 404:
            logger.warning(
                "CLOB market not found (404) — market already closed for trading: %s",
                trade.title[:60],
            )
        else:
            logger.error("CLOB API error (status=%s): %s", e.status_code, e)
        return False
    except (ValueError, KeyError) as e:
        logger.error("Invalid trade parameters: %s", e)
        return False
    except requests.RequestException as e:
        logger.error("Network error during copy trade: %s", e)
        return False
    except Exception as e:
        logger.error("Unexpected error during copy trade: %s", e)
        return False
