"""
monitor_service.py
Polls the Polymarket positions API to advance yield_trades lifecycle statuses
and fires Telegram alerts on state transitions.

Called once per yield farming cycle (after execution). Also handles the daily
summary at 23:00 UTC.

Lifecycle: submitted → filled → won | lost (detected via curPrice in open positions)
Won/lost positions remain in the open positions API until claimed (redeemable=true).
Resolution is detected by curPrice >= 0.99 (won) or curPrice <= 0.01 (lost), NOT by
disappearance from the open positions list.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from service import db_service, telegram_service
from utility.constants import REQUEST_TIMEOUT_SECONDS
from utility.endpoints import POSITIONS
from utility.http_timing import timed_get

load_dotenv()
logger = logging.getLogger(__name__)

_OUR_WALLET = os.getenv("poly_funder_address", "").strip(" '\"")
_STUCK_HOURS = 4           # flag trades unresolved after this many hours (markets close within 30 min)
_SETTLE_DELAY_MINUTES = 30 # mark settled_at this long after resolved_at
_BALANCE_WARNING_MULTIPLIER = 2.0  # warn when balance < N × floor

# Stop-loss parameters
# Trigger a SELL when CLOB curPrice drops below this threshold on a filled trade.
# Based on EV analysis: exiting at $0.50 recovers ~$2.50 vs full $4.83 loss.
_STOP_LOSS_THRESHOLD = 0.50
# Only trigger if at least this many minutes remain before market close.
# Prevents pointless sells when the market is about to resolve anyway.
_STOP_LOSS_MIN_MINS_TO_CLOSE = 1.0

# Track last daily summary date to avoid double-sending
_last_daily_summary_date: str | None = None

# Deduplication for balance warnings — only fire once per hour to prevent Telegram spam
_last_balance_warning_at: datetime | None = None
_BALANCE_WARNING_COOLDOWN = timedelta(hours=1)


def _compute_minutes_remaining(trade: dict, now_utc: datetime) -> float | None:
    """
    Estimate minutes until market close for a trade.

    Uses submitted_at + minutes_to_close (recorded at entry) to derive the
    expected close time, then subtracts current time.  Returns None if either
    field is missing or unparseable.
    """
    submitted_at = trade.get("submitted_at")
    minutes_to_close = trade.get("minutes_to_close")
    if not submitted_at or minutes_to_close is None:
        return None
    try:
        if isinstance(submitted_at, str):
            submitted_dt = datetime.fromisoformat(submitted_at)
        else:
            submitted_dt = submitted_at
        if submitted_dt.tzinfo is None:
            submitted_dt = submitted_dt.replace(tzinfo=timezone.utc)
        market_close = submitted_dt + timedelta(minutes=float(minutes_to_close))
        return (market_close - now_utc).total_seconds() / 60
    except Exception as e:
        logger.warning("Monitor: could not compute minutes_remaining for trade %s: %s", trade.get("id"), e)
        return None


def _fetch_open_positions() -> list[dict]:
    """Fetch all current open positions for our wallet."""
    try:
        resp = timed_get(POSITIONS, label="positions poll", params={"user": _OUR_WALLET, "limit": 500}, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Monitor: could not fetch open positions: %s", e)
        return []



def _trigger_stop_loss(trade: dict, cur_price: float, mins_left: float, now_utc: datetime) -> None:
    """
    Execute a stop-loss SELL for a filled yield trade whose CLOB price has dropped
    below _STOP_LOSS_THRESHOLD with meaningful time remaining before market close.

    Marks the trade as 'stopped' on success and fires a Telegram alert.
    On failure, logs and returns without marking — monitor will retry next cycle.
    """
    from service import copy_trade_service
    trade_id = trade["id"]
    shares = int(trade.get("shares") or 0)

    if not shares:
        logger.warning("Stop-loss: trade %d has no shares recorded — cannot sell", trade_id)
        return

    logger.warning(
        "Stop-loss trigger: trade %d curPrice=$%.4f < %.2f | %.1f min left | %s",
        trade_id, cur_price, _STOP_LOSS_THRESHOLD, mins_left, trade["title"][:50],
    )

    entry_price = float(trade.get("fill_price") or trade.get("signal_price") or cur_price)
    total_shares = shares
    success, exit_price, shares_sold = copy_trade_service.execute_stop_loss_sell(
        token_id=trade["token_id"],
        condition_id=trade["condition_id"],
        title=trade["title"],
        shares=shares,
        entry_price=entry_price,
    )

    if success and exit_price is not None and shares_sold > 0:
        cost_usd = float(trade.get("cost_usd") or 0)
        recovered_usd = shares_sold * exit_price
        # pnl = recovered − full cost. Remaining (unsold) shares are assumed to
        # resolve worthless — the stop-loss only triggers when price < $0.50 and
        # the market is heading to zero. Charging full cost_usd here gives the
        # accurate total pnl for the position rather than an optimistic partial figure.
        pnl = round(recovered_usd - cost_usd, 4)
        partial = shares_sold < total_shares

        db_service.update_yield_trade(
            trade_id,
            status="stopped",
            pnl_usd=pnl,
            stop_loss_exit_price=round(exit_price, 4),
            stop_loss_at=now_utc,
        )
        logger.info(
            "Stop-loss complete: trade %d | sold %d/%d @ $%.4f | recovered $%.2f | pnl=$%.4f%s",
            trade_id, shares_sold, total_shares, exit_price, recovered_usd, pnl,
            " [PARTIAL EXIT]" if partial else "",
        )
        summary = db_service.get_yield_pnl_summary()
        telegram_service.send_stop_loss_triggered(
            title=trade["title"],
            outcome=trade["outcome"],
            shares=shares_sold,
            exit_price=exit_price,
            recovered_usd=recovered_usd,
            cost_usd=cost_usd,
            pnl_usd=pnl,
            session_net_pnl=summary["net_pnl"],
        )
    else:
        logger.warning(
            "Stop-loss SELL failed for trade %d — will retry next cycle (%s)",
            trade_id, trade["title"][:50],
        )


def poll_lifecycle() -> None:
    """
    Advance statuses for all open yield_trades rows.
    Fires Telegram alerts on each transition.
    Called once per cycle.
    """
    if not _OUR_WALLET:
        logger.warning("Monitor: poly_funder_address not set — skipping lifecycle poll.")
        return

    open_trades = db_service.get_open_yield_trades()
    if not open_trades:
        return

    now_utc = datetime.now(timezone.utc)
    open_positions = _fetch_open_positions()

    # Build lookup: (conditionId, outcome_lower) → position dict for open positions.
    # Outcome strings are lowercased on both sides to guard against API case divergence
    # (Gamma returns "Down", positions API may return "down" or "DOWN").
    open_pos_lookup: dict[tuple[str, str], dict] = {}
    for pos in open_positions:
        key = (pos.get("conditionId", ""), pos.get("outcome", "").lower())
        open_pos_lookup[key] = pos

    for trade in open_trades:
        trade_id = trade["id"]
        condition_id = trade["condition_id"]
        outcome = trade["outcome"].lower()  # normalize for consistent key matching
        key = (condition_id, outcome)
        cost = float(trade["cost_usd"] or 0)
        submitted_at = trade.get("submitted_at")

        if key in open_pos_lookup:
            pos = open_pos_lookup[key]
            cur_price = float(pos.get("curPrice", 0.5))

            # ── resolved won: curPrice settled at $1 ──────────────────────────
            # Polymarket keeps redeemable positions in open positions until claimed.
            # We detect resolution via curPrice, not by waiting for disappearance.
            #
            # IMPORTANT: do NOT use cashPnl from the positions API. For positions
            # entered close to resolution, Polymarket's cashPnl includes unrealised
            # gains from before our entry (uses the market's initialValue from
            # inception, not from our fill price), returning a grossly inflated
            # figure (e.g. $4.94 instead of $0.06 for a trade bought at $0.988).
            # The correct pnl is: shares_redeemed × $1.00 − our cost_usd.
            if cur_price >= 0.99:
                shares_held = int(trade.get("shares") or 0)
                if shares_held > 0:
                    pnl = round(float(shares_held) - cost, 4)
                else:
                    # Fallback if shares not recorded (shouldn't happen for yield trades)
                    pnl = round(float(pos.get("currentValue", cost)) - cost, 4)
                db_service.update_yield_trade(
                    trade_id,
                    status="won",
                    pnl_usd=round(pnl, 4),
                    resolved_at=now_utc,
                )
                logger.info("Monitor: trade %d WON — pnl=$%.4f (%s)", trade_id, pnl, trade["title"][:50])
                summary = db_service.get_yield_pnl_summary()
                telegram_service.send_yield_trade_won(
                    title=trade["title"],
                    outcome=trade["outcome"],
                    pnl_usd=pnl,
                    session_net_pnl=summary["net_pnl"],
                    win_rate=summary["win_rate"],
                )
                continue

            # ── resolved lost: curPrice settled at $0 ────────────────────────
            elif cur_price <= 0.01:
                db_service.update_yield_trade(
                    trade_id,
                    status="lost",
                    pnl_usd=-cost,
                    resolved_at=now_utc,
                )
                logger.info("Monitor: trade %d LOST — cost=$%.4f (%s)", trade_id, cost, trade["title"][:50])
                summary = db_service.get_yield_pnl_summary()
                telegram_service.send_yield_trade_lost(
                    title=trade["title"],
                    outcome=trade["outcome"],
                    loss_usd=cost,
                    session_net_pnl=summary["net_pnl"],
                    win_rate=summary["win_rate"],
                )
                continue

            # ── still active: advance submitted → filled ──────────────────────
            elif trade["status"] == "submitted":
                db_service.update_yield_trade(trade_id, status="filled", fill_price=cur_price)
                logger.info("Monitor: trade %d status → filled @ $%.4f (%s)", trade_id, cur_price, trade["title"][:50])

            # ── stop-loss check for filled positions ──────────────────────────
            # If curPrice drops below threshold with enough time left, sell now
            # to recover partial capital rather than losing the full position.
            if trade["status"] == "filled" and cur_price < _STOP_LOSS_THRESHOLD:
                mins_left = _compute_minutes_remaining(trade, now_utc)
                if mins_left is not None and mins_left > _STOP_LOSS_MIN_MINS_TO_CLOSE:
                    _trigger_stop_loss(trade, cur_price, mins_left, now_utc)

        else:
            # Position not in open positions — either the order never filled, or the
            # position was redeemed externally (e.g. manually via Polymarket UI) before
            # the bot could match it. Distinguish by current status:
            #
            #   submitted → order likely expired/unfilled → mark 'expired' (no cost incurred)
            #   filled    → position existed but is gone  → mark 'error' with detail note
            #
            # Only act after _STUCK_HOURS to allow for API lag and slow fills.
            if submitted_at:
                try:
                    submitted_dt = datetime.fromisoformat(str(submitted_at)) if isinstance(submitted_at, str) else submitted_at
                    if submitted_dt.tzinfo is None:
                        submitted_dt = submitted_dt.replace(tzinfo=timezone.utc)
                    if (now_utc - submitted_dt).total_seconds() > _STUCK_HOURS * 3600:
                        current_status = trade["status"]
                        if current_status == "submitted":
                            # Order never appeared in positions — treat as never filled
                            db_service.update_yield_trade(trade_id, status="expired", pnl_usd=0.0, resolved_at=now_utc)
                            logger.warning("Monitor: trade %d expired (never filled) — marking expired, pnl=0", trade_id)
                            telegram_service.send_yield_error(
                                context=f"Trade {trade_id} expired — order never filled",
                                error=f"Market: {trade['title'][:80]} | Outcome: {trade['outcome']} | Submitted: {submitted_dt.strftime('%H:%M UTC')}"
                            )
                        else:
                            # status == 'filled': position existed on-chain but was redeemed before bot tracked it
                            db_service.update_yield_trade(trade_id, status="error")
                            logger.warning("Monitor: trade %d filled but position gone — likely redeemed externally", trade_id)
                            telegram_service.send_yield_error(
                                context=f"Trade {trade_id} — position redeemed externally",
                                error=f"Market: {trade['title'][:80]} | Outcome: {trade['outcome']} | Redeem manually and check P&L"
                            )
                except Exception as e:
                    logger.warning("Monitor: error parsing submitted_at for trade %d: %s", trade_id, e)

        # ── resolved → settled (30min delay) ─────────────────────────────────
        if trade.get("resolved_at") and not trade.get("settled_at"):
            try:
                resolved_dt = datetime.fromisoformat(str(trade["resolved_at"])) if isinstance(trade["resolved_at"], str) else trade["resolved_at"]
                if resolved_dt.tzinfo is None:
                    resolved_dt = resolved_dt.replace(tzinfo=timezone.utc)
                if (now_utc - resolved_dt).total_seconds() > _SETTLE_DELAY_MINUTES * 60:
                    db_service.update_yield_trade(trade_id, settled_at=now_utc)
                    logger.info("Monitor: trade %d marked settled", trade_id)
            except Exception as e:
                logger.warning("Monitor: error parsing resolved_at for trade %d: %s", trade_id, e)


def check_balance_warning(current_balance: float) -> None:
    """Fire a Telegram alert if balance is approaching the floor threshold.
    Fires at most once per hour to prevent Telegram spam across 5-second cycles."""
    global _last_balance_warning_at
    from service.risk_guard_service import get_balance_floor
    balance_floor = get_balance_floor()
    if current_balance < balance_floor * _BALANCE_WARNING_MULTIPLIER:
        now = datetime.now(timezone.utc)
        if _last_balance_warning_at and (now - _last_balance_warning_at) < _BALANCE_WARNING_COOLDOWN:
            return
        _last_balance_warning_at = now
        logger.warning("Monitor: balance $%.2f is below 2× floor ($%.2f)", current_balance, balance_floor)
        telegram_service.send_balance_warning(current_balance=current_balance, floor=balance_floor)


def send_daily_summary_if_due(current_balance: float) -> None:
    """
    Send the daily P&L summary if the current UTC hour is 23 and
    we have not sent one today yet.
    """
    global _last_daily_summary_date
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour != 23:
        return
    today_str = now_utc.strftime("%Y-%m-%d")
    if _last_daily_summary_date == today_str:
        return

    try:
        summary = db_service.get_yield_pnl_summary()
        telegram_service.send_yield_daily_summary(
            total_trades=summary["total_trades"],
            won=summary["won"],
            lost=summary["lost"],
            win_rate=summary["win_rate"],
            net_pnl=summary["net_pnl"],
            current_balance=current_balance,
        )
        _last_daily_summary_date = today_str
        logger.info("Monitor: daily summary sent for %s", today_str)
    except Exception as e:
        logger.error("Monitor: failed to send daily summary: %s", e)
