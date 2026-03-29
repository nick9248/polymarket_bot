# Telegram Bot — Commands & Alerts

The bot communicates via a Telegram bot configured through `telegram_bot_key` and `telegram_chat_id` in `.env`. It both pushes alerts automatically and accepts two interactive commands.

---

## Interactive Commands

Commands are polled each cycle (~5 s). Send them to the configured chat to trigger an immediate response.

### `/health`

Returns a full status report for the current bot session.

**What it shows:**

| Field | Description |
|-------|-------------|
| **Status** | Always "Running" if the bot is up to respond |
| **Uptime** | Time elapsed since the process started (`Xh Ym Zs`) |
| **Cycles completed** | Number of polling iterations completed |
| **Last cycle** | UTC timestamp of the most recent cycle |
| **Tracking** | Display names of wallets being monitored (copy-trade mode) |
| **Last trade copied** | UTC timestamp of the most recent copied trade, or "No trades copied yet" |
| **Total alerts sent** | Cumulative count of Telegram trade alerts in this session |
| **Database** | ✅ OK / ❌ ERROR — whether the last DB operation succeeded |
| **Geo check (Spain)** | ✅ OK / ❌ FAILED — whether the IP check passed (only relevant when `CHECK_GEO_IP=True`) |

**Example response:**
```
🤖 Polymarket Bot — Health Report

🟢 Status: Running
⏱ Uptime: 2h 14m 38s
🔄 Cycles completed: 1612
🕒 Last cycle: 2026-03-29 01:42:11 UTC

👤 Tracking: stingo43
📋 Last trade copied: 2026-03-28 23:17:03 UTC
📣 Total alerts sent: 4

✅ Database: OK
✅ Geo check (Spain): OK
```

**How it works internally:**

`main.py` maintains a `stats` dict that is updated every cycle. When `/health` arrives, it adds `uptime_seconds` (calculated from `started_at`) and calls `telegram_service.send_health_report(stats)`.

---

### `/test`

Triggers a live copy-trade execution test — places a real $1.50 CLOB order on the first valid open market found.

**Purpose:** Verify that the CLOB authentication, order sizing, and network path are working without waiting for a real copy-trade event.

**What it does:**

1. Searches for open Polymarket markets (same logic as the main copy-trade loop)
2. Finds the first eligible market (open, valid price range, sufficient balance)
3. Submits a real limit order at the current best ask price
4. Reports the result back to Telegram

**Possible outcomes:**

| Result | Meaning |
|--------|---------|
| ✅ Order submitted successfully | CLOB accepted the order; includes the `order_id` |
| ❌ No target wallets configured | Running in leaderboard mode without `--wallets` |
| ❌ No suitable market found | All candidates were closed, outside price range, or had no liquidity |
| ❌ Order failed | CLOB rejected the order — see the `detail` field for the API error message |
| ❌ Unexpected error | Python exception — see detail for stack info |

**Example success response:**
```
✅ Copy-Trade Test Result

Status: Order submitted successfully!
Detail: BTC up or down 5% | YES @ $0.9720 | order_id=0x1a2b3c…
```

**Example failure response:**
```
❌ Copy-Trade Test Result

Status: Order failed.
Detail: CLOB rejected: not enough funds
```

**Important:** `/test` places a real order. It is not a dry run. The order will be visible on-chain and may execute if the price is hit.

---

## Automatic Alerts

These are sent proactively by the bot — no command needed.

### Trade Alerts (copy-trade mode)

Sent whenever a new trade is detected from a tracked wallet.

**Format:**
```
📋 New Trade — stingo43
BTC up or down 5% (end 2026-03-29 18:00 ET)
Side: BUY | Outcome: YES
Price: $0.9720 | Shares: 5 | Cost: ~$4.86
tx: 0x1a2b3c…
```

### Risk Guard Blocked

Sent once when any circuit breaker trips (not repeated every cycle to avoid spam).

**Example:**
```
🛑 Risk guard blocked trading: Drawdown limit hit: 10.2% > 10% …
```

### Yield Trade Executed

Sent after a successful yield farming order placement.

**Example:**
```
🌾 Yield trade placed
Market: BTC up or down 5% | YES
Signal price: $0.9750 | Fill price: $0.9710
Shares: 6 | Cost: $5.83
```

---

## Configuration

| `.env` key | Purpose |
|------------|---------|
| `telegram_bot_key` | Bot token from @BotFather |
| `telegram_chat_id` | Chat or group ID to send messages to |

The bot will silently skip all Telegram operations if either variable is missing or empty (`is_configured()` returns False). This allows running locally without Telegram configured.

To find your `chat_id`: forward any message from the target chat to @userinfobot, or check the URL in Telegram Web.
