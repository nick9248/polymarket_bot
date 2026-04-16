"""
telegram_service.py
Telegram notification service.
Sends trade alerts to a configured chat via the polym_check_bot.

All messages go through send_message(). The alert format is
specifically designed for trade notifications.
"""

import logging
import os
import requests
from dotenv import load_dotenv

from utility.constants import REQUEST_TIMEOUT_SECONDS
from core.models.trades import TradeEntry

# Load .env from project root
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=_env_path)

logger = logging.getLogger(__name__)

_BOT_TOKEN = os.environ.get("telegram_bot_key", "")
_CHAT_ID = os.environ.get("telegram_chat_id", "")
_BASE_URL = f"https://api.telegram.org/bot{_BOT_TOKEN}"

# Tracks the highest processed update_id so we never handle the same message twice
_last_update_id: int = 0

_REPLY_KEYBOARD = {
    "keyboard": [
        [{"text": "\U0001f3e5 Health"}, {"text": "\U0001f4b0 Balance"}, {"text": "\U0001f4ca Summary"}],
        [{"text": "\U0001f4cb Trades"}, {"text": "\u26a0\ufe0f Reset Risk"}, {"text": "\U0001f9ea Test"}],
    ],
    "resize_keyboard": True,
    "persistent": True,
    "is_persistent": True,
}

_KEYBOARD_TEXT_MAP: dict[str, str] = {
    "\U0001f3e5 health": "/health",
    "\U0001f4b0 balance": "/balance",
    "\U0001f4ca summary": "/summary",
    "\U0001f4cb trades": "/trades",
    "\u26a0\ufe0f reset risk": "/reset_risk",
    "\U0001f9ea test": "/test",
}


def is_configured() -> bool:
    """Return True if both bot token and chat_id are set in .env."""
    return bool(_BOT_TOKEN and _CHAT_ID)


