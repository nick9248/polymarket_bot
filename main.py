"""
main.py
Entry point for the polymarket_robot application.
Runs as a continuous daemon in yield farming mode.

Usage:
    python main.py --yield-farming [OPTIONS]

Options:
    --threshold FLOAT     Minimum outcome price to act on (default: 0.95)
    --window INT          Look-ahead window in minutes for closing markets (default: 10)
    --dry-run             Scan and log but submit no real orders

Examples:
    python main.py --yield-farming
    python main.py --yield-farming --threshold 0.97 --window 5
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

from utility.logger import init_logging
from service import db_service, telegram_service
from service.copy_trade_service import _get_usdc_balance
from service.yield_farming_service import run_yield_farming_cycle
from service.risk_guard_service import check_risk
from service.monitor_service import poll_lifecycle, check_balance_warning, send_daily_summary_if_due

init_logging(level="INFO")
logger = logging.getLogger(__name__)

# Seconds between each polling cycle
POLL_INTERVAL_SECONDS = 5

# Risk guard alert deduplication — True while trading is halted so we only
# fire the Telegram alert on the transition into the blocked state, not every cycle.
_risk_guard_currently_blocked: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Polymarket yield farming daemon",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--yield-farming",
        action="store_true",
        default=False,
        help="Run in yield farming mode: scan near-expiry markets and buy high-confidence outcomes",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.95,
        help="Minimum outcome price to act on in yield farming mode (default: 0.95)",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=10,
        help="Look-ahead window in minutes for closing markets in yield farming mode (default: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Scan and log opportunities but submit no real orders (use with --yield-farming)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.yield_farming:
        logger.info(
            "polymarket_robot starting in YIELD FARMING mode (threshold=%.2f, window=%dmin, polling every %ds)...",
            args.threshold, args.window, POLL_INTERVAL_SECONDS,
        )
    else:
        logger.info("polymarket_robot starting (daemon mode, polling every %ds)...", POLL_INTERVAL_SECONDS)

    # Initialise DB once at startup — safe to call repeatedly
    try:
        db_service.initialise_database()
    except Exception as e:
        logger.critical("Database initialisation failed: %s", e)
        sys.exit(1)

    # Fetch USDC balance once at session start — used for drawdown calculation.
    # If the DB already has a persisted session_start (e.g. after a hot reset or restart),
    # we restore it so the drawdown guard keeps continuity across restarts.
    session_start_balance = 0.0
    session_start_time = datetime.now(timezone.utc)
    if args.yield_farming:
        pk = os.getenv("poly_private_key", "").strip(" '\"")
        if pk:
            live_balance = _get_usdc_balance(pk)
            # Restore session from DB if it was persisted within the last 12 hours
            # (avoids inheriting stale sessions after a long outage)
            try:
                heartbeat = db_service.get_bot_heartbeat()
                if heartbeat and heartbeat.get("session_start_time"):
                    stored_time_str = heartbeat["session_start_time"]
                    stored_time = datetime.fromisoformat(stored_time_str)
                    if stored_time.tzinfo is None:
                        stored_time = stored_time.replace(tzinfo=timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - stored_time).total_seconds() / 3600
                    if age_hours < 12 and heartbeat.get("session_start_balance"):
                        session_start_balance = float(heartbeat["session_start_balance"])
                        session_start_time = stored_time
                        logger.info(
                            "Session restored from DB: start=$%.2f at %s (%.1fh ago)",
                            session_start_balance, stored_time.strftime("%H:%M UTC"), age_hours
                        )
                    else:
                        session_start_balance = live_balance
                        logger.info("Session start USDC balance: $%.2f (fresh session)", session_start_balance)
                else:
                    session_start_balance = live_balance
                    logger.info("Session start USDC balance: $%.2f", session_start_balance)
            except Exception as e:
                logger.warning("Could not restore session from DB, using live balance: %s", e)
                session_start_balance = live_balance
                logger.info("Session start USDC balance: $%.2f", session_start_balance)

    # Runtime stats — updated each cycle and reported via /health
    started_at = datetime.now(timezone.utc)
    stats = {
        "cycles_completed": 0,
        "last_cycle_at": "Not run yet",
        "last_new_trade_at": None,
        "alerts_total": 0,
        "db_ok": True,
        "geo_ok": True,
    }

    while True:
        global _risk_guard_currently_blocked
        # ── Handle incoming Telegram commands ────────────────────────────────
        commands = telegram_service.get_pending_commands()
        for cmd in commands:
            if cmd == "/health":
                stats["uptime_seconds"] = (datetime.now(timezone.utc) - started_at).total_seconds()
                telegram_service.send_health_report(stats)

            elif cmd == "/reset_risk" and args.yield_farming:
                try:
                    pk = os.getenv("poly_private_key", "").strip(" '\"")
                    new_balance = _get_usdc_balance(pk) if pk and not args.dry_run else session_start_balance
                    now_utc = datetime.now(timezone.utc)
                    db_service.reset_session_start(new_balance=new_balance, new_time=now_utc)
                    session_start_balance = new_balance
                    session_start_time = now_utc
                    _risk_guard_currently_blocked = False
                    logger.info("/reset_risk applied: new session start=$%.2f at %s", new_balance, now_utc.strftime("%H:%M UTC"))
                    from service.telegram_service import send_risk_guard_reset
                    send_risk_guard_reset(new_balance=new_balance, triggered_by="Telegram /reset_risk")
                except Exception as e:
                    logger.error("/reset_risk failed: %s", e)
                    telegram_service.send_message(f"❌ /reset_risk failed: {e}")

            elif cmd == "/balance" and args.yield_farming:
                try:
                    pk = os.getenv("poly_private_key", "").strip(" '\"")
                    bal = _get_usdc_balance(pk) if pk and not args.dry_run else session_start_balance
                    drawdown_pct = max(0.0, (session_start_balance - bal) / session_start_balance * 100) if session_start_balance > 0 else 0.0
                    from service.risk_guard_service import get_balance_floor
                    telegram_service.send_balance_status(
                        current_balance=bal,
                        session_start=session_start_balance,
                        drawdown_pct=drawdown_pct,
                        floor=get_balance_floor(),
                    )
                    logger.info("/balance command handled: $%.2f (%.1f%% drawdown)", bal, drawdown_pct)
                except Exception as e:
                    logger.error("/balance failed: %s", e)
                    telegram_service.send_message(f"❌ /balance failed: {e}")

            elif cmd == "/summary" and args.yield_farming:
                try:
                    summary = db_service.get_yield_pnl_summary()
                    telegram_service.send_session_summary(
                        total_trades=summary["total_trades"],
                        won=summary["won"],
                        lost=summary["lost"],
                        win_rate=summary["win_rate"],
                        net_pnl=summary["net_pnl"],
                    )
                    logger.info("/summary command handled.")
                except Exception as e:
                    logger.error("/summary failed: %s", e)
                    telegram_service.send_message(f"❌ /summary failed: {e}")

            elif cmd == "/trades" and args.yield_farming:
                try:
                    trades = db_service.get_yield_trades_page(limit=5)
                    telegram_service.send_recent_trades(trades)
                    logger.info("/trades command handled.")
                except Exception as e:
                    logger.error("/trades failed: %s", e)
                    telegram_service.send_message(f"❌ /trades failed: {e}")

        # ── Run polling cycle ─────────────────────────────────────────────────
        try:
            if args.yield_farming:
                # 1. Update bot heartbeat (liveness signal for dashboard)
                try:
                    pk = os.getenv("poly_private_key", "").strip(" '\"")
                    current_balance = _get_usdc_balance(pk) if pk and not args.dry_run else session_start_balance
                    db_service.update_bot_heartbeat(
                        mode="yield-farming" + (" [dry-run]" if args.dry_run else ""),
                        session_start_balance=session_start_balance,
                        current_balance=current_balance,
                        session_start_time=session_start_time,
                    )
                except Exception as e:
                    logger.warning("Could not update bot heartbeat: %s", e)
                    current_balance = session_start_balance

                # 1b. Midnight auto-reset — new calendar day = new session baseline.
                # This prevents a single bad day from permanently blocking the bot.
                # All historical trade data stays in DB; only the drawdown reference resets.
                if not args.dry_run and session_start_time.date() < datetime.now(timezone.utc).date():
                    try:
                        pk = os.getenv("poly_private_key", "").strip(" '\"")
                        new_balance = _get_usdc_balance(pk) if pk else current_balance
                        now_utc = datetime.now(timezone.utc)
                        db_service.reset_session_start(new_balance=new_balance, new_time=now_utc)
                        session_start_balance = new_balance
                        session_start_time = now_utc
                        _risk_guard_currently_blocked = False
                        logger.info("Midnight auto-reset: new session start=$%.2f at %s", new_balance, now_utc.strftime("%Y-%m-%d %H:%M UTC"))
                        from service.telegram_service import send_risk_guard_reset
                        send_risk_guard_reset(new_balance=new_balance, triggered_by="Midnight auto-reset (new day)")
                    except Exception as e:
                        logger.error("Midnight auto-reset failed: %s", e)

                # 1c. Dashboard-triggered reset — check for reset_requested flag in DB.
                if not args.dry_run:
                    try:
                        heartbeat = db_service.get_bot_heartbeat()
                        if heartbeat and heartbeat.get("reset_requested"):
                            pk = os.getenv("poly_private_key", "").strip(" '\"")
                            new_balance = _get_usdc_balance(pk) if pk else current_balance
                            now_utc = datetime.now(timezone.utc)
                            db_service.reset_session_start(new_balance=new_balance, new_time=now_utc)
                            session_start_balance = new_balance
                            session_start_time = now_utc
                            _risk_guard_currently_blocked = False
                            logger.info("Dashboard reset applied: new session start=$%.2f", new_balance)
                            from service.telegram_service import send_risk_guard_reset
                            send_risk_guard_reset(new_balance=new_balance, triggered_by="Dashboard reset button")
                    except Exception as e:
                        logger.warning("Dashboard reset check failed: %s", e)

                # 2. Advance lifecycle of previous trades
                if not args.dry_run:
                    try:
                        poll_lifecycle()
                    except Exception as e:
                        logger.error("Monitor lifecycle poll failed: %s", e)

                # 3. Risk guard — check all three circuit breakers
                if not args.dry_run:
                    risk = check_risk(current_balance=current_balance, session_start_balance=session_start_balance, session_start_time=session_start_time)
                    if not risk.allowed:
                        if not _risk_guard_currently_blocked:
                            # First cycle in blocked state — fire alert once
                            from service.telegram_service import send_risk_guard_blocked
                            send_risk_guard_blocked(risk.reason)
                            _risk_guard_currently_blocked = True
                        logger.warning("Risk guard halted trading this cycle: %s", risk.reason)
                        stats["cycles_completed"] += 1
                        stats["last_cycle_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                        stats["db_ok"] = True
                        logger.info("Sleeping %ds until next cycle...", POLL_INTERVAL_SECONDS)
                        time.sleep(POLL_INTERVAL_SECONDS)
                        continue
                    else:
                        _risk_guard_currently_blocked = False  # reset when unblocked

                # 4. Balance warning check
                if not args.dry_run:
                    try:
                        check_balance_warning(current_balance)
                    except Exception as e:
                        logger.warning("Balance warning check failed: %s", e)

                # 5. Execute yield farming cycle
                submitted = run_yield_farming_cycle(
                    threshold=args.threshold,
                    window_minutes=args.window,
                    dry_run=args.dry_run,
                    session_balance_start=session_start_balance,
                )
                stats["cycles_completed"] += 1
                stats["last_cycle_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                stats["alerts_total"] += submitted
                stats["db_ok"] = True

                # 6. Daily summary
                if not args.dry_run:
                    try:
                        send_daily_summary_if_due(current_balance)
                    except Exception as e:
                        logger.warning("Daily summary failed: %s", e)
            else:
                logger.error("No mode selected. Run with --yield-farming to start yield farming mode.")
                sys.exit(1)
        except Exception as e:
            logger.error("Unhandled error in polling cycle: %s", e)
            stats["db_ok"] = False

        logger.info("Sleeping %ds until next cycle...", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
