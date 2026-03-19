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

    text = (
        f"{side_emoji} <b>NEW TRADE DETECTED</b>\n"
        f"\n"
        f"👤 <b>Trader:</b> {trader_name}\n"
        f"📋 <b>Market:</b> {trade.title}\n"
        f"🎯 <b>Outcome:</b> {trade.outcome}\n"
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
