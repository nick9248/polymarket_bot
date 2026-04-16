"""
yield_farming_service.py
Scans the Gamma API for near-expiry markets where one outcome has a high
probability (>= threshold), then executes a BUY order on that outcome.

Strategy:
  - Poll markets closing within `window_minutes` (default 15).
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
import os
import re
from datetime import datetime, timedelta, timezone

from core.models.yield_opportunity import YieldOpportunity
from service.copy_trade_service import execute_yield_trade
from service import db_service, telegram_service
from service.volatility_service import get_btc_volatility_snapshot, get_btc_realized_vol
from utility.constants import REQUEST_TIMEOUT_SECONDS
from utility.endpoints import GAMMA_MARKETS, CLOB_MARKETS, CLOB_BOOK
from utility.http_timing import timed_get

logger = logging.getLogger(__name__)

# How far ahead (minutes) to look for closing markets
_DEFAULT_WINDOW_MINUTES = 15

# Minimum price threshold — below this, skip (too much residual risk)
_DEFAULT_THRESHOLD = 0.95

# Maximum CLOB price we'll accept — CLOB rejects orders >= 0.99, and prices
# above this indicate the market has already locked in with no tradeable spread.
_MAX_CLOB_PRICE = 0.989

# Maximum simultaneous open opportunities per cycle (safety cap)
_MAX_TRADES_PER_CYCLE = 20

# Direction filter — which outcome directions to trade.
# "up"   → only take Up bets (backtest: 98.7% WR, +$11.55 net on 153 trades)
# "down" → only take Down bets
# "both" → no direction filter (default — let the rv guard handle regime risk)
# Set via YIELD_DIRECTION_FILTER env var.
_DIRECTION_FILTER = os.getenv("YIELD_DIRECTION_FILTER", "both").lower().strip()

# Hourly market close-time cap.
# Polymarket has two market types:
#   Short-window: "4:15PM–4:30PM ET" — 5–30min window, we enter at 1–7 min left.
#   Hourly:       "5AM ET" — 1-hour window, we typically see them at 8–15 min left.
#
# Hourly markets at 8–15 min have 3× the loss rate (7.5% vs 2.8%) because more
# time means more room for a price reversal. BUT entered at ≤3 min they behave
# like short-window markets — and by then, markets that have already flipped will
# be below our 0.95 threshold and auto-filtered.
#
# Price history analysis of hourly losses:
#   10524 XRP 6PM (Up): at 3 min price was $0.29–$0.80 → auto-blocked by threshold ✓
#   10753 BTC 1PM (Up): at 2 min price was chaotic $0.04–$0.96 → likely blocked ✓
#   10672 XRP 4AM (Up): at 2 min still $0.96–$0.97, then flipped at expiry → unavoidable ✗
#
# Short-window markets are detected by title pattern; hourly markets get a 3-min cap.
_SHORT_WINDOW_RE = re.compile(r'\d+:\d+[AP]M-\d+:\d+[AP]M', re.IGNORECASE)
_MAX_MINS_HOURLY = 3.0  # only enter hourly markets with ≤3 min to close

# Realized volatility guard — skip entries when BTC 30-min realized vol exceeds this.
# Backtest: Up + rv < 0.50 → 100% WR on 119 trades, EV +$0.146/trade.
# Set via YIELD_MAX_REALIZED_VOL env var. Disabled if set to 0 or empty.
_rv_env = os.getenv("YIELD_MAX_REALIZED_VOL", "0.50").strip()
_MAX_REALIZED_VOL: float | None = float(_rv_env) if _rv_env else None

# Session hours filter disabled — 90-day data shows off-hours win rate is 99.1% vs
# 99.3% in-session (0.2% difference), not worth blocking 56% of trading time.
# Two-phase filter alone handles the main risk. Filter kept in code but never fires.
_SESSION_START_UTC_MINUTES = 0  # 0 = accept all hours

# Markets executed this session — keyed by token_id to prevent re-entry
_executed_token_ids: set[str] = set()

# Market close-windows traded this session — prevents taking a second correlated
# position in the same 5-minute window (e.g. BTC Down + ETH Down closing at same time).
# Module-level so it persists across the 5-second polling cycles within a session.
# Old windows never match new opportunities (close_time has already passed), so no cleanup needed.
_traded_close_windows: set = set()

def _is_trading_session(close_time: datetime) -> bool:
    """
    Returns True if the market closes during active trading hours (9:30AM–8PM ET).

    Outside these hours liquidity drops and late reversals are more common.
    Uses UTC time with a fixed EDT offset (UTC-4). Winter EST shifts the window
    by one hour — acceptable approximation given the evidence pattern.

    Active window: 13:30–00:00 UTC (9:30AM–8PM EDT).
    Skip window:   00:00–13:30 UTC (8PM–9:30AM EDT).
    """
    total_minutes = close_time.hour * 60 + close_time.minute
    return total_minutes >= _SESSION_START_UTC_MINUTES


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
    Fetch the verified CLOB token ID and live best-ask price for a given outcome.

    Two-step resolution:
    1. /markets/{condition_id}  — get the correct token ID (Gamma clobTokenIds are invalid).
    2. /book?token_id={token_id} — get the real best ask price (the price we would actually pay).

    The CLOB markets endpoint "price" field is the last-trade price, which can lag
    significantly behind the live orderbook. Near market close, the last-trade price
    may show 0.96 while the actual ask is already at 0.999. Using the ask prevents
    wasted execution attempts that will fail the CLOB range guard (>= 0.99).

    Args:
        condition_id: Market condition ID.
        outcome_name: Outcome name to match (e.g. "Yes", "No", "Up").

    Returns:
        (token_id, best_ask_price) if found and orderbook has asks, (None, None) otherwise.
    """
    try:
        resp = timed_get(
            f"{CLOB_MARKETS}/{condition_id}",
            label="CLOB market lookup",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if resp.status_code == 404:
            return None, None
        resp.raise_for_status()
        data = resp.json()
        tokens = data.get("tokens", [])

        token_id = None
        for token in tokens:
            if token.get("outcome", "").lower() == outcome_name.lower():
                token_id = token["token_id"]
                break
        # Fallback: pick the highest-priced token if name match fails
        if not token_id and tokens:
            best = max(tokens, key=lambda t: float(t.get("price", 0.0)))
            token_id = best["token_id"]

        if not token_id:
            return None, None

        # Fetch live orderbook to get the real best ask (what we would actually pay).
        book_resp = timed_get(
            CLOB_BOOK,
            label="CLOB orderbook",
            params={"token_id": token_id},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        book_resp.raise_for_status()
        book = book_resp.json()
        asks = book.get("asks", [])
        if not asks:
            logger.debug("Empty orderbook for token %s... — market may be closed.", token_id[:20])
            return None, None

        best_ask = min(float(a["price"]) for a in asks)
        return token_id, best_ask

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
        resp = timed_get(GAMMA_MARKETS, label="Gamma market scan", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
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
    session_skipped = 0

    for candidate in candidates:
        close_time = candidate["close_time"]

        # Session hours filter: skip markets closing outside 9:30AM–8PM ET.
        # Evidence: 3/5 live losses and majority of 90-day backtested losses were
        # in the 8PM–9:30AM ET window. Liquidity drops and reversals increase at night.
        if not _is_trading_session(close_time):
            logger.info(
                "Session filter: skipping %s (%s) — closes at %s UTC (outside 13:30–00:00 window)",
                candidate["title"][:50], candidate["outcome_name"],
                close_time.strftime("%H:%M"),
            )
            session_skipped += 1
            continue

        token_id, clob_price = _resolve_clob_token(
            candidate["condition_id"], candidate["outcome_name"]
        )
        if not token_id:
            # Either market isn't on CLOB yet, is closed, or orderbook is empty
            logger.debug("No CLOB token/ask for %s — skipping.", candidate["title"][:50])
            continue

        if token_id in _executed_token_ids:
            continue

        # Re-check ask against threshold — Gamma price can diverge from live ask.
        # Without this, a 95¢ Gamma signal could result in a <95¢ CLOB ask.
        if clob_price < threshold:
            logger.debug(
                "Skipping: ask $%.4f below threshold %.2f for %s (%s) — Gamma was $%.4f",
                clob_price, threshold, candidate["title"][:50],
                candidate["outcome_name"], candidate["gamma_price"],
            )
            continue

        # Reject when ask is at or above ceiling — CLOB rejects orders >= 0.99, and
        # near-ceiling ask means the spread has vanished and the market has locked in.
        if clob_price >= _MAX_CLOB_PRICE:
            logger.info(
                "Skipping: ask $%.4f >= $%.3f ceiling for %s (%s) — market locked in",
                clob_price, _MAX_CLOB_PRICE, candidate["title"][:50], candidate["outcome_name"],
            )
            continue

        opportunities.append(YieldOpportunity(
            condition_id=candidate["condition_id"],
            token_id=token_id,
            title=candidate["title"],
            outcome=candidate["outcome_name"],
            price=clob_price,
            gamma_price=candidate["gamma_price"],
            close_time=close_time,
        ))

    # Sort by price descending — most certain first
    opportunities.sort(key=lambda o: o.price, reverse=True)

    logger.info(
        "Yield scan: %d polled → %d up/down above %.2f → %d session-filtered → %d ready to execute",
        len(markets), len(candidates), threshold,
        session_skipped, len(opportunities),
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

    # Fetch volatility snapshot once per cycle — cached hourly, never blocks on failure
    vol_snapshot = get_btc_volatility_snapshot()
    btc_dvol = vol_snapshot[0] if vol_snapshot else None
    btc_iv_percentile = vol_snapshot[1] if vol_snapshot else None

    # DVOL regime guard — high implied volatility means crypto can move significantly
    # within the 15-minute window, flipping a near-certain outcome.
    # Thresholds are based on 12-month Deribit DVOL data (Apr 2025 – Apr 2026):
    #   < 50: 79.3% of the year — safe zone, trade normally
    #   50–55: 15.1% — caution zone, raise bar and tighten window
    #   > 55: 5.6% — skip entirely, risk too high
    # Review thresholds quarterly as market regimes shift.
    _DVOL_CAUTION = 50.0
    _DVOL_SKIP = 55.0
    _DVOL_CAUTION_THRESHOLD = 0.975
    _DVOL_CAUTION_MAX_MINUTES = 7.0

    if btc_dvol is not None:
        if btc_dvol > _DVOL_SKIP:
            logger.warning(
                "DVOL guard: DVOL=%.1f > %.0f — skipping entire cycle (high volatility regime)",
                btc_dvol, _DVOL_SKIP,
            )
            return 0
        elif btc_dvol > _DVOL_CAUTION:
            pre_count = len(opportunities)
            now_utc = datetime.now(timezone.utc)
            opportunities = [
                o for o in opportunities
                if o.price >= _DVOL_CAUTION_THRESHOLD
                and (o.close_time - now_utc).total_seconds() / 60 <= _DVOL_CAUTION_MAX_MINUTES
            ]
            logger.info(
                "DVOL guard: DVOL=%.1f in caution zone (%.0f–%.0f) — "
                "raised threshold to %.3f, max %.0f min (%d→%d opportunities)",
                btc_dvol, _DVOL_CAUTION, _DVOL_SKIP,
                _DVOL_CAUTION_THRESHOLD, _DVOL_CAUTION_MAX_MINUTES,
                pre_count, len(opportunities),
            )
    else:
        logger.warning("DVOL guard: no DVOL data available — proceeding without volatility filter")

    if not opportunities:
        logger.info("Yield cycle: no opportunities after DVOL guard.")
        return 0

    # ── Realized vol guard ────────────────────────────────────────────────────
    # Skip the cycle if BTC 30-min realized vol exceeds the configured threshold.
    # Backtest shows the danger zone is rv 0.40–0.60 (93–81% WR); above 0.50 EV
    # turns negative even for Up bets. Cached for 5 min — never blocks on failure.
    if _MAX_REALIZED_VOL:
        btc_rv = get_btc_realized_vol()
        if btc_rv is not None:
            if btc_rv > _MAX_REALIZED_VOL:
                logger.warning(
                    "RV guard: BTC 30-min rv=%.4f > %.2f threshold — skipping cycle (volatile regime)",
                    btc_rv, _MAX_REALIZED_VOL,
                )
                return 0
            else:
                logger.info("RV guard: BTC 30-min rv=%.4f ≤ %.2f — OK", btc_rv, _MAX_REALIZED_VOL)
        else:
            logger.warning("RV guard: realized vol unavailable — proceeding without rv filter")

    # ── Direction filter ──────────────────────────────────────────────────────
    # Only take Up or Down bets based on YIELD_DIRECTION_FILTER env var.
    # Backtest: Up-only → 98.7% WR vs 95.6% baseline. Configurable so we can
    # flip to "down" or "both" when market regime changes without code changes.
    if _DIRECTION_FILTER in ("up", "down"):
        pre_count = len(opportunities)
        opportunities = [o for o in opportunities if o.outcome.lower() == _DIRECTION_FILTER]
        logger.info(
            "Direction filter (%s): %d → %d opportunities",
            _DIRECTION_FILTER, pre_count, len(opportunities),
        )

    if not opportunities:
        logger.info("Yield cycle: no opportunities after direction filter.")
        return 0

    submitted = 0
    for opp in opportunities[:_MAX_TRADES_PER_CYCLE]:
        mins_until_close = (opp.close_time - datetime.now(timezone.utc)).total_seconds() / 60

        # Hourly market guard: short-window markets (explicit range in title like
        # "4:15PM–4:30PM ET") trade freely; hourly markets ("5AM ET") are only
        # entered with ≤3 min to close. At 3 min, markets that have already flipped
        # will be below the 0.95 threshold and auto-filtered; those still at 96–99¢
        # behave identically to short-window markets at the same timing.
        is_short_window = bool(_SHORT_WINDOW_RE.search(opp.title))
        if not is_short_window and mins_until_close > _MAX_MINS_HOURLY:
            logger.info(
                "Hourly guard: skipping %s (%s) @ $%.4f — %.1f min to close > %.0f min cap (hourly market)",
                opp.title[:50], opp.outcome, opp.price, mins_until_close, _MAX_MINS_HOURLY,
            )
            continue

        # Correlation guard: skip if another trade has already been placed in this
        # close-window this session. BTC and ETH closing at the same time are driven
        # by the same macro move — if one goes wrong, the other almost certainly does too.
        if opp.close_time in _traded_close_windows:
            logger.info(
                "Correlation guard: skipping %s (%s) @ $%.4f — window %s already traded this session",
                opp.title[:50], opp.outcome, opp.price,
                opp.close_time.strftime("%Y-%m-%d %H:%M UTC"),
            )
            continue

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
        gamma_clob_spread = round(opp.gamma_price - opp.price, 4)
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
                gamma_clob_spread=gamma_clob_spread,
                minutes_to_close=round(mins_until_close, 2),
                btc_dvol=btc_dvol,
                btc_iv_percentile=btc_iv_percentile,
            )
        except Exception as e:
            logger.error("Failed to write yield trade to DB: %s", e)

        # Always mark the token as seen — success or failure — so the same
        # market is never retried within this session. Without this, a failed
        # trade (e.g. price outside CLOB range) would be re-attempted every
        # 5-second cycle until the market closes, flooding the DB and CLOB API.
        _executed_token_ids.add(opp.token_id)

        if result.success:
            # Claim this close-window so subsequent correlated assets in the same
            # window are skipped for the remainder of the session.
            _traded_close_windows.add(opp.close_time)
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
