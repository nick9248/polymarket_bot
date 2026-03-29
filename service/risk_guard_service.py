"""
risk_guard_service.py
Three independent circuit breakers that must all pass before a yield trade is executed.
Pure decision layer — no mutations.

Circuit breakers (evaluated in order, first failure wins):
  1. Balance floor — stop if USDC < YIELD_BALANCE_FLOOR
  2. Session drawdown — stop if confirmed losses since session start > YIELD_MAX_DRAWDOWN_PCT %
  3. Consecutive losses — stop if last N resolved trades are all 'lost'

Drawdown is measured from confirmed 'lost' DB rows only — NOT from the live CLOB balance.
This avoids false triggers caused by in-flight positions temporarily depleting the balance
(e.g. a $5 buy order drops the CLOB balance for 5 minutes until settlement credits it back).

Configure via .env:
  YIELD_BALANCE_FLOOR=5
  YIELD_MAX_CONSECUTIVE_LOSSES=3
  YIELD_MAX_DRAWDOWN_PCT=10
"""

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_BALANCE_FLOOR = float(os.getenv("YIELD_BALANCE_FLOOR", "5"))
_MAX_CONSECUTIVE_LOSSES = int(os.getenv("YIELD_MAX_CONSECUTIVE_LOSSES", "3"))
_MAX_DRAWDOWN_PCT = float(os.getenv("YIELD_MAX_DRAWDOWN_PCT", "10"))


@dataclass
class RiskStatus:
    """
    Result of a risk check.

    Attributes:
        allowed: True if trading is permitted, False if halted.
        reason: Human-readable explanation when allowed=False, None otherwise.
    """
    allowed: bool
    reason: str | None


def get_balance_floor() -> float:
    """Return the configured balance floor threshold."""
    return _BALANCE_FLOOR


def check_risk(
    current_balance: float,
    session_start_balance: float,
    session_start_time,
) -> RiskStatus:
    """
    Run all three circuit breakers. Returns on the first failure.

    Args:
        current_balance: Current USDC balance (for floor check only).
        session_start_balance: USDC balance when the bot session started.
        session_start_time: Datetime when the current session started.

    Returns:
        RiskStatus with allowed=True if all checks pass, or allowed=False
        with a human-readable reason string identifying the triggered breaker.
    """
    from service import db_service

    # 1. Balance floor
    if current_balance < _BALANCE_FLOOR:
        reason = f"Balance floor hit: ${current_balance:.2f} < ${_BALANCE_FLOOR:.2f} minimum"
        logger.warning("Risk guard BLOCKED: %s", reason)
        return RiskStatus(allowed=False, reason=reason)

    # 2. Session drawdown — measured from confirmed losses only.
    # Comparing CLOB balance would cause false triggers while positions are in-flight
    # (a $5 order depletes CLOB balance until settlement, even if the trade wins).
    if session_start_balance > 0:
        session_losses = db_service.get_session_realized_losses(session_start_time)
        drawdown_pct = session_losses / session_start_balance * 100
        if drawdown_pct > _MAX_DRAWDOWN_PCT:
            reason = (
                f"Drawdown limit hit: {drawdown_pct:.1f}% > {_MAX_DRAWDOWN_PCT:.0f}% "
                f"(${session_losses:.2f} confirmed losses on ${session_start_balance:.2f} start)"
            )
            logger.warning("Risk guard BLOCKED: %s", reason)
            return RiskStatus(allowed=False, reason=reason)

    # 3. Consecutive losses
    recent = db_service.get_recent_yield_trade_statuses(limit=_MAX_CONSECUTIVE_LOSSES)
    if len(recent) >= _MAX_CONSECUTIVE_LOSSES and all(s == "lost" for s in recent):
        reason = f"{_MAX_CONSECUTIVE_LOSSES} consecutive losses — manual review required"
        logger.warning("Risk guard BLOCKED: %s", reason)
        return RiskStatus(allowed=False, reason=reason)

    session_losses = db_service.get_session_realized_losses(session_start_time)
    drawdown_pct = session_losses / session_start_balance * 100 if session_start_balance > 0 else 0
    logger.debug(
        "Risk guard OK: balance=$%.2f, realized_drawdown=%.1f%% ($%.2f losses), recent=%s",
        current_balance, drawdown_pct, session_losses, recent,
    )
    return RiskStatus(allowed=True, reason=None)


def get_risk_dashboard_state(current_balance: float, session_start_balance: float, session_start_time) -> dict:
    """
    Return per-breaker state for the monitoring dashboard /api/risk endpoint.

    Returns:
        Dict with keys: balance_floor, drawdown, consecutive_losses.
        Each value is a dict with: current, threshold, triggered (bool), label (str).
    """
    from service import db_service
    recent = db_service.get_recent_yield_trade_statuses(limit=_MAX_CONSECUTIVE_LOSSES)
    consecutive_loss_count = 0
    for s in recent:
        if s == "lost":
            consecutive_loss_count += 1
        else:
            break
    session_losses = db_service.get_session_realized_losses(session_start_time)
    drawdown_pct = session_losses / session_start_balance * 100 if session_start_balance > 0 else 0.0

    return {
        "balance_floor": {
            "current": round(current_balance, 2),
            "threshold": _BALANCE_FLOOR,
            "triggered": current_balance < _BALANCE_FLOOR,
            "label": f"${current_balance:.2f} / ${_BALANCE_FLOOR:.2f} floor",
        },
        "drawdown": {
            "current": round(drawdown_pct, 2),
            "threshold": _MAX_DRAWDOWN_PCT,
            "triggered": drawdown_pct > _MAX_DRAWDOWN_PCT,
            "label": f"{drawdown_pct:.1f}% realized losses / {_MAX_DRAWDOWN_PCT:.0f}% max",
        },
        "consecutive_losses": {
            "current": consecutive_loss_count,
            "threshold": _MAX_CONSECUTIVE_LOSSES,
            "triggered": consecutive_loss_count >= _MAX_CONSECUTIVE_LOSSES,
            "label": f"{consecutive_loss_count} / {_MAX_CONSECUTIVE_LOSSES} max",
        },
    }
