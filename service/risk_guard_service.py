"""
risk_guard_service.py
Three independent circuit breakers that must all pass before a yield trade is executed.
Pure decision layer — no API calls, no mutations.

Circuit breakers (evaluated in order, first failure wins):
  1. Balance floor — stop if USDC < YIELD_BALANCE_FLOOR
  2. Session drawdown — stop if loss from session start > YIELD_MAX_DRAWDOWN_PCT %
  3. Consecutive losses — stop if last N resolved trades are all 'lost'

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


def check_risk(current_balance: float, session_start_balance: float) -> RiskStatus:
    """
    Run all three circuit breakers. Returns on the first failure.

    Args:
        current_balance: Current USDC balance.
        session_start_balance: USDC balance when the bot session started.

    Returns:
        RiskStatus with allowed=True if all checks pass, or allowed=False
        with a human-readable reason string identifying the triggered breaker.
    """
    # 1. Balance floor
    if current_balance < _BALANCE_FLOOR:
        reason = f"Balance floor hit: ${current_balance:.2f} < ${_BALANCE_FLOOR:.2f} minimum"
        logger.warning("Risk guard BLOCKED: %s", reason)
        return RiskStatus(allowed=False, reason=reason)

    # 2. Session drawdown
    if session_start_balance > 0:
        drawdown_pct = (session_start_balance - current_balance) / session_start_balance * 100
        if drawdown_pct > _MAX_DRAWDOWN_PCT:
            reason = (
                f"Drawdown limit hit: {drawdown_pct:.1f}% > {_MAX_DRAWDOWN_PCT:.0f}% "
                f"(${session_start_balance:.2f} → ${current_balance:.2f})"
            )
            logger.warning("Risk guard BLOCKED: %s", reason)
            return RiskStatus(allowed=False, reason=reason)

    # 3. Consecutive losses (DB read — read-only, no mutations)
    from service import db_service
    recent = db_service.get_recent_yield_trade_statuses(limit=_MAX_CONSECUTIVE_LOSSES)
    if len(recent) >= _MAX_CONSECUTIVE_LOSSES and all(s == "lost" for s in recent):
        reason = f"{_MAX_CONSECUTIVE_LOSSES} consecutive losses — manual review required"
        logger.warning("Risk guard BLOCKED: %s", reason)
        return RiskStatus(allowed=False, reason=reason)

    logger.debug(
        "Risk guard OK: balance=$%.2f, drawdown=%.1f%%, recent=%s",
        current_balance,
        (session_start_balance - current_balance) / session_start_balance * 100 if session_start_balance > 0 else 0,
        recent,
    )
    return RiskStatus(allowed=True, reason=None)


def get_risk_dashboard_state(current_balance: float, session_start_balance: float) -> dict:
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
    drawdown_pct = (
        (session_start_balance - current_balance) / session_start_balance * 100
        if session_start_balance > 0 else 0.0
    )

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
            "label": f"{drawdown_pct:.1f}% / {_MAX_DRAWDOWN_PCT:.0f}% max",
        },
        "consecutive_losses": {
            "current": consecutive_loss_count,
            "threshold": _MAX_CONSECUTIVE_LOSSES,
            "triggered": consecutive_loss_count >= _MAX_CONSECUTIVE_LOSSES,
            "label": f"{consecutive_loss_count} / {_MAX_CONSECUTIVE_LOSSES} max",
        },
    }
