"""
copy_trade_service.py
Service for executing automated copy-trades using Polymarket CLOB.

Order size = CLOB per-market minimum shares at the current market ask price.
A slippage guard skips trades where the market has moved more than
MAX_SLIPPAGE_PCT from the original signal price since coinman detected it.
"""

import logging
import math
import os
import time

import requests
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, BalanceAllowanceParams, AssetType
from py_clob_client.exceptions import PolyApiException
from py_clob_client.order_builder.constants import BUY, SELL

from core.models.trades import TradeEntry
from core.models.yield_trade_result import YieldTradeResult
from utility.geo import is_in_spain

# Fraction of USDC balance used per yield trade (1%)
_DEFAULT_BUDGET_FRACTION = 0.01

load_dotenv()
logger = logging.getLogger(__name__)

_CLOB_HOST = "https://clob.polymarket.com"
_POLYGON_CHAIN_ID = 137
_DEFAULT_MIN_ORDER_SIZE = 5  # fallback if market lookup fails
_CLOB_MIN_NOTIONAL_USD = 1.0  # CLOB rejects orders below $1 notional regardless of share count
_MAX_ORDER_USD = 6.0          # Hard cap per yield trade — skip markets whose minimum forces a larger spend

# Maximum allowed price movement between signal price and current market price.
# With 5-second polling, slippage is usually < 2%. 10% flags something unusual
# (e.g. a stale trade detected late, or a fast-moving illiquid market).
_MAX_SLIPPAGE_PCT = 10.0


def _get_client() -> ClobClient:
    """
    Returns an authenticated ClobClient using POLY_PROXY mode (signature_type=1).

    poly_private_key  → EOA signer.
    poly_funder_address → proxy wallet (maker); holds USDC; must differ from signer.
    CLOB rejects maker==signer with "invalid signature"; type=0 has no USDC balance.
    """
    pk = os.getenv("poly_private_key", "").strip(" '\"")
    funder = os.getenv("poly_funder_address", "").strip(" '\"")
    if not pk:
        raise ValueError("Missing poly_private_key in .env")
    if not funder:
        raise ValueError(
            "Missing poly_funder_address in .env — required for POLY_PROXY order signing"
        )

    client = ClobClient(
        host=_CLOB_HOST,
        key=pk,
        chain_id=_POLYGON_CHAIN_ID,
        signature_type=1,
        funder=funder,
    )
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client


def _get_usdc_balance(pk: str) -> float:
    """Returns available USDC balance from the POLY_PROXY pool (signature_type=1)."""
    try:
        balance_client = ClobClient(
            host=_CLOB_HOST,
            key=pk,
            chain_id=_POLYGON_CHAIN_ID,
            signature_type=1,
        )
        creds = balance_client.create_or_derive_api_creds()
        balance_client.set_api_creds(creds)
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp = balance_client.get_balance_allowance(params)
        raw = resp.get("balance", "0")
        return float(raw) / 1_000_000
    except Exception as e:
        logger.warning("Could not fetch USDC balance: %s", e)
        return 0.0


def _get_min_order_size(client: ClobClient, condition_id: str) -> int:
    """Returns the CLOB minimum order size (shares) for the market, or the default."""
    if not condition_id:
        return _DEFAULT_MIN_ORDER_SIZE
    try:
        market = client.get_market(condition_id)
        return int(market.get("minimum_order_size", _DEFAULT_MIN_ORDER_SIZE))
    except Exception as e:
        logger.warning(
            "Could not fetch minimum_order_size for %s: %s — using default %d",
            condition_id, e, _DEFAULT_MIN_ORDER_SIZE,
        )
        return _DEFAULT_MIN_ORDER_SIZE


