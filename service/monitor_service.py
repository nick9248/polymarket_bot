"""
monitor_service.py
Polls the Polymarket positions API to advance yield_trades lifecycle statuses
and fires Telegram alerts on state transitions.

Called once per yield farming cycle (after execution). Also handles the daily
summary at 23:00 UTC.

Lifecycle: submitted → filled → won | lost → settled_at set after 30min
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from service import db_service, telegram_service
from utility.constants import REQUEST_TIMEOUT_SECONDS
from utility.endpoints import POSITIONS, CLOSED_POSITIONS

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


def _fetch_closed_positions() -> list[dict]:
    """Fetch recent closed positions for our wallet (last 500)."""
    try:
        resp = requests.get(CLOSED_POSITIONS, params={"user": _OUR_WALLET, "limit": 500}, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Monitor: could not fetch closed positions: %s", e)
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

    # Build lookup: (conditionId, outcome) → position dict for open positions
    open_pos_lookup: dict[tuple[str, str], dict] = {}
    for pos in open_positions:
        key = (pos.get("conditionId", ""), pos.get("outcome", ""))
        open_pos_lookup[key] = pos

    # We only fetch closed positions once if any trade needs resolution check
    closed_positions: list[dict] = []
    closed_fetched = False

    for trade in open_trades:
        trade_id = trade["id"]
        condition_id = trade["condition_id"]
        outcome = trade["outcome"]
        key = (condition_id, outcome)

        # ── submitted → filled ────────────────────────────────────────────────
        if trade["status"] == "submitted" and key in open_pos_lookup:
            pos = open_pos_lookup[key]
            fill_price = float(pos.get("curPrice", trade["signal_price"] or 0))
            db_service.update_yield_trade(trade_id, status="filled", fill_price=fill_price)
            logger.info("Monitor: trade %d status → filled @ $%.4f (%s)", trade_id, fill_price, trade["title"][:50])
            continue

        # ── filled/submitted → won or lost ────────────────────────────────────
        # Only check if not in open positions (resolved) and past submitted time
        submitted_at = trade.get("submitted_at")
        if key not in open_pos_lookup:
            if not closed_fetched:
                closed_positions = _fetch_closed_positions()
                closed_fetched = True

            closed_lookup: dict[tuple[str, str], dict] = {
                (p.get("conditionId", ""), p.get("outcome", "")): p
                for p in closed_positions
            }

            if key in closed_lookup:
                closed = closed_lookup[key]
                realized_pnl = float(closed.get("realizedPnl", 0))
                cost = float(trade["cost_usd"] or 0)
                if realized_pnl > 0:
                    db_service.update_yield_trade(
                        trade_id,
                        status="won",
                        pnl_usd=realized_pnl,
                        resolved_at=now_utc,
                    )
                    logger.info("Monitor: trade %d WON — pnl=$%.4f (%s)", trade_id, realized_pnl, trade["title"][:50])
                    summary = db_service.get_yield_pnl_summary()
                    telegram_service.send_yield_trade_won(
                        title=trade["title"],
                        outcome=outcome,
                        pnl_usd=realized_pnl,
                        session_net_pnl=summary["net_pnl"],
                        win_rate=summary["win_rate"],
                    )
                else:
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
                        outcome=outcome,
                        loss_usd=cost,
                        session_net_pnl=summary["net_pnl"],
                        win_rate=summary["win_rate"],
                    )

            # Flag trades stuck > 24h with no resolution
            elif submitted_at:
                try:
                    submitted_dt = datetime.fromisoformat(str(submitted_at)).replace(tzinfo=timezone.utc) if not hasattr(submitted_at, 'tzinfo') else submitted_at
                    if (now_utc - submitted_dt).total_seconds() > _STUCK_HOURS * 3600:
                        db_service.update_yield_trade(trade_id, status="error")
                        logger.warning("Monitor: trade %d stuck >%dh — marking error", trade_id, _STUCK_HOURS)
                        telegram_service.send_yield_error(
                            context=f"Trade {trade_id} stuck >{_STUCK_HOURS}h",
                            error=f"Market: {trade['title'][:80]} | Outcome: {outcome}"
                        )
                except Exception as e:
                    logger.warning("Monitor: error parsing submitted_at for trade %d: %s", trade_id, e)

        # ── resolved → settled (30min delay) ─────────────────────────────────
        if trade.get("resolved_at") and not trade.get("settled_at"):
            try:
                resolved_dt = datetime.fromisoformat(str(trade["resolved_at"])).replace(tzinfo=timezone.utc) if not hasattr(trade["resolved_at"], 'tzinfo') else trade["resolved_at"]
                if (now_utc - resolved_dt).total_seconds() > _SETTLE_DELAY_MINUTES * 60:
                    db_service.update_yield_trade(trade_id, settled_at=now_utc)
                    logger.info("Monitor: trade %d marked settled", trade_id)
            except Exception as e:
                logger.warning("Monitor: error parsing resolved_at for trade %d: %s", trade_id, e)


def check_balance_warning(current_balance: float) -> None:
    """Fire a Telegram alert if balance is approaching the floor threshold."""
    from service.risk_guard_service import _BALANCE_FLOOR
    if current_balance < _BALANCE_FLOOR * _BALANCE_WARNING_MULTIPLIER:
        logger.warning("Monitor: balance $%.2f is below 2× floor ($%.2f)", current_balance, _BALANCE_FLOOR)
        telegram_service.send_balance_warning(current_balance=current_balance, floor=_BALANCE_FLOOR)


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
