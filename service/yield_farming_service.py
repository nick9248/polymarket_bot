"""
yield_farming_service.py
Scans the Gamma API for near-expiry markets where one outcome has a high
probability (>= threshold), then executes a BUY order on that outcome.

Strategy:
  - Poll markets closing within `window_minutes` (default 5).
  - Find any outcome with price >= threshold (default 0.95).
  - Buy that outcome — it's near-certain, collect the spread.
  - No directional prediction needed — purely mechanical execution.

Token ID resolution:
  - Gamma API `clobTokenIds` are NOT valid CLOB token IDs (returns 404 on CLOB).
  - Correct token IDs come from the CLOB's public /markets/{condition_id} endpoint.
  - Gamma is used only for discovery (markets closing soon + price filter).
  - CLOB is the source of truth for token IDs and live prices.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import requests

from core.models.yield_opportunity import YieldOpportunity
from service.copy_trade_service import execute_yield_trade
from service import db_service, telegram_service
from utility.constants import REQUEST_TIMEOUT_SECONDS
from utility.endpoints import GAMMA_MARKETS, CLOB_MARKETS

logger = logging.getLogger(__name__)

# How far ahead (minutes) to look for closing markets
_DEFAULT_WINDOW_MINUTES = 5

# Minimum price threshold — below this, skip (too much residual risk)
_DEFAULT_THRESHOLD = 0.95

# Maximum simultaneous open opportunities per cycle (safety cap)
_MAX_TRADES_PER_CYCLE = 20

# Markets executed this session — keyed by token_id to prevent re-entry
_executed_token_ids: set[str] = set()

def _is_updown_market(title: str) -> bool:
    """
    Returns True if the market is an Up/Down price-direction market.

    These are the only market type with a locked-in outcome near close —
    the price direction is established and reversals in the final minutes
    are rare. Covers crypto, stocks, forex, and any future asset class
    Polymarket adds in the same format, without needing a ticker list.

    Sports, politics, and other event markets never use this title format.
    """
    return "up or down" in title.lower()


def _resolve_clob_token(condition_id: str, outcome_name: str) -> tuple[str, float] | tuple[None, None]:
    """
    Fetch the verified CLOB token ID and live price for a given outcome.

    Uses the public CLOB /markets/{condition_id} endpoint — the only reliable
    source of valid token IDs. Gamma API clobTokenIds are not valid on the CLOB.

    Args:
        condition_id: Market condition ID.
        outcome_name: Outcome name to match (e.g. "Yes", "No", "Up").

    Returns:
        (token_id, price) if found, (None, None) otherwise.
    """
    try:
        resp = requests.get(
            f"{CLOB_MARKETS}/{condition_id}",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if resp.status_code == 404:
            return None, None
        resp.raise_for_status()
        data = resp.json()
        tokens = data.get("tokens", [])
        for token in tokens:
            if token.get("outcome", "").lower() == outcome_name.lower():
                return token["token_id"], float(token.get("price", 0.0))
        # Fallback: return the highest-priced token if name match fails
        if tokens:
            best = max(tokens, key=lambda t: float(t.get("price", 0.0)))
            return best["token_id"], float(best.get("price", 0.0))
    except Exception as e:
        logger.warning("CLOB token lookup failed for %s: %s", condition_id[:20], e)
    return None, None


def scan_opportunities(
    threshold: float = _DEFAULT_THRESHOLD,
    window_minutes: int = _DEFAULT_WINDOW_MINUTES,
) -> list[YieldOpportunity]:
    """
    Query the Gamma API for active markets closing within the next window_minutes,
    filter by price threshold, then resolve correct CLOB token IDs for each.

    Args:
        threshold: Minimum outcome price to qualify (e.g. 0.95).
        window_minutes: How many minutes ahead to look for closing markets.

    Returns:
        List of YieldOpportunity sorted by price descending (most certain first).
    """
    now_utc = datetime.now(timezone.utc)
    end_min = now_utc.isoformat()
    end_max = (now_utc + timedelta(minutes=window_minutes)).isoformat()

    params = {
        "end_date_min": end_min,
        "end_date_max": end_max,
        "active": "true",
        "closed": "false",
        "archived": "false",
        "limit": 200,
    }

    try:
        resp = requests.get(GAMMA_MARKETS, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        markets = resp.json()
    except Exception as e:
        logger.error("Gamma API scan failed: %s", e)
        return []

    # Step 1: filter by price threshold using Gamma data (cheap — no extra API calls)
    candidates: list[dict] = []
    for market in markets:
        condition_id = market.get("conditionId", "")
        title = market.get("question") or market.get("title", "")
        end_date_str = market.get("endDate") or market.get("endDateIso", "")
        outcome_prices_raw = market.get("outcomePrices", "[]")
        outcomes_raw = market.get("outcomes", "[]")

        if not condition_id or not end_date_str:
            continue

        if not _is_updown_market(title):
            continue

        try:
            close_time = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            outcome_prices = (
                json.loads(outcome_prices_raw)
                if isinstance(outcome_prices_raw, str)
                else outcome_prices_raw
            )
            outcomes = (
                json.loads(outcomes_raw)
                if isinstance(outcomes_raw, str)
                else outcomes_raw
            )
        except Exception:
            continue

        # Gamma API ignores end_date_min — filter out already-closed markets locally
        if close_time <= now_utc:
            continue

        if not outcome_prices:
            continue

        for i, price_str in enumerate(outcome_prices):
            try:
                price = float(price_str)
            except (ValueError, TypeError):
                continue

            if price < threshold:
                continue

            outcome_name = outcomes[i] if outcomes and i < len(outcomes) else f"Outcome {i}"
            candidates.append({
                "condition_id": condition_id,
                "title": title,
                "close_time": close_time,
                "outcome_name": outcome_name,
                "gamma_price": price,
            })

    # Step 2: resolve verified CLOB token IDs only for candidates that passed the filter
    opportunities: list[YieldOpportunity] = []
    for candidate in candidates:
        token_id, clob_price = _resolve_clob_token(
            candidate["condition_id"], candidate["outcome_name"]
        )
        if not token_id:
            logger.debug("No CLOB token found for %s — market may already be closed.", candidate["title"][:50])
            continue

        if token_id in _executed_token_ids:
            continue

        # Re-check CLOB price against threshold — Gamma price can diverge from live CLOB
        # price (stale Gamma data, fallback token selection, or rapid price movement).
        # Without this, a 95¢ Gamma signal could result in a 49¢ CLOB buy.
        if clob_price < threshold:
            logger.debug(
                "Skipping: CLOB price $%.4f below threshold %.2f for %s (%s) — Gamma was $%.4f",
                clob_price, threshold, candidate["title"][:50],
                candidate["outcome_name"], candidate["gamma_price"],
            )
            continue

        # Use CLOB live price as the authoritative price (Gamma price is used only for pre-filter)
        opportunities.append(YieldOpportunity(
            condition_id=candidate["condition_id"],
            token_id=token_id,
            title=candidate["title"],
            outcome=candidate["outcome_name"],
            price=clob_price,
            close_time=candidate["close_time"],
        ))

    # Sort by price descending — most certain first
    opportunities.sort(key=lambda o: o.price, reverse=True)

    logger.info(
        "Yield scan: %d market(s) polled → %d up/down candidate(s) above %.2f → %d with valid CLOB token",
        len(markets), len(candidates), threshold, len(opportunities),
    )

    return opportunities


def run_yield_farming_cycle(
    threshold: float = _DEFAULT_THRESHOLD,
    window_minutes: int = _DEFAULT_WINDOW_MINUTES,
    budget_fraction: float = 0.01,
    dry_run: bool = False,
    session_balance_start: float = 0.0,
) -> int:
    """
    One full yield farming cycle: scan → filter → execute → record.

    Args:
        threshold: Minimum price to act on (e.g. 0.95).
        window_minutes: Look-ahead window for closing markets.
        budget_fraction: Fraction of USDC balance to spend per trade (default 1%).
        dry_run: If True, log what would be traded but submit no orders and write no DB rows.
        session_balance_start: USDC balance when the bot session started (written to each trade row).

    Returns:
        Number of orders successfully submitted (or would-be submitted in dry_run) this cycle.
    """
    opportunities = scan_opportunities(threshold=threshold, window_minutes=window_minutes)

    if not opportunities:
        logger.info("Yield cycle: no qualifying opportunities found.")
        return 0

    submitted = 0
    for opp in opportunities[:_MAX_TRADES_PER_CYCLE]:
        mins_until_close = (opp.close_time - datetime.now(timezone.utc)).total_seconds() / 60
        logger.info(
            "%sOpportunity: %s | %s @ $%.4f | closes in %.1f min",
            "[DRY-RUN] " if dry_run else "",
            opp.title[:55], opp.outcome, opp.price, mins_until_close,
        )

        if dry_run:
            logger.info(
                "[DRY-RUN] Would execute: token=%s... condition=%s...",
                opp.token_id[:20], opp.condition_id[:20],
            )
            submitted += 1
            continue

        result = execute_yield_trade(
            token_id=opp.token_id,
            condition_id=opp.condition_id,
            title=opp.title,
            signal_price=opp.price,
            budget_fraction=budget_fraction,
        )

        # Record every attempt (success or failure) to the DB for monitoring
        status = "submitted" if result.success else "error"
        try:
            db_service.insert_yield_trade(
                token_id=opp.token_id,
                condition_id=opp.condition_id,
                title=opp.title,
                outcome=opp.outcome,
                signal_price=opp.price,
                fill_price=result.fill_price,
                shares=result.shares,
                cost_usd=result.cost_usd,
                clob_order_id=result.order_id,
                status=status,
                session_balance_start=session_balance_start,
                balance_before=result.balance_before or 0.0,
            )
        except Exception as e:
            logger.error("Failed to write yield trade to DB: %s", e)

        # Always mark the token as seen — success or failure — so the same
        # market is never retried within this session. Without this, a failed
        # trade (e.g. price outside CLOB range) would be re-attempted every
        # 5-second cycle until the market closes, flooding the DB and CLOB API.
        _executed_token_ids.add(opp.token_id)

        if result.success:
            submitted += 1
            logger.info("Yield trade submitted: %s (%s)", opp.title[:55], opp.outcome)
            # Send Telegram alert for successful submission
            if result.fill_price is not None and result.shares is not None and result.cost_usd is not None:
                try:
                    balance_after = (result.balance_before or 0.0) - result.cost_usd
                    telegram_service.send_yield_trade_submitted(
                        title=opp.title,
                        outcome=opp.outcome,
                        fill_price=result.fill_price,
                        shares=result.shares,
                        cost_usd=result.cost_usd,
                        balance_after=balance_after,
                    )
                except Exception as e:
                    logger.warning("Failed to send submission Telegram alert: %s", e)
        else:
            logger.warning("Yield trade failed: %s (%s)", opp.title[:55], opp.outcome)

    logger.info(
        "Yield cycle complete%s: %d/%d trade(s) submitted.",
        " [DRY-RUN]" if dry_run else "",
        submitted,
        len(opportunities[:_MAX_TRADES_PER_CYCLE]),
    )
    return submitted
