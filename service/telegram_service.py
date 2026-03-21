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
from core.api import polymarket_client

# Load .env from project root
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path=_env_path)

logger = logging.getLogger(__name__)

_BOT_TOKEN = os.environ.get("telegram_bot_key", "")
_CHAT_ID = os.environ.get("telegram_chat_id", "")
_BASE_URL = f"https://api.telegram.org/bot{_BOT_TOKEN}"

# Tracks the highest processed update_id so we never handle the same message twice
_last_update_id: int = 0


def is_configured() -> bool:
    """Return True if both bot token and chat_id are set in .env."""
    return bool(_BOT_TOKEN and _CHAT_ID)


def send_message(text: str) -> bool:
    """
    Send a plain or Markdown message to the configured chat.

    Args:
        text: Message text. Supports Telegram MarkdownV2.

    Returns:
        True if message sent successfully, False otherwise.
    """
    if not is_configured():
        logger.warning("Telegram not configured — skipping message (set telegram_chat_id in .env).")
        return False

    try:
        response = requests.post(
            f"{_BASE_URL}/sendMessage",
            json={
                "chat_id": _CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        logger.debug("Telegram message sent successfully.")
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
    
    # Fetch token ID
    token_id = polymarket_client.get_market_token_id(trade.condition_id, trade.outcome_index)
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
        if text.startswith("/"):
            # Strip any @BotName suffix (e.g. /health@polym_check_bot -> /health)
            commands.append(text.split("@")[0])

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
    return send_message(text)


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