def send_message(text: str, reply_markup: dict | None = None) -> bool:
    """
    Send a plain or HTML message to the configured chat.

    Args:
        text: Message text. Supports Telegram HTML parse mode.
        reply_markup: Optional Telegram reply markup dict (e.g. a keyboard).

    Returns:
        True if message sent successfully, False otherwise.
    """
    if not is_configured():
        logger.warning("Telegram not configured — skipping message (set telegram_chat_id in .env).")
        return False

    try:
        payload = {
            "chat_id": _CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        response = requests.post(
            f"{_BASE_URL}/sendMessage",
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        logger.info("Telegram message sent successfully (%d chars).", len(text))
        return True
    except Exception as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


def send_trade_alert(trade: TradeEntry, trader_name: str) -> bool:
    """
    Send a formatted trade alert for a single trade.

    Args:
        trade: The new trade to report.
        trader_name: Display name of the trader.

    Returns:
        True if message sent successfully, False otherwise.
    """
    side_emoji = "🟢" if trade.side == "BUY" else "🔴"
    usdc = trade.size * trade.price

    token_id = trade.asset
    token_display = f"<code>{token_id[:8]}...{token_id[-8:]}</code>" if token_id else "N/A"

    text = (
        f"{side_emoji} <b>NEW TRADE DETECTED</b>\n"
        f"\n"
        f"👤 <b>Trader:</b> {trader_name}\n"
        f"📋 <b>Market:</b> {trade.title}\n"
        f"🎯 <b>Outcome:</b> {trade.outcome}\n"
        f"🔑 <b>Token ID:</b> {token_display}\n"
        f"📊 <b>Side:</b> {trade.side}\n"
        f"💵 <b>Price:</b> ${trade.price:.3f} ({trade.price * 100:.1f}% implied prob)\n"
        f"📦 <b>Size:</b> {trade.size:,.0f} shares (~${usdc:,.0f} USDC)\n"
        f"🕒 <b>Time:</b> {trade.datetime_utc.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"🔗 <b>Wallet:</b> <code>{trade.proxy_wallet[:12]}...</code>"
    )

    logger.info(
        "Sending Telegram alert — trader=%s side=%s market=%.40s",
        trader_name, trade.side, trade.title,
    )
    return send_message(text)


def get_pending_commands() -> list[str]:
    """
    Poll the Telegram Bot API for new incoming messages.
    Returns a list of command strings (e.g. ['/health']) sent since the last check.
    Advances the internal offset so each update is processed exactly once.

    Returns:
        List of command strings found in new messages (lowercased, stripped).
    """
    global _last_update_id

    if not is_configured():
        return []

    try:
        response = requests.get(
            f"{_BASE_URL}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 0},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        updates = response.json().get("result", [])
    except requests.RequestException as e:
        logger.warning("Could not poll Telegram updates: %s", e)
        return []

    commands = []
    for update in updates:
        _last_update_id = max(_last_update_id, update["update_id"])
        text = (
            update.get("message", {})
            .get("text", "")
            .strip()
            .lower()
        )
        if text.lower() in _KEYBOARD_TEXT_MAP:
            mapped = _KEYBOARD_TEXT_MAP[text.lower()]
            commands.append(mapped)
            logger.info("Received Telegram keyboard button: %s → %s", text, mapped)
        elif text.startswith("/"):
            # Strip any @BotName suffix (e.g. /health@polym_check_bot -> /health)
            commands.append(text.split("@")[0])
            logger.info("Received Telegram command: %s", text.split("@")[0])

    return commands


def send_health_report(stats: dict) -> bool:
    """
    Send a formatted health status report to the configured chat.

    Args:
        stats: Dict with keys:
            uptime_seconds (float)
            cycles_completed (int)
            last_cycle_at (str)          — human-readable UTC timestamp
            targets (list[str])          — wallet display names being tracked
            last_new_trade_at (str|None) — UTC timestamp or None
            alerts_total (int)
            db_ok (bool)
            geo_ok (bool)

    Returns:
        True if sent successfully.
    """
    uptime_s = int(stats.get("uptime_seconds", 0))
    hours, remainder = divmod(uptime_s, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    targets = stats.get("targets", [])
    targets_str = ", ".join(targets) if targets else "none"

    last_trade = stats.get("last_new_trade_at") or "No trades copied yet"
    db_icon = "✅" if stats.get("db_ok") else "❌"
    geo_icon = "✅" if stats.get("geo_ok") else "❌"

    text = (
        f"🤖 <b>Polymarket Bot — Health Report</b>\n"
        f"\n"
        f"🟢 <b>Status:</b> Running\n"
        f"⏱ <b>Uptime:</b> {uptime_str}\n"
        f"🔄 <b>Cycles completed:</b> {stats.get('cycles_completed', 0)}\n"
        f"🕒 <b>Last cycle:</b> {stats.get('last_cycle_at', 'N/A')}\n"
        f"\n"
        f"👤 <b>Tracking:</b> {targets_str}\n"
        f"📋 <b>Last trade copied:</b> {last_trade}\n"
        f"📣 <b>Total alerts sent:</b> {stats.get('alerts_total', 0)}\n"
        f"\n"
        f"{db_icon} <b>Database:</b> {'OK' if stats.get('db_ok') else 'ERROR'}\n"
        f"{geo_icon} <b>Geo check (Spain):</b> {'OK' if stats.get('geo_ok') else 'FAILED'}"
    )

    logger.info("Sending health report to Telegram.")
    return send_message(text, reply_markup=_REPLY_KEYBOARD)


def send_test_result(success: bool, detail: str) -> bool:
    """Send the result of a /test command execution."""
    icon = "✅" if success else "❌"
    status = "Order submitted successfully!" if success else "Order failed."
    text = (
        f"{icon} <b>Copy-Trade Test Result</b>\n"
        f"\n"
        f"<b>Status:</b> {status}\n"
        f"<b>Detail:</b> {detail}"
    )
    return send_message(text, reply_markup=_REPLY_KEYBOARD)


def send_leaderboard_summary(entries, period: str, category: str) -> bool:
    """
    Send a leaderboard summary message (top 5).

    Args:
        entries: List of LeaderboardEntry objects.
        period: Time period string.
        category: Category string.

    Returns:
        True if message sent successfully, False otherwise.
    """
    lines = [f"🏆 <b>Polymarket Leaderboard — {period} / {category}</b>\n"]
    for e in entries[:5]:
        lines.append(
            f"  #{e.rank}  <b>{e.user_name}</b>  PnL: <code>${e.pnl:,.0f}</code>"
        )
    return send_message("\n".join(lines))


def send_yield_trade_submitted(title: str, outcome: str, fill_price: float, shares: int, cost_usd: float, balance_after: float) -> bool:
    """Alert when a yield trade order is accepted by the CLOB."""
    text = (
        f"🟢 <b>YIELD TRADE SUBMITTED</b>\n"
        f"\n"
        f"📋 <b>Market:</b> {title}\n"
        f"🎯 <b>Outcome:</b> {outcome}\n"
        f"💵 <b>Fill price:</b> ${fill_price:.4f} ({fill_price * 100:.1f}% prob)\n"
        f"📦 <b>Shares:</b> {shares}\n"
        f"💰 <b>Cost:</b> ${cost_usd:.2f}\n"
        f"🏦 <b>Balance after:</b> ${balance_after:.2f}"
    )
    return send_message(text)


def send_yield_trade_won(title: str, outcome: str, pnl_usd: float, session_net_pnl: float, win_rate: float) -> bool:
    """Alert when a yield trade resolves as a win."""
    text = (
        f"✅ <b>YIELD TRADE WON</b>\n"
        f"\n"
        f"📋 <b>Market:</b> {title}\n"
        f"🎯 <b>Outcome:</b> {outcome}\n"
        f"💸 <b>P&amp;L:</b> +${pnl_usd:.4f}\n"
        f"📊 <b>Session net P&amp;L:</b> ${session_net_pnl:+.2f}\n"
        f"🏆 <b>Win rate:</b> {win_rate * 100:.1f}%"
    )
    return send_message(text)


def send_yield_trade_lost(title: str, outcome: str, loss_usd: float, session_net_pnl: float, win_rate: float) -> bool:
    """Alert when a yield trade resolves as a loss."""
    text = (
        f"❌ <b>YIELD TRADE LOST</b>\n"
        f"\n"
        f"📋 <b>Market:</b> {title}\n"
        f"🎯 <b>Outcome:</b> {outcome}\n"
        f"💸 <b>Loss:</b> -${abs(loss_usd):.4f}\n"
        f"📊 <b>Session net P&amp;L:</b> ${session_net_pnl:+.2f}\n"
        f"📉 <b>Win rate:</b> {win_rate * 100:.1f}%"
    )
    return send_message(text)


def send_risk_guard_blocked(reason: str) -> bool:
    """Alert when a circuit breaker halts trading."""
    text = (
        f"🛑 <b>TRADING HALTED — RISK GUARD</b>\n"
        f"\n"
        f"⚠️ <b>Reason:</b> {reason}\n"
        f"\n"
        f"Bot will keep scanning but will not execute trades until conditions improve."
    )
    return send_message(text)


def send_risk_guard_reset(new_balance: float, triggered_by: str) -> bool:
    """Confirm that the risk guard has been manually reset."""
    text = (
        f"✅ <b>RISK GUARD RESET</b>\n"
        f"\n"
        f"🔄 <b>Triggered by:</b> {triggered_by}\n"
        f"🏦 <b>New session start:</b> ${new_balance:.2f}\n"
        f"\n"
        f"Trading resumed. Drawdown tracking restarts from this balance."
    )
    return send_message(text)


def send_balance_warning(current_balance: float, floor: float) -> bool:
    """Alert when balance drops below 2× the floor threshold."""
    text = (
        f"⚠️ <b>LOW BALANCE WARNING</b>\n"
        f"\n"
        f"🏦 <b>Current balance:</b> ${current_balance:.2f}\n"
        f"🛑 <b>Floor (halt threshold):</b> ${floor:.2f}\n"
        f"📉 Balance is below 2× floor — approaching trading halt."
    )
    return send_message(text)


def send_yield_daily_summary(
    total_trades: int,
    won: int,
    lost: int,
    win_rate: float,
    net_pnl: float,
    current_balance: float,
) -> bool:
    """Send the daily summary at 23:00 UTC."""
    icon = "📈" if net_pnl >= 0 else "📉"
    text = (
        f"{icon} <b>Yield Farming — Daily Summary</b>\n"
        f"\n"
        f"📊 <b>Trades today:</b> {total_trades} | Won: {won} | Lost: {lost}\n"
        f"🏆 <b>Win rate:</b> {win_rate * 100:.1f}%\n"
        f"💸 <b>Net P&amp;L:</b> ${net_pnl:+.2f}\n"
        f"🏦 <b>Current balance:</b> ${current_balance:.2f}"
    )
    return send_message(text)


def send_stop_loss_triggered(
    title: str,
    outcome: str,
    shares: int,
    exit_price: float,
    recovered_usd: float,
    cost_usd: float,
    pnl_usd: float,
    session_net_pnl: float,
) -> bool:
    """Alert when a stop-loss sell is executed to exit a losing position."""
    text = (
        f"🛑 <b>STOP-LOSS TRIGGERED</b>\n"
        f"\n"
        f"📋 <b>Market:</b> {title}\n"
        f"🎯 <b>Outcome:</b> {outcome}\n"
        f"📦 <b>Shares sold:</b> {shares}\n"
        f"💵 <b>Exit price:</b> ${exit_price:.4f}\n"
        f"💰 <b>Recovered:</b> ${recovered_usd:.2f} / ${cost_usd:.2f} invested\n"
        f"💸 <b>P&amp;L:</b> ${pnl_usd:+.2f} (vs -${cost_usd:.2f} full loss)\n"
        f"📊 <b>Session net P&amp;L:</b> ${session_net_pnl:+.2f}"
    )
    return send_message(text)


def send_yield_error(context: str, error: str) -> bool:
    """Alert on unexpected errors in the yield farming cycle."""
    text = (
        f"🔴 <b>YIELD FARMING ERROR</b>\n"
        f"\n"
        f"📍 <b>Context:</b> {context}\n"
        f"⚠️ <b>Error:</b> {error[:300]}"
    )
    return send_message(text)


def register_commands() -> bool:
    """
    Register bot command list with BotFather via setMyCommands.
    Makes commands appear in the / autocomplete menu. Idempotent.
    Called once at bot startup.
    """
    if not is_configured():
        return False
    commands = [
        {"command": "health",     "description": "Bot uptime, cycle count, DB and geo status"},
        {"command": "balance",    "description": "Current USDC balance and drawdown"},
        {"command": "summary",    "description": "Session P&amp;L, win rate, trade count"},
        {"command": "trades",     "description": "Last 5 resolved trades with P&amp;L"},
        {"command": "reset_risk", "description": "Reset risk guard and resume trading"},
        {"command": "test",       "description": "Run a live order test on the CLOB"},
    ]
    try:
        response = requests.post(
            f"{_BASE_URL}/setMyCommands",
            json={"commands": commands},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        logger.info("Telegram: bot commands registered (%d commands).", len(commands))
        return True
    except Exception as exc:
        logger.warning("Telegram: failed to register commands: %s", exc)
        return False


def send_keyboard() -> bool:
    """Send the persistent reply keyboard. Call once at startup."""
    return send_message("\U0001f916 Yield farming bot online.", reply_markup=_REPLY_KEYBOARD)


def send_balance_status(current_balance: float, session_start: float, drawdown_pct: float, floor: float) -> bool:
    """Send current balance and drawdown on /balance command."""
    icon = "\U0001f7e2" if current_balance >= floor * 2 else ("\U0001f7e1" if current_balance >= floor else "\U0001f534")
    text = (
        f"{icon} <b>Balance Status</b>\n"
        f"\n"
        f"\U0001f3e6 <b>Current balance:</b> ${current_balance:.2f}\n"
        f"\U0001f4ca <b>Session start:</b> ${session_start:.2f}\n"
        f"\U0001f4c9 <b>Drawdown:</b> {drawdown_pct:.1f}%\n"
        f"\U0001f6d1 <b>Floor:</b> ${floor:.2f}"
    )
    return send_message(text, reply_markup=_REPLY_KEYBOARD)


def send_session_summary(total_trades: int, won: int, lost: int, win_rate: float, net_pnl: float) -> bool:
    """Send session P&L summary on /summary command."""
    icon = "\U0001f4c8" if net_pnl >= 0 else "\U0001f4c9"
    text = (
        f"{icon} <b>Session Summary</b>\n"
        f"\n"
        f"\U0001f4ca <b>Trades:</b> {total_trades} | Won: {won} | Lost: {lost}\n"
        f"\U0001f3c6 <b>Win rate:</b> {win_rate * 100:.1f}%\n"
        f"\U0001f4b8 <b>Net P&amp;L:</b> ${net_pnl:+.2f}"
    )
    return send_message(text, reply_markup=_REPLY_KEYBOARD)


def send_recent_trades(trades: list) -> bool:
    """Send last N resolved trades on /trades command."""
    if not trades:
        return send_message("\U0001f4cb <b>No resolved trades yet.</b>", reply_markup=_REPLY_KEYBOARD)
    lines = ["\U0001f4cb <b>Recent Trades</b>\n"]
    for t in trades:
        status = t.get("status", "?")
        pnl = t.get("pnl_usd")
        icon = "\u2705" if status == "won" else ("\U0001f6d1" if status == "stopped" else ("\u274c" if status == "lost" else "\u23f8"))
        pnl_str = f"${float(pnl):+.2f}" if pnl is not None else "\u2014"
        title = (t.get("title") or "Unknown")[:45]
        lines.append(f"{icon} {title}\n    {pnl_str}")
    return send_message("\n".join(lines), reply_markup=_REPLY_KEYBOARD)