def _get_current_market_price(client: ClobClient, token_id: str, side: str) -> float | None:
    """
    Fetch current best ask (BUY) or best bid (SELL) from the live order book.

    Using live price instead of the stale signal price ensures orders fill
    immediately as taker orders.  Returns None if the book is empty or unavailable.
    """
    try:
        book = client.get_order_book(token_id)
        if side.upper() == "BUY":
            prices = sorted([float(a.price) for a in book.asks]) if book.asks else []
        else:
            prices = sorted([float(b.price) for b in book.bids], reverse=True) if book.bids else []
        return prices[0] if prices else None
    except Exception as e:
        logger.warning("Could not fetch order book for token %s: %s", token_id[:20], e)
        return None


def execute_copy_trade(trade: TradeEntry) -> bool:
    """
    Executes a mirrored trade on the Polymarket CLOB API.

    Places a taker order at the current best ask/bid price for the market minimum
    number of shares.  Skips if the market has moved more than _MAX_SLIPPAGE_PCT
    from the signal price, or if the current price is outside the CLOB-valid range (0.01–0.99).

    Args:
        trade: The parsed TradeEntry signal to copy.

    Returns:
        True if order successfully submitted and matched, False otherwise.
    """
    logger.info("=== PREPARING COPY TRADE ===")
    logger.info(
        "Signal: %s | %s | %s @ $%.4f (historical)",
        trade.title, trade.side, trade.outcome, trade.price,
    )

    if not is_in_spain():
        logger.error("Execution blocked: Geo location is not Spain (ES).")
        return False

    token_id = trade.asset
    if not token_id:
        logger.error(
            "Execution blocked: No asset/token_id on trade for condition_id=%s",
            trade.condition_id,
        )
        return False

    if trade.price <= 0.0 or trade.price > 1.0:
        logger.error("Execution blocked: Invalid historical signal price %.6f", trade.price)
        return False

    try:
        pk = os.getenv("poly_private_key", "").strip(" '\"")
        client = _get_client()

        # ── Current market price ───────────────────────────────────────────────
        current_price = _get_current_market_price(client, token_id, trade.side)
        if current_price is None:
            logger.error(
                "Execution blocked: Order book empty or market closed: %s",
                trade.title[:60],
            )
            return False

        # ── Slippage guard ─────────────────────────────────────────────────────
        # With 5s polling the gap is usually < 2%. A larger gap means the trade
        # was detected late or the market moved unusually fast — skip to avoid
        # buying at a significantly worse price than the signal.
        slippage_pct = abs(current_price - trade.price) / trade.price * 100
        if slippage_pct > _MAX_SLIPPAGE_PCT:
            logger.warning(
                "Skipping: slippage %.1f%% exceeds %.0f%% threshold "
                "(signal $%.3f → current $%.3f): %s",
                slippage_pct, _MAX_SLIPPAGE_PCT,
                trade.price, current_price, trade.title[:60],
            )
            return False

        logger.info(
            "Current market price: $%.4f  (signal: $%.4f, slippage: %.1f%%)",
            current_price, trade.price, slippage_pct,
        )

        # ── CLOB valid price range ─────────────────────────────────────────────
        # Official constraint: 0.01–0.99. Outside this the CLOB rejects orders.
        if current_price >= 0.99 or current_price <= 0.01:
            logger.warning(
                "Skipping: current price %.4f is outside CLOB range (0.01–0.99): %s",
                current_price, trade.title[:60],
            )
            return False

        # ── Minimum order size ─────────────────────────────────────────────────
        # Two floors must both be met:
        # 1. Per-market minimum_order_size (shares)
        # 2. CLOB-wide $1 minimum notional — cheap markets need more shares to hit it
        min_size = _get_min_order_size(client, trade.condition_id)
        min_size_for_notional = math.ceil(_CLOB_MIN_NOTIONAL_USD / current_price)
        min_size = max(min_size, min_size_for_notional)
        order_cost = min_size * current_price

        # ── Balance check ──────────────────────────────────────────────────────
        balance = _get_usdc_balance(pk)
        if balance < order_cost:
            logger.error(
                "Execution blocked: Insufficient USDC. Need $%.2f (%d shares × $%.3f), have $%.2f",
                order_cost, min_size, current_price, balance,
            )
            return False

        logger.info(
            "Balance OK: $%.2f available | deploying %d shares @ $%.4f = $%.2f",
            balance, min_size, current_price, order_cost,
        )

        # ── Submit taker order ─────────────────────────────────────────────────
        order_side = BUY if trade.side.upper() == "BUY" else SELL
        args = OrderArgs(
            token_id=token_id,
            price=round(current_price, 2),
            size=float(min_size),
            side=order_side,
        )

        logger.info(
            "Submitting: %s %d shares @ $%.4f (~$%.2f)",
            order_side, min_size, args.price, args.price * args.size,
        )

        signed_order = client.create_order(args)
        resp = client.post_order(signed_order)

        if resp.get("success"):
            logger.info(
                "COPY TRADE SUBMITTED! OrderID=%s status=%s",
                resp.get("orderID"), resp.get("status"),
            )
            return True
        else:
            logger.error("COPY TRADE REJECTED: %s", resp)
            return False

    except PolyApiException as e:
        if e.status_code == 404:
            logger.warning(
                "CLOB market not found (404) — market already closed: %s", trade.title[:60]
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


def execute_yield_trade(
    token_id: str,
    condition_id: str,
    title: str,
    signal_price: float,
    budget_fraction: float = _DEFAULT_BUDGET_FRACTION,
) -> YieldTradeResult:
    """
    Execute a yield farming BUY order on the Polymarket CLOB.

    Order size is determined by: max($1.00 minimum, balance × budget_fraction).
    The CLOB minimum share count and $1 notional floor are both respected.

    Args:
        token_id: CLOB token ID for the outcome to buy.
        condition_id: Market condition ID (used for min order size lookup).
        title: Human-readable market title (for logging only).
        signal_price: Expected price from Gamma API (used for slippage check).
        budget_fraction: Fraction of USDC balance to spend (default 1%).

    Returns:
        YieldTradeResult with order_id, shares, and cost_usd if successful, None values otherwise.
    """
    logger.info("=== PREPARING YIELD TRADE ===")
    logger.info("Market: %s @ $%.4f (signal)", title[:60], signal_price)

    if not is_in_spain():
        logger.error("Execution blocked: Geo location is not Spain (ES).")
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)

    if not token_id:
        logger.error("Execution blocked: No token_id provided.")
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)

    if signal_price <= 0.0 or signal_price > 1.0:
        logger.error("Execution blocked: Invalid signal price %.6f", signal_price)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)

    try:
        pk = os.getenv("poly_private_key", "").strip(" '\"")
        client = _get_client()

        # ── Current market price ───────────────────────────────────────────────
        current_price = _get_current_market_price(client, token_id, "BUY")
        if current_price is None:
            logger.error("Execution blocked: Order book empty or market closed: %s", title[:60])
            return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)

        # ── Slippage guard ─────────────────────────────────────────────────────
        slippage_pct = abs(current_price - signal_price) / signal_price * 100
        if slippage_pct > _MAX_SLIPPAGE_PCT:
            logger.warning(
                "Skipping: slippage %.1f%% exceeds %.0f%% threshold "
                "(signal $%.3f → current $%.3f): %s",
                slippage_pct, _MAX_SLIPPAGE_PCT,
                signal_price, current_price, title[:60],
            )
            return YieldTradeResult(success=False, order_id=None, fill_price=current_price, shares=None, cost_usd=None, balance_before=None)

        logger.info(
            "Current market price: $%.4f  (signal: $%.4f, slippage: %.1f%%)",
            current_price, signal_price, slippage_pct,
        )

        # ── CLOB valid price range ─────────────────────────────────────────────
        if current_price >= 0.99 or current_price <= 0.01:
            logger.warning(
                "Skipping: current price %.4f is outside CLOB range (0.01–0.99): %s",
                current_price, title[:60],
            )
            return YieldTradeResult(success=False, order_id=None, fill_price=current_price, shares=None, cost_usd=None, balance_before=None)

        # ── Dynamic sizing ─────────────────────────────────────────────────────
        # Target budget: max($1 minimum, balance × budget_fraction)
        balance = _get_usdc_balance(pk)
        budget_usd = max(_CLOB_MIN_NOTIONAL_USD, balance * budget_fraction)

        # Shares: satisfy per-market minimum, $1 notional floor, and target budget
        min_market_shares = _get_min_order_size(client, condition_id)
        min_shares_for_notional = math.ceil(_CLOB_MIN_NOTIONAL_USD / current_price)
        target_shares = math.floor(budget_usd / current_price)
        order_size = max(min_market_shares, min_shares_for_notional, target_shares)
        order_cost = order_size * current_price

        # ── Order cap ──────────────────────────────────────────────────────────
        # If the market's minimum_order_size forces the cost above the hard cap,
        # skip rather than silently deploying more capital than intended.
        if order_cost > _MAX_ORDER_USD:
            logger.warning(
                "Skipping: minimum order cost $%.2f exceeds cap $%.2f (%d shares × $%.3f): %s",
                order_cost, _MAX_ORDER_USD, order_size, current_price, title[:60],
            )
            return YieldTradeResult(success=False, order_id=None, fill_price=current_price, shares=order_size, cost_usd=order_cost, balance_before=balance)

        # ── Balance check ──────────────────────────────────────────────────────
        if balance < order_cost:
            logger.error(
                "Execution blocked: Insufficient USDC. Need $%.2f (%d shares × $%.3f), have $%.2f",
                order_cost, order_size, current_price, balance,
            )
            return YieldTradeResult(success=False, order_id=None, fill_price=current_price, shares=order_size, cost_usd=order_cost, balance_before=balance)

        logger.info(
            "Balance: $%.2f | budget: $%.2f | %d shares @ $%.4f = $%.2f",
            balance, budget_usd, order_size, current_price, order_cost,
        )

        # ── Submit taker order ─────────────────────────────────────────────────
        args = OrderArgs(
            token_id=token_id,
            price=round(current_price, 2),
            size=float(order_size),
            side=BUY,
        )

        logger.info(
            "Submitting yield BUY: %d shares @ $%.4f (~$%.2f)",
            order_size, args.price, args.price * args.size,
        )

        t0 = time.perf_counter()
        signed_order = client.create_order(args)
        sign_ms = (time.perf_counter() - t0) * 1000
        logger.info("[TIMING] SDK (create_order / sign) → OK  %.0fms", sign_ms)

        t0 = time.perf_counter()
        resp = client.post_order(signed_order)
        post_ms = (time.perf_counter() - t0) * 1000
        logger.info("[TIMING] SDK (post_order / CLOB submit) → %s  %.0fms",
                    "OK" if resp.get("success") else "REJECTED", post_ms)

        if resp.get("success"):
            order_id = resp.get("orderID")
            logger.info("YIELD TRADE SUBMITTED! OrderID=%s status=%s", order_id, resp.get("status"))
            return YieldTradeResult(success=True, order_id=order_id, fill_price=current_price, shares=order_size, cost_usd=order_cost, balance_before=balance)
        else:
            logger.error("YIELD TRADE REJECTED: %s", resp)
            return YieldTradeResult(success=False, order_id=None, fill_price=current_price, shares=order_size, cost_usd=order_cost, balance_before=balance)

    except PolyApiException as e:
        if e.status_code == 404:
            logger.warning("CLOB market not found (404) — market already closed: %s", title[:60])
        else:
            logger.error("CLOB API error (status=%s): %s", e.status_code, e)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)
    except (ValueError, KeyError) as e:
        logger.error("Invalid yield trade parameters: %s", e)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)
    except requests.RequestException as e:
        logger.error("Network error during yield trade: %s", e)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)
    except Exception as e:
        logger.error("Unexpected error during yield trade: %s", e)
        return YieldTradeResult(success=False, order_id=None, fill_price=None, shares=None, cost_usd=None, balance_before=None)


