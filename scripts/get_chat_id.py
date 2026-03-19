"""
get_chat_id.py
Helper script to find your Telegram chat_id.

STEPS:
1. Open Telegram and send any message to @polym_check_bot (e.g. /start)
2. Run: python scripts/get_chat_id.py
3. Copy the chat_id printed below
4. Add it to your .env:  telegram_chat_id = <your_id>
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

BOT_TOKEN = os.environ.get("telegram_bot_key", "")

if not BOT_TOKEN:
    print("ERROR: telegram_bot_key not found in .env")
    raise SystemExit(1)

print("Fetching recent updates from Telegram bot...")
response = requests.get(
    f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
    timeout=10,
)
data = response.json()

if not data.get("ok"):
    print(f"ERROR: Telegram API error — {data}")
    raise SystemExit(1)

updates = data.get("result", [])

if not updates:
    print()
    print("No messages found yet.")
    print("  → Open Telegram, find @polym_check_bot, and send /start")
    print("  → Then run this script again.")
    raise SystemExit(0)

print()
print("Found the following chats that messaged the bot:")
print("-" * 50)

seen = set()
for update in updates:
    message = update.get("message") or update.get("channel_post", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type", "unknown")
    name = chat.get("first_name") or chat.get("title") or "?"
    username = chat.get("username", "")

    if chat_id and chat_id not in seen:
        seen.add(chat_id)
        print(f"  Chat ID  : {chat_id}")
        print(f"  Name     : {name} {'(@' + username + ')' if username else ''}")
        print(f"  Type     : {chat_type}")
        print()

print("Add to your .env:")
print('  telegram_chat_id = "<your_chat_id>"')
