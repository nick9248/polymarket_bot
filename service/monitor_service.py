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

import requests
from dotenv import load_dotenv

from service import db_service, telegram_service
from utility.constants import REQUEST_TIMEOUT_SECONDS
from utility.endpoints import POSITIONS

load_dotenv()
logger = logging.getLogger(__name__)

_OUR_WALLET = os.getenv("poly_funder_address", "").strip(" '\"")
_STUCK_HOURS = 24          # flag trades unresolved after this many hours
_SETTLE_DELAY_MINUTES = 30 # mark settled_at this long after resolved_at
_BALANCE_WARNING_MULTIPLIER = 2.0  # warn when balance < N × floor

# Track last daily summary date to avoid double-sending
_last_daily_summary_date: str | None = None


def _fetch_open_positions() -> list[dict]:
    """Fetch all current open positions for our wallet."""
    try:
        resp = requests.get(POSITIONS, params={"user": _OUR_WALLET, "limit": 500}, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Monitor: could not fetch open positions: %s", e)
        return []



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
            if cur_price >= 0.99:
                cash_pnl = float(pos.get("cashPnl", 0))
                pnl = cash_pnl if cash_pnl > 0 else (float(pos.get("currentValue", 0)) - float(pos.get("initialValue", cost)))
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

        else:
            # Position gone from open positions entirely — either claimed or never filled.
            # Flag as stuck if unresolved after _STUCK_HOURS.
            if submitted_at:
                try:
                    submitted_dt = datetime.fromisoformat(str(submitted_at)) if isinstance(submitted_at, str) else submitted_at
                    if submitted_dt.tzinfo is None:
                        submitted_dt = submitted_dt.replace(tzinfo=timezone.utc)
                    if (now_utc - submitted_dt).total_seconds() > _STUCK_HOURS * 3600:
                        db_service.update_yield_trade(trade_id, status="error")
                        logger.warning("Monitor: trade %d stuck >%dh — marking error", trade_id, _STUCK_HOURS)
                        telegram_service.send_yield_error(
                            context=f"Trade {trade_id} stuck >{_STUCK_HOURS}h",
                            error=f"Market: {trade['title'][:80]} | Outcome: {trade['outcome']}"
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
    """Fire a Telegram alert if balance is approaching the floor threshold."""
    from service.risk_guard_service import get_balance_floor
    balance_floor = get_balance_floor()
    if current_balance < balance_floor * _BALANCE_WARNING_MULTIPLIER:
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