def execute_stop_loss_sell(
    token_id: str,
    condition_id: str,
    title: str,
    shares: int,
    entry_price: float,
) -> tuple[bool, float | None, int]:
    """
    Execute a stop-loss SELL on the Polymarket CLOB to exit a losing position.

    Sells shares at the current best bid price (taker order, fills immediately).
    The CLOB requires shares × $1.00 USDC as collateral for SELL orders (full face
    value, regardless of sell price). If available USDC is less than that, sells as
    many shares as the balance allows (partial exit) rather than failing entirely.

    Args:
        token_id: CLOB token ID of the outcome we hold.
        condition_id: Market condition ID (for logging only).
        title: Human-readable market title (for logging only).
        shares: Number of shares to sell (our full position).
        entry_price: Price we originally paid (for logging context only).

    Returns:
        (success, exit_price, shares_sold) — shares_sold may be < shares for partial exits.
    """
    logger.info("=== STOP-LOSS SELL ===")
    logger.info("Market: %s | %d shares @ entry $%.4f", title[:60], shares, entry_price)

    if not is_in_spain():
        logger.error("Stop-loss blocked: geo location is not Spain (ES).")
        return False, None, 0

    if not token_id or shares <= 0:
        logger.error("Stop-loss blocked: invalid token_id or shares=%d", shares)
        return False, None, 0

    try:
        client = _get_client()

        # ── Current bid price ──────────────────────────────────────────────────
        bid_price = _get_current_market_price(client, token_id, "SELL")
        if bid_price is None:
            logger.warning("Stop-loss: no bids available for %s — market may be resolving", title[:60])
            return False, None, 0

        if bid_price <= 0.01:
            logger.warning(
                "Stop-loss: bid $%.4f too low — market already resolving, skipping: %s",
                bid_price, title[:60],
            )
            return False, None, 0

        # ── Submit SELL with retry on balance error ─────────────────────────────
        # The CLOB requires shares × $1.00 USDC as collateral for SELL orders.
        # get_balance_allowance() returns total proxy wallet balance and does not
        # reflect the CLOB's internal collateral accounting — it cannot be used
        # reliably to pre-cap shares. Instead: try the full position, and if the
        # CLOB rejects with "not enough balance", reduce by 1 share and retry.
        # Worst case: 5 attempts (~1.5 s) for a 5-share position.
        attempt_shares = shares
        while attempt_shares > 0:
            args = OrderArgs(
                token_id=token_id,
                price=round(bid_price, 2),
                size=float(attempt_shares),
                side=SELL,
            )
            logger.info(
                "Submitting stop-loss SELL: %d shares @ $%.4f (~$%.2f recovered)",
                attempt_shares, args.price, args.price * attempt_shares,
            )
            try:
                signed_order = client.create_order(args)
                resp = client.post_order(signed_order)
                if resp.get("success"):
                    logger.info(
                        "STOP-LOSS EXECUTED! OrderID=%s status=%s recovered=$%.2f",
                        resp.get("orderID"), resp.get("status"), attempt_shares * bid_price,
                    )
                    return True, bid_price, attempt_shares
                else:
                    logger.error("STOP-LOSS ORDER REJECTED: %s", resp)
                    return False, None, 0
            except PolyApiException as e:
                if e.status_code == 400 and "not enough balance" in str(e) and attempt_shares > 1:
                    logger.warning(
                        "Stop-loss: collateral insufficient for %d shares — retrying with %d",
                        attempt_shares, attempt_shares - 1,
                    )
                    attempt_shares -= 1
                    continue
                elif e.status_code == 404:
                    logger.warning("Stop-loss: market not found (404) — already closed: %s", title[:60])
                else:
                    logger.error("Stop-loss CLOB API error (status=%s): %s", e.status_code, e)
                return False, None, 0

        logger.error("Stop-loss: collateral insufficient even for 1 share — cannot exit: %s", title[:60])
        return False, None, 0

    except Exception as e:
        logger.error("Stop-loss unexpected error: %s", e)
        return False, None, 0
