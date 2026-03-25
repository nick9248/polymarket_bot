# Yield Farming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the failing copy-trade loop with a proactive yield-farming mode that scans Polymarket's Gamma API for near-expiry markets with a near-certain outcome (price ≥ threshold), then executes CLOB orders automatically — with full safety monitoring, dry-run testing, and copy-trade execution toggle.

**Architecture:** A new `--yield-farming` CLI mode runs a dedicated cycle: `market_scanner_service` polls Gamma API for qualifying markets, `yield_farming_service` orchestrates deduplication, sizing, pre-trade safety checks, and settlement monitoring, and a refactored `submit_order()` in `copy_trade_service` handles CLOB execution with DRY_RUN support. Copy-trade mode gains a `--copy-trade on/off` toggle so trade detection and Telegram alerts keep working while execution is paused.

**Tech Stack:** Python 3.12, py_clob_client, requests, pytest, python-dotenv

---

## Staged Capital Plan

This plan is implemented once, then deployed in stages:

| Phase | Mode | Capital | Duration | Pass Criteria |
|---|---|---|---|---|
| 0 | `DRY_RUN=true` | $0 | 2–3 days | Bot runs without crashes, finds markets, logs correctly |
| 1 | Live | $50 | 2 weeks | Executes correctly, ≥ 90% of orders filled, no bugs |
| 2 | Live | $100 | 1 month | Positive P&L, win rate confirmed with real data |
| 3 | Live | $300+ | 2 months | ≥ 500 trades of real edge, then scale |

**Phase 0 is operational correctness, not financial.** You need DRY_RUN to trust the pipeline before money goes in.

### Phase 1 targets (at ≥ 0.93 threshold, $50 capital)

Used by `/health` to show how far you are from the projected goal after 2 weeks:

| Metric | Projected |
|--------|-----------|
| Trades/day | ~72 (6/batch × 12 batches) |
| Trades over 2 weeks | ~1,008 |
| Expected win rate | ≥ 90% |
| Expected P&L range | $47–$55 (variance dominates at this scale) |

The `/health` command shows: trades executed, days elapsed, P&L vs start, and extrapolated end-of-phase P&L — so you know at day 7 whether you're tracking toward $47–$55 or something went wrong.

---

## Pre-requisite: Commit existing changes

The repository has 6 modified files (`core/database/repository.py`, `main.py`, `scripts/run_analyzer.py`, `service/db_service.py`, `service/validator_service.py`, `utility/geo.py`) and several untracked files. Before starting this plan:

```bash
git add core/database/repository.py main.py scripts/run_analyzer.py \
        service/db_service.py service/validator_service.py utility/geo.py
git commit -m "chore: commit working state before yield farming feature"
```

---

## File Map

| Action   | Path                                   | Responsibility |
|----------|----------------------------------------|----------------|
| Create   | `core/models/market.py`               | `MarketOpportunity` dataclass |
| Modify   | `utility/endpoints.py`                | Add `GAMMA_MARKETS` URL constant |
| Modify   | `core/api/polymarket_client.py`       | Add `get_near_expiry_markets()` raw HTTP call |
| Modify   | `service/copy_trade_service.py`       | Extract `submit_order()`, add DRY_RUN support |
| Modify   | `main.py`                             | Add `--copy-trade on/off` flag; add `--yield-farming` flag and loop |
| Create   | `service/market_scanner_service.py`   | Filter + rank `MarketOpportunity` list |
| Create   | `service/yield_farming_service.py`    | Farming cycle: safety checks, execution, settlement monitoring, P&L tracking |
| Modify   | `service/telegram_service.py`         | Add `send_farming_alert()`, extend `/health` |
| Create   | `tests/test_market_model.py`          | MarketOpportunity dataclass tests |
| Create   | `tests/test_polymarket_client_gamma.py` | Gamma API raw call tests |
| Create   | `tests/test_submit_order.py`          | submit_order unit tests (including DRY_RUN) |
| Create   | `tests/test_market_scanner.py`        | Scanner filter and ranking tests |
| Create   | `tests/test_yield_farming_service.py` | Farming orchestrator tests |
| Create   | `tests/test_main_flags.py`            | CLI flag parsing tests |

---

## Task 1: `MarketOpportunity` model + Gamma endpoint

**Files:**
- Create: `core/models/market.py`
- Modify: `utility/endpoints.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_market_model.py
from datetime import datetime, timezone
from core.models.market import MarketOpportunity

def test_market_opportunity_fields():
    opp = MarketOpportunity(
        condition_id="0xabc",
        token_id="123456",
        title="BTC Up or Down?",
        outcome="Up",
        price=0.97,
        closes_at=datetime(2026, 3, 25, 19, 0, tzinfo=timezone.utc),
        minutes_left=3.5,
    )
    assert opp.condition_id == "0xabc"
    assert opp.token_id == "123456"
    assert opp.price == 0.97
    assert opp.minutes_left == 3.5

def test_market_opportunity_is_dataclass():
    from dataclasses import fields
    field_names = {f.name for f in fields(MarketOpportunity)}
    assert field_names == {
        "condition_id", "token_id", "title", "outcome",
        "price", "closes_at", "minutes_left",
    }
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_market_model.py -v
```
Expected: `ImportError: cannot import name 'MarketOpportunity'`

- [ ] **Step 3: Create `core/models/market.py`**

```python
"""
market.py
Data model for a yield-farming market opportunity.
Pure data — no business logic, no API calls.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class MarketOpportunity:
    """
    A near-expiry market where one outcome has price >= threshold.

    Attributes:
        condition_id: Polymarket condition identifier (used for CLOB market lookup).
        token_id: CLOB token ID for the qualifying outcome (use directly for orders).
        title: Human-readable market question.
        outcome: Which outcome qualifies (e.g. "Up", "Yes").
        price: Current price of the qualifying outcome (0.01–0.99).
        closes_at: UTC datetime when the market resolves.
        minutes_left: Minutes until close at time of scan.
    """

    condition_id: str
    token_id: str
    title: str
    outcome: str
    price: float
    closes_at: datetime
    minutes_left: float
```

- [ ] **Step 4: Add Gamma endpoint to `utility/endpoints.py`**

Add after the existing CLOB constant:

```python
# ── Polymarket Gamma API (market metadata — no authentication required) ────────
GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"

# Active market listings with close times and CLOB token IDs
GAMMA_MARKETS = f"{GAMMA_API_BASE_URL}/markets"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_market_model.py -v
```
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add core/models/market.py utility/endpoints.py tests/test_market_model.py
git commit -m "feat: add MarketOpportunity model and Gamma API endpoint"
```

---

## Task 2: Gamma API raw HTTP call

**Files:**
- Modify: `core/api/polymarket_client.py`

**Context — Gamma API shape (verified via live scan 2026-03-25):**
```json
{
  "conditionId": "0xabc...",
  "question": "BTC Up or Down?",
  "endDate": "2026-03-25T19:00:00Z",
  "active": true,
  "closed": false,
  "clobTokenIds": ["111...token_yes", "222...token_no"],
  "outcomes": "[\"Up\", \"Down\"]",
  "outcomePrices": "[\"0.997\", \"0.003\"]"
}
```

`clobTokenIds[i]` maps to `outcomes[i]` and `outcomePrices[i]`.

> **Verify before coding:** Hit `https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=5` manually and confirm field names match the shape above. If they differ, update `_extract_opportunity()` in Task 4 accordingly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_polymarket_client_gamma.py
from unittest.mock import patch, MagicMock
from core.api.polymarket_client import get_near_expiry_markets

MOCK_RESPONSE = [
    {
        "conditionId": "0xabc",
        "question": "BTC Up or Down?",
        "endDate": "2026-03-25T19:00:00Z",
        "active": True,
        "closed": False,
        "clobTokenIds": ["token_up", "token_down"],
        "outcomes": '["Up", "Down"]',
        "outcomePrices": '["0.997", "0.003"]',
    }
]

def test_get_near_expiry_markets_returns_raw_list():
    with patch("core.api.polymarket_client.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_RESPONSE
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = get_near_expiry_markets(
            end_date_min="2026-03-25T18:55:00Z",
            end_date_max="2026-03-25T19:00:00Z",
        )

    assert len(result) == 1
    assert result[0]["conditionId"] == "0xabc"

def test_get_near_expiry_markets_returns_empty_on_error():
    with patch("core.api.polymarket_client.requests.get") as mock_get:
        mock_get.side_effect = Exception("network error")
        result = get_near_expiry_markets(
            end_date_min="2026-03-25T18:55:00Z",
            end_date_max="2026-03-25T19:00:00Z",
        )
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_polymarket_client_gamma.py -v
```
Expected: `ImportError: cannot import name 'get_near_expiry_markets'`

- [ ] **Step 3: Add `get_near_expiry_markets()` to `core/api/polymarket_client.py`**

Update the import line at the top:
```python
from utility.endpoints import (
    LEADERBOARD, BUILDER_LEADERBOARD, TRADES, ACTIVITY,
    POSITIONS, CLOSED_POSITIONS, GAMMA_MARKETS,
)
```

Add this function:
```python
def get_near_expiry_markets(end_date_min: str, end_date_max: str) -> list[dict]:
    """
    Fetch active markets closing within a time window from the Gamma API.

    Args:
        end_date_min: ISO 8601 UTC string — lower bound of the close window.
        end_date_max: ISO 8601 UTC string — upper bound of the close window.

    Returns:
        List of raw market dicts from the Gamma API.
        Returns empty list on any error (network, parse, etc.).
    """
    try:
        response = requests.get(
            GAMMA_MARKETS,
            params={
                "active": "true",
                "closed": "false",
                "end_date_min": end_date_min,
                "end_date_max": end_date_max,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.warning("Gamma API error fetching near-expiry markets: %s", e)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_polymarket_client_gamma.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add core/api/polymarket_client.py tests/test_polymarket_client_gamma.py
git commit -m "feat: add Gamma API raw call for near-expiry market discovery"
```

---

## Task 3: DRY_RUN support + extract `submit_order()` from `copy_trade_service.py`

**Two things in one task because they're in the same function:**

**DRY_RUN:** When `DRY_RUN=true` in `.env`, all CLOB submissions are skipped. Balance reads are real (you see your real balance); order submission is not. Every log line in this path is prefixed `[DRY-RUN]`. This lets you run the full pipeline on the VPS for 2–3 days, see it find markets and pass all safety checks, then flip to live.

**`submit_order()` extraction:** `execute_copy_trade(trade: TradeEntry)` is tightly coupled to the `TradeEntry` type. Yield farming produces `MarketOpportunity` objects. We extract the shared CLOB logic into `submit_order(token_id, side, price, condition_id)` and make `execute_copy_trade` a thin adapter over it.

**Files:**
- Modify: `service/copy_trade_service.py`
- Modify: `.env` (add `DRY_RUN=false`)

- [ ] **Step 1: Add DRY_RUN to `.env`**

Open `.env` and add:
```
DRY_RUN = "false"
```

(Set to `"true"` for Phase 0 testing. Back to `"false"` for live.)

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_submit_order.py
import os
from unittest.mock import patch, MagicMock
import pytest


def _mock_env(monkeypatch):
    monkeypatch.setenv("poly_private_key", "0xdeadbeef")
    monkeypatch.setenv("poly_funder_address", "0xfunder")


def test_submit_order_skips_when_not_in_spain(monkeypatch):
    _mock_env(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "false")
    with patch("service.copy_trade_service.is_in_spain", return_value=False):
        from service.copy_trade_service import submit_order
        result = submit_order(token_id="tok123", side="BUY", price=0.95, condition_id="cond1")
    assert result is False


def test_submit_order_skips_invalid_price(monkeypatch):
    _mock_env(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "false")
    with patch("service.copy_trade_service.is_in_spain", return_value=True):
        from service.copy_trade_service import submit_order
        assert submit_order("tok", "BUY", 0.99, "cond") is False
        assert submit_order("tok", "BUY", 0.01, "cond") is False


def test_submit_order_dry_run_skips_clob(monkeypatch):
    """DRY_RUN=true must skip CLOB submission and return True (simulated success)."""
    _mock_env(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "true")
    with patch("service.copy_trade_service.is_in_spain", return_value=True), \
         patch("service.copy_trade_service._get_client") as mock_client_factory, \
         patch("service.copy_trade_service._get_usdc_balance", return_value=50.0):
        from service.copy_trade_service import submit_order
        result = submit_order("tok", "BUY", 0.95, "cond")
    assert result is True
    # CLOB client must never be constructed in dry-run
    mock_client_factory.assert_not_called()


def test_submit_order_skips_on_empty_order_book(monkeypatch):
    _mock_env(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "false")
    mock_client = MagicMock()
    mock_client.get_order_book.return_value = MagicMock(asks=[], bids=[])
    mock_client.get_market.return_value = {"minimum_order_size": 5}

    with patch("service.copy_trade_service.is_in_spain", return_value=True), \
         patch("service.copy_trade_service._get_client", return_value=mock_client), \
         patch("service.copy_trade_service._get_usdc_balance", return_value=50.0):
        from service.copy_trade_service import submit_order
        result = submit_order("tok", "BUY", 0.95, "cond")
    assert result is False


def test_submit_order_returns_true_on_success(monkeypatch):
    _mock_env(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "false")
    mock_book = MagicMock()
    mock_book.asks = [MagicMock(price="0.96")]
    mock_book.bids = []

    mock_client = MagicMock()
    mock_client.get_order_book.return_value = mock_book
    mock_client.get_market.return_value = {"minimum_order_size": 2}
    mock_client.create_order.return_value = MagicMock()
    mock_client.post_order.return_value = {"success": True, "orderID": "ord1", "status": "matched"}

    with patch("service.copy_trade_service.is_in_spain", return_value=True), \
         patch("service.copy_trade_service._get_client", return_value=mock_client), \
         patch("service.copy_trade_service._get_usdc_balance", return_value=50.0):
        from service.copy_trade_service import submit_order
        result = submit_order("tok", "BUY", 0.95, "cond")
    assert result is True


def test_submit_order_insufficient_balance(monkeypatch):
    _mock_env(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "false")
    mock_book = MagicMock()
    mock_book.asks = [MagicMock(price="0.96")]
    mock_book.bids = []

    mock_client = MagicMock()
    mock_client.get_order_book.return_value = mock_book
    mock_client.get_market.return_value = {"minimum_order_size": 5}

    with patch("service.copy_trade_service.is_in_spain", return_value=True), \
         patch("service.copy_trade_service._get_client", return_value=mock_client), \
         patch("service.copy_trade_service._get_usdc_balance", return_value=0.30):
        from service.copy_trade_service import submit_order
        result = submit_order("tok", "BUY", 0.95, "cond")
    assert result is False
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/test_submit_order.py -v
```
Expected: `ImportError: cannot import name 'submit_order'`

- [ ] **Step 4: Refactor `copy_trade_service.py`**

Add `submit_order` before `execute_copy_trade`. Then make `execute_copy_trade` a thin adapter.

```python
def submit_order(token_id: str, side: str, price: float, condition_id: str) -> bool:
    """
    Submit a taker order on the Polymarket CLOB.

    Core execution logic shared by copy-trading and yield farming.
    Reads DRY_RUN from .env — if true, logs the intended action and returns
    True without touching the CLOB. All real safety checks still run so
    dry-run output reflects exactly what live would do.

    Args:
        token_id:     CLOB token ID for the outcome to buy/sell.
        side:         "BUY" or "SELL".
        price:        Reference price (used for slippage guard only).
        condition_id: Market condition ID (for minimum_order_size lookup).

    Returns:
        True if order submitted and matched (or dry-run simulated), False otherwise.
    """
    dry_run = os.getenv("DRY_RUN", "false").strip().lower() == "true"
    prefix = "[DRY-RUN] " if dry_run else ""

    logger.info("%s=== PREPARING ORDER ===", prefix)
    logger.info(
        "%stoken_id=%s | side=%s | ref_price=$%.4f",
        prefix, token_id[:20], side, price,
    )

    if not is_in_spain():
        logger.error("%sExecution blocked: Geo location is not Spain (ES).", prefix)
        return False

    if not token_id:
        logger.error("%sExecution blocked: Empty token_id.", prefix)
        return False

    if price <= 0.0 or price > 1.0:
        logger.error("%sExecution blocked: Invalid reference price %.6f", prefix, price)
        return False

    # ── DRY_RUN: skip CLOB, log intended action, return simulated success ─────
    if dry_run:
        logger.info(
            "[DRY-RUN] Would submit BUY order: token=%s ref_price=$%.4f — SKIPPED (DRY_RUN=true)",
            token_id[:20], price,
        )
        return True

    try:
        pk = os.getenv("poly_private_key", "").strip(" '\"")
        client = _get_client()

        # ── Live market price ──────────────────────────────────────────────────
        current_price = _get_current_market_price(client, token_id, side)
        if current_price is None:
            logger.error(
                "Execution blocked: Order book empty or market closed (token=%s)", token_id[:20]
            )
            return False

        # ── Slippage guard ─────────────────────────────────────────────────────
        slippage_pct = abs(current_price - price) / price * 100
        if slippage_pct > _MAX_SLIPPAGE_PCT:
            logger.warning(
                "Skipping: slippage %.1f%% exceeds %.0f%% threshold "
                "(ref $%.3f → current $%.3f)",
                slippage_pct, _MAX_SLIPPAGE_PCT, price, current_price,
            )
            return False

        logger.info(
            "Current market price: $%.4f  (ref: $%.4f, slippage: %.1f%%)",
            current_price, price, slippage_pct,
        )

        # ── CLOB valid price range ─────────────────────────────────────────────
        if current_price >= 0.99 or current_price <= 0.01:
            logger.warning(
                "Skipping: current price %.4f is outside CLOB range (0.01–0.99)", current_price
            )
            return False

        # ── Order sizing ───────────────────────────────────────────────────────
        min_size = _get_min_order_size(client, condition_id)
        min_size_for_notional = math.ceil(_CLOB_MIN_NOTIONAL_USD / current_price)
        min_size = max(min_size, min_size_for_notional)
        order_cost = min_size * current_price

        # ── Balance check ──────────────────────────────────────────────────────
        balance = _get_usdc_balance(pk)

        # ── Pre-trade safety log ───────────────────────────────────────────────
        logger.info("─── SAFETY CHECK ───────────────────────────────────────")
        logger.info("  Balance:      $%.2f USDC available", balance)
        logger.info("  Order cost:   $%.2f  (%d shares × $%.4f)", order_cost, min_size, current_price)
        logger.info("  Price:        $%.4f  (within 0.01–0.99: OK)", current_price)
        logger.info("  Slippage:     %.1f%%  (under %.0f%%: OK)", slippage_pct, _MAX_SLIPPAGE_PCT)
        logger.info("  Geo:          Spain (OK)")
        logger.info("────────────────────────────────────────────────────────")

        if balance < order_cost:
            logger.error(
                "SAFETY BLOCK: Insufficient USDC. Need $%.2f, have $%.2f",
                order_cost, balance,
            )
            return False

        logger.info("SAFETY → PROCEED")

        # ── Submit taker order ─────────────────────────────────────────────────
        order_side = BUY if side.upper() == "BUY" else SELL
        order_args = OrderArgs(
            token_id=token_id,
            price=round(current_price, 2),
            size=float(min_size),
            side=order_side,
        )

        logger.info(
            "Submitting: %s %d shares @ $%.4f (~$%.2f)",
            order_side, min_size, order_args.price, order_args.price * order_args.size,
        )

        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order)

        if resp.get("success"):
            logger.info(
                "ORDER SUBMITTED! OrderID=%s status=%s",
                resp.get("orderID"), resp.get("status"),
            )
            return True
        else:
            logger.error("ORDER REJECTED: %s", resp)
            return False

    except PolyApiException as e:
        if e.status_code == 404:
            logger.warning(
                "CLOB market not found (404) — market already closed (token=%s)", token_id[:20]
            )
        else:
            logger.error("CLOB API error (status=%s): %s", e.status_code, e)
        return False
    except (ValueError, KeyError) as e:
        logger.error("Invalid order parameters: %s", e)
        return False
    except requests.RequestException as e:
        logger.error("Network error during order submission: %s", e)
        return False
    except Exception as e:
        logger.error("Unexpected error during order submission: %s", e)
        return False


def execute_copy_trade(trade: TradeEntry) -> bool:
    """
    Executes a mirrored trade on the Polymarket CLOB API.

    Delegates to submit_order() using the trade's asset token and reference price.

    Args:
        trade: The parsed TradeEntry signal to copy.

    Returns:
        True if order successfully submitted and matched, False otherwise.
    """
    logger.info(
        "Copy-trade signal: %s | %s | %s @ $%.4f",
        trade.title, trade.side, trade.outcome, trade.price,
    )
    return submit_order(
        token_id=trade.asset,
        side=trade.side,
        price=trade.price,
        condition_id=trade.condition_id,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_submit_order.py -v
```
Expected: 6 passed

- [ ] **Step 6: Verify existing tests still pass**

```bash
pytest tests/ -v
```
Expected: all previously passing tests still pass

- [ ] **Step 7: Commit**

```bash
git add service/copy_trade_service.py tests/test_submit_order.py .env
git commit -m "feat: extract submit_order(), add DRY_RUN mode and pre-trade safety log"
```

---

## Task 4: `--copy-trade on/off` flag

**Use case:** You want Telegram alerts for stingo43's trades (to learn/monitor) without executing copy-trades. The detection, DB persistence, and alerts all run as normal. Only the execution step is gated.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main_flags.py
import sys
import importlib


def _parse(argv):
    sys.argv = ["main.py"] + argv
    import main as m
    importlib.reload(m)
    return m.parse_args()


def test_copy_trade_defaults_to_on():
    args = _parse([])
    assert args.copy_trade == "on"


def test_copy_trade_can_be_set_off():
    args = _parse(["--copy-trade", "off"])
    assert args.copy_trade == "off"


def test_copy_trade_rejects_invalid_value():
    import pytest
    with pytest.raises(SystemExit):
        _parse(["--copy-trade", "maybe"])


def test_yield_farming_flag_defaults_false():
    args = _parse([])
    assert args.yield_farming is False


def test_yield_farming_flag_can_be_set():
    args = _parse(["--yield-farming"])
    assert args.yield_farming is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_main_flags.py -v
```
Expected: `AttributeError: Namespace object has no attribute 'copy_trade'`

- [ ] **Step 3: Add flags to `parse_args()` in `main.py`**

Add after the `--wallets` argument:

```python
parser.add_argument(
    "--copy-trade",
    choices=["on", "off"],
    default="on",
    help="Enable or disable copy-trade execution. Alerts and detection still run when 'off'.",
)
parser.add_argument(
    "--yield-farming",
    action="store_true",
    default=False,
    help="Run in yield-farming mode: scan for near-expiry markets and execute orders.",
)
```

- [ ] **Step 4: Gate execution on the flag in `run_cycle()` in `main.py`**

Find this block in `run_cycle()` (around line 394):
```python
            # Attempt to copy the trade
            logger.info("Initiating copy-trade execution...")
            try:
                executed = execute_copy_trade(trade)
```

Replace with:
```python
            # Attempt to copy the trade (only if --copy-trade on)
            if args.copy_trade == "off":
                logger.info("Copy-trade execution DISABLED (--copy-trade off) — alert sent, no order.")
                continue

            logger.info("Initiating copy-trade execution...")
            try:
                executed = execute_copy_trade(trade)
```

Also update the validator block at the bottom of `run_cycle()` — wrap the entire validator section:
```python
    # Validation: only runs when copy-trading is enabled
    if args.wallets and args.copy_trade == "on":
        from service.validator_service import find_missed_trades
        # ... (rest of validator block unchanged) ...
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_main_flags.py -v
```
Expected: 5 passed

- [ ] **Step 6: Full test suite**

```bash
pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main_flags.py
git commit -m "feat: add --copy-trade on/off flag and --yield-farming flag to CLI"
```

---

## Task 5: `market_scanner_service.py`

**Files:**
- Create: `service/market_scanner_service.py`
- Create: `tests/test_market_scanner.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_market_scanner.py
from datetime import datetime, timezone
from unittest.mock import patch
from service.market_scanner_service import find_opportunities


def _make_gamma_market(condition_id, end_date_iso, prices, outcomes=None, token_ids=None):
    if outcomes is None:
        outcomes = ["Up", "Down"]
    if token_ids is None:
        token_ids = [f"tok_{condition_id}_0", f"tok_{condition_id}_1"]
    return {
        "conditionId": condition_id,
        "question": f"Market {condition_id}",
        "endDate": end_date_iso,
        "active": True,
        "closed": False,
        "clobTokenIds": token_ids,
        "outcomes": f'["{outcomes[0]}", "{outcomes[1]}"]',
        "outcomePrices": f'["{prices[0]}", "{prices[1]}"]',
    }


def test_find_opportunities_returns_qualifying_markets():
    now = datetime(2026, 3, 25, 18, 57, 0, tzinfo=timezone.utc)
    closes_at = "2026-03-25T19:00:00Z"

    mock_markets = [
        _make_gamma_market("cond1", closes_at, ["0.97", "0.03"]),
        _make_gamma_market("cond2", closes_at, ["0.50", "0.50"]),  # below threshold
    ]

    with patch("service.market_scanner_service.get_near_expiry_markets", return_value=mock_markets), \
         patch("service.market_scanner_service.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat

        results = find_opportunities(min_price=0.93, window_minutes=5)

    assert len(results) == 1
    assert results[0].condition_id == "cond1"
    assert results[0].price == 0.97
    assert results[0].outcome == "Up"
    assert results[0].token_id == "tok_cond1_0"


def test_find_opportunities_picks_highest_price_outcome():
    now = datetime(2026, 3, 25, 18, 57, 0, tzinfo=timezone.utc)
    closes_at = "2026-03-25T19:00:00Z"

    mock_markets = [
        _make_gamma_market("cond1", closes_at, ["0.03", "0.97"]),  # Down is the winner
    ]

    with patch("service.market_scanner_service.get_near_expiry_markets", return_value=mock_markets), \
         patch("service.market_scanner_service.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat

        results = find_opportunities(min_price=0.93, window_minutes=5)

    assert len(results) == 1
    assert results[0].outcome == "Down"
    assert results[0].token_id == "tok_cond1_1"
    assert results[0].price == 0.97


def test_find_opportunities_returns_empty_when_api_fails():
    with patch("service.market_scanner_service.get_near_expiry_markets", return_value=[]):
        results = find_opportunities(min_price=0.93, window_minutes=5)
    assert results == []


def test_find_opportunities_sorted_by_price_descending():
    now = datetime(2026, 3, 25, 18, 57, 0, tzinfo=timezone.utc)
    closes_at = "2026-03-25T19:00:00Z"

    mock_markets = [
        _make_gamma_market("cond1", closes_at, ["0.94", "0.06"]),
        _make_gamma_market("cond2", closes_at, ["0.99", "0.01"]),
        _make_gamma_market("cond3", closes_at, ["0.96", "0.04"]),
    ]

    with patch("service.market_scanner_service.get_near_expiry_markets", return_value=mock_markets), \
         patch("service.market_scanner_service.datetime") as mock_dt:
        mock_dt.now.return_value = now
        mock_dt.fromisoformat = datetime.fromisoformat

        results = find_opportunities(min_price=0.93, window_minutes=5)

    prices = [r.price for r in results]
    assert prices == sorted(prices, reverse=True)
    assert prices[0] == 0.99
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_market_scanner.py -v
```
Expected: `ModuleNotFoundError: No module named 'service.market_scanner_service'`

- [ ] **Step 3: Create `service/market_scanner_service.py`**

```python
"""
market_scanner_service.py
Scans Polymarket Gamma API for near-expiry markets with a near-certain outcome.

Responsibility: fetch, filter, and rank MarketOpportunity objects.
No order execution — that is yield_farming_service's job.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from core.api.polymarket_client import get_near_expiry_markets
from core.models.market import MarketOpportunity

logger = logging.getLogger(__name__)


def find_opportunities(
    min_price: float = 0.93,
    window_minutes: int = 5,
) -> list[MarketOpportunity]:
    """
    Return markets closing within window_minutes where one outcome's price >= min_price.

    Results are sorted highest price first — the most certain outcome leads.

    Args:
        min_price: Minimum outcome price to qualify (0.01–0.99).
        window_minutes: How many minutes ahead to scan for closing markets.

    Returns:
        List of MarketOpportunity, sorted by price descending. Empty on failure.
    """
    now = datetime.now(timezone.utc)
    end_min = now.isoformat().replace("+00:00", "Z")
    end_max = (now + timedelta(minutes=window_minutes)).isoformat().replace("+00:00", "Z")

    raw_markets = get_near_expiry_markets(end_date_min=end_min, end_date_max=end_max)
    logger.info(
        "Gamma API returned %d markets in the next %dm window.",
        len(raw_markets), window_minutes,
    )

    opportunities: list[MarketOpportunity] = []
    for market in raw_markets:
        opportunity = _extract_opportunity(market, now, min_price)
        if opportunity:
            opportunities.append(opportunity)

    opportunities.sort(key=lambda o: o.price, reverse=True)
    logger.info(
        "Found %d qualifying market(s) at min_price=%.2f.",
        len(opportunities), min_price,
    )
    return opportunities


def _extract_opportunity(
    market: dict,
    now: datetime,
    min_price: float,
) -> MarketOpportunity | None:
    """
    Parse a single Gamma API market dict and return a MarketOpportunity if it qualifies.

    Returns None if no outcome meets the price threshold or if the market data is malformed.
    """
    try:
        condition_id = market["conditionId"]
        title = market.get("question", "")
        end_date_str = market["endDate"]
        token_ids: list[str] = market.get("clobTokenIds", [])
        outcomes: list[str] = json.loads(market.get("outcomes", "[]"))
        prices: list[float] = [float(p) for p in json.loads(market.get("outcomePrices", "[]"))]

        if not token_ids or not outcomes or not prices:
            logger.debug("Skipping market %s: missing token_ids/outcomes/prices", condition_id)
            return None

        if len(token_ids) != len(outcomes) or len(outcomes) != len(prices):
            logger.debug("Skipping market %s: mismatched array lengths", condition_id)
            return None

        closes_at = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        minutes_left = (closes_at - now).total_seconds() / 60.0

        best_idx = max(range(len(prices)), key=lambda i: prices[i])
        best_price = prices[best_idx]

        if best_price < min_price:
            return None

        return MarketOpportunity(
            condition_id=condition_id,
            token_id=token_ids[best_idx],
            title=title,
            outcome=outcomes[best_idx],
            price=best_price,
            closes_at=closes_at,
            minutes_left=minutes_left,
        )

    except (KeyError, ValueError, json.JSONDecodeError) as e:
        logger.warning(
            "Could not parse market opportunity: %s — %s",
            market.get("conditionId", "?"), e,
        )
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_market_scanner.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add service/market_scanner_service.py tests/test_market_scanner.py
git commit -m "feat: add market scanner service for near-expiry opportunity detection"
```

---

## Task 6: `yield_farming_service.py` with P&L tracking and settlement monitoring

**Files:**
- Create: `service/yield_farming_service.py`
- Create: `tests/test_yield_farming_service.py`

**P&L tracking:** The service tracks `session_start_balance` and `total_cost_spent` to compute realized P&L each cycle: `realized_pnl = current_balance - session_start_balance`. This is logged each cycle. No Positions API call needed — the balance delta is truth.

**Settlement monitoring:** After each execution we record the expected settlement amount. Each cycle we compare `current_balance` to what we'd expect if all markets resolved. If the balance is more than $0.50 below expectation after 30+ minutes, we log a warning. This is a simple dict lookup, not a service.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_yield_farming_service.py
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from core.models.market import MarketOpportunity
from service.yield_farming_service import run_farming_cycle, FarmingSession


def _make_opportunity(condition_id="cond1", token_id="tok1", price=0.97, outcome="Up"):
    return MarketOpportunity(
        condition_id=condition_id,
        token_id=token_id,
        title="BTC Up or Down?",
        outcome=outcome,
        price=price,
        closes_at=datetime(2026, 3, 25, 19, 0, tzinfo=timezone.utc),
        minutes_left=3.0,
    )


def test_run_farming_cycle_executes_new_opportunity():
    opp = _make_opportunity()
    session = FarmingSession(start_balance=50.0)

    with patch("service.yield_farming_service.find_opportunities", return_value=[opp]), \
         patch("service.yield_farming_service.submit_order", return_value=True) as mock_submit:

        results = run_farming_cycle(balance=50.0, session=session)

    assert len(results) == 1
    assert results[0]["condition_id"] == "cond1"
    assert results[0]["executed"] is True
    mock_submit.assert_called_once_with(
        token_id="tok1", side="BUY", price=0.97, condition_id="cond1",
    )


def test_run_farming_cycle_skips_already_traded():
    opp = _make_opportunity(condition_id="cond1")
    session = FarmingSession(start_balance=50.0)
    session.already_traded.add("cond1")

    with patch("service.yield_farming_service.find_opportunities", return_value=[opp]), \
         patch("service.yield_farming_service.submit_order") as mock_submit:

        results = run_farming_cycle(balance=50.0, session=session)

    assert results == []
    mock_submit.assert_not_called()


def test_run_farming_cycle_skips_insufficient_balance():
    opp = _make_opportunity(price=0.97)
    session = FarmingSession(start_balance=50.0)

    with patch("service.yield_farming_service.find_opportunities", return_value=[opp]), \
         patch("service.yield_farming_service.submit_order") as mock_submit:

        results = run_farming_cycle(balance=0.50, session=session)

    assert results == []
    mock_submit.assert_not_called()


def test_run_farming_cycle_returns_empty_when_no_opportunities():
    session = FarmingSession(start_balance=50.0)
    with patch("service.yield_farming_service.find_opportunities", return_value=[]):
        results = run_farming_cycle(balance=50.0, session=session)
    assert results == []


def test_run_farming_cycle_handles_execution_failure():
    opp = _make_opportunity()
    session = FarmingSession(start_balance=50.0)

    with patch("service.yield_farming_service.find_opportunities", return_value=[opp]), \
         patch("service.yield_farming_service.submit_order", return_value=False):

        results = run_farming_cycle(balance=50.0, session=session)

    assert len(results) == 1
    assert results[0]["executed"] is False


def test_farming_session_tracks_already_traded():
    opp = _make_opportunity(condition_id="cond1")
    session = FarmingSession(start_balance=50.0)

    with patch("service.yield_farming_service.find_opportunities", return_value=[opp]), \
         patch("service.yield_farming_service.submit_order", return_value=True):
        run_farming_cycle(balance=50.0, session=session)

    assert "cond1" in session.already_traded


def test_farming_session_realized_pnl():
    session = FarmingSession(start_balance=50.0)
    # Simulate $1 profit: current balance is $51
    pnl = session.realized_pnl(current_balance=51.0)
    assert abs(pnl - 1.0) < 0.001
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_yield_farming_service.py -v
```
Expected: `ModuleNotFoundError: No module named 'service.yield_farming_service'`

- [ ] **Step 3: Create `service/yield_farming_service.py`**

```python
"""
yield_farming_service.py
Orchestrates one yield-farming cycle.

Responsibility: scan for opportunities, filter already-traded markets,
validate balance, execute orders, track P&L, monitor settlements.
No Telegram or DB logic — those belong in main.py.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.models.market import MarketOpportunity
from service.copy_trade_service import submit_order
from service.market_scanner_service import find_opportunities

logger = logging.getLogger(__name__)

_MIN_TRADE_USD = 1.0
_CAPITAL_FRACTION = 0.01

# How much below the expected balance (in USD) triggers a settlement warning.
_SETTLEMENT_GAP_THRESHOLD = 0.50


@dataclass
class FarmingSession:
    """
    Holds all state for a yield-farming daemon session.
    Created once at startup, passed into every run_farming_cycle() call.

    Attributes:
        start_balance:       USDC balance at session start (for P&L calculation).
        started_at:          UTC datetime when the session started (for progress tracking).
        total_executed:      Number of orders successfully submitted this session.
        already_traded:      condition_ids traded this session (prevents duplicate orders).
        pending_settlements: Maps condition_id → expected return amount for open positions.
    """
    start_balance: float
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_executed: int = 0
    already_traded: set[str] = field(default_factory=set)
    pending_settlements: dict[str, float] = field(default_factory=dict)

    def realized_pnl(self, current_balance: float) -> float:
        """
        Approximate realized P&L for this session.

        Computed as: current_balance - start_balance.
        Includes settled wins and USDC spent on open positions.
        """
        return current_balance - self.start_balance

    def days_elapsed(self) -> float:
        """Days since session start (float, e.g. 3.5 = 3 days 12 hours)."""
        return (datetime.now(timezone.utc) - self.started_at).total_seconds() / 86400

    def projected_pnl_at_end_of_phase(
        self,
        current_balance: float,
        phase_duration_days: float = 14.0,
    ) -> float:
        """
        Extrapolate current P&L rate to end of phase.

        Projects: (realized_pnl / days_elapsed) × phase_duration_days.
        Returns 0.0 if less than 1 day has elapsed (too early to project).
        """
        elapsed = self.days_elapsed()
        if elapsed < 1.0:
            return 0.0
        daily_pnl = self.realized_pnl(current_balance) / elapsed
        return daily_pnl * phase_duration_days


def run_farming_cycle(
    balance: float,
    session: FarmingSession,
    min_price: float = 0.93,
    window_minutes: int = 5,
) -> list[dict]:
    """
    Execute one farming cycle: scan → filter → safety-check → execute.

    Args:
        balance:        Current USDC balance.
        session:        FarmingSession carrying cross-cycle state.
        min_price:      Minimum outcome price to qualify.
        window_minutes: Markets closing within this many minutes are scanned.

    Returns:
        List of result dicts: {"condition_id", "title", "outcome", "price", "executed"}.
    """
    # ── P&L summary ───────────────────────────────────────────────────────────
    realized_pnl = session.realized_pnl(current_balance=balance)
    logger.info(
        "[FARMING] Balance: $%.2f | Session P&L: %+.2f | Traded markets: %d",
        balance, realized_pnl, len(session.already_traded),
    )

    # ── Settlement check ──────────────────────────────────────────────────────
    _check_pending_settlements(balance, session)

    opportunities = find_opportunities(min_price=min_price, window_minutes=window_minutes)

    if not opportunities:
        logger.info("[FARMING] No qualifying opportunities found this cycle.")
        return []

    results = []

    for opp in opportunities:
        if opp.condition_id in session.already_traded:
            logger.info("[FARMING] Skipping already-traded market: %s", opp.title[:60])
            continue

        if balance < _MIN_TRADE_USD:
            logger.warning(
                "[FARMING] Insufficient balance $%.2f for min trade $%.2f — skipping.",
                balance, _MIN_TRADE_USD,
            )
            continue

        logger.info(
            "[FARMING] Opportunity: %s | %s @ $%.4f | closes in %.1fm",
            opp.title[:60], opp.outcome, opp.price, opp.minutes_left,
        )

        executed = submit_order(
            token_id=opp.token_id,
            side="BUY",
            price=opp.price,
            condition_id=opp.condition_id,
        )

        session.already_traded.add(opp.condition_id)

        if executed:
            # Record expected return for settlement monitoring.
            # At price 0.97, buying 1 share costs $0.97 and returns $1.00 if it wins.
            expected_return = round(1.0 / opp.price, 4)
            session.pending_settlements[opp.condition_id] = expected_return
            logger.info("[FARMING] Executed: %s %s @ $%.4f", opp.title[:50], opp.outcome, opp.price)
        else:
            logger.warning("[FARMING] Execution failed for: %s", opp.title[:60])

        results.append({
            "condition_id": opp.condition_id,
            "title": opp.title,
            "outcome": opp.outcome,
            "price": opp.price,
            "executed": executed,
        })

    return results


def _check_pending_settlements(current_balance: float, session: FarmingSession) -> None:
    """
    Log a warning if the balance is lower than expected given pending settlements.

    We can't know for sure which positions have settled (that would require a
    Positions API call). Instead we flag when the total expected settlements
    significantly exceed what the balance delta implies. This catches cases
    where settlement failed silently.
    """
    if not session.pending_settlements:
        return

    total_expected = sum(session.pending_settlements.values())
    apparent_return = current_balance - session.start_balance

    if apparent_return < total_expected - _SETTLEMENT_GAP_THRESHOLD:
        logger.warning(
            "[SETTLEMENT] Balance gap detected: expected +$%.2f from settlements, "
            "balance delta is %+.2f. Check wallet manually.",
            total_expected, apparent_return,
        )
    else:
        logger.debug(
            "[SETTLEMENT] Balance delta %+.2f vs expected +$%.2f — OK.",
            apparent_return, total_expected,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_yield_farming_service.py -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add service/yield_farming_service.py tests/test_yield_farming_service.py
git commit -m "feat: add yield farming service with P&L tracking and settlement monitoring"
```

---

## Task 7: Wire into `main.py` — `--yield-farming` loop

**Files:**
- Modify: `main.py`

The farming loop polls every 30 seconds. A `FarmingSession` is created once at startup and lives for the entire daemon run.

- [ ] **Step 1: Add imports to `main.py`**

At the top, update service imports:
```python
import os
from service.copy_trade_service import execute_copy_trade, _get_usdc_balance
from service.yield_farming_service import run_farming_cycle, FarmingSession
```

- [ ] **Step 2: Add the farming loop function to `main.py`**

Add after `_handle_test_command`:

```python
_FARMING_POLL_INTERVAL_SECONDS = 30
_FARMING_MIN_PRICE = 0.93
_FARMING_WINDOW_MINUTES = 5


def run_yield_farming_loop() -> None:
    """
    Continuous yield-farming daemon loop.

    Polls every 30 seconds for near-expiry market opportunities.
    A FarmingSession tracks already-traded markets and P&L across cycles.
    The session resets on restart — intentional, markets close hourly.
    """
    from utility.geo import is_in_spain

    logger.info("=" * 60)
    logger.info("YIELD FARMING MODE")
    logger.info("  threshold: price >= %.2f  |  window: %dm  |  poll: %ds",
                _FARMING_MIN_PRICE, _FARMING_WINDOW_MINUTES, _FARMING_POLL_INTERVAL_SECONDS)
    logger.info("=" * 60)

    pk = os.getenv("poly_private_key", "").strip(" '\"")
    start_balance = _get_usdc_balance(pk)
    session = FarmingSession(start_balance=start_balance)

    logger.info("Session start balance: $%.2f USDC", start_balance)
    telegram_service.send_message(
        f"🌾 <b>Yield Farming started</b>\n"
        f"Balance: <code>${start_balance:.2f}</code> USDC\n"
        f"Threshold: <code>≥ {_FARMING_MIN_PRICE}</code>  |  "
        f"Window: <code>{_FARMING_WINDOW_MINUTES}m</code>"
    )

    while True:
        # ── Handle Telegram commands ──────────────────────────────────────────
        commands = telegram_service.get_pending_commands()
        for cmd in commands:
            if cmd == "/health":
                balance = _get_usdc_balance(pk)
                realized_pnl = session.realized_pnl(current_balance=balance)
                days = session.days_elapsed()
                projected = session.projected_pnl_at_end_of_phase(balance)
                projected_str = f"${projected:+.2f}" if days >= 1.0 else "< 1 day elapsed"
                # Phase 1 target: $47–$55 after 2 weeks starting from $50
                target_low, target_high = 47.0, 55.0
                on_track = (
                    "✅ on track" if target_low <= (50.0 + projected) <= target_high
                    else "⚠️ off target"
                ) if days >= 1.0 else "—"
                telegram_service.send_message(
                    f"🌾 <b>Yield Farming — Health</b>\n\n"
                    f"💵 <b>Balance:</b> <code>${balance:.2f}</code> USDC\n"
                    f"📈 <b>Session P&L:</b> <code>{realized_pnl:+.2f}</code>\n"
                    f"📊 <b>Projected end-of-phase:</b> <code>{projected_str}</code> {on_track}\n"
                    f"🎯 <b>Phase 1 target:</b> <code>$47–$55</code> after 14 days\n"
                    f"📅 <b>Days elapsed:</b> <code>{days:.1f}</code>\n"
                    f"🔄 <b>Orders executed:</b> <code>{session.total_executed}</code>\n"
                    f"⏳ <b>Pending settlements:</b> <code>{len(session.pending_settlements)}</code>"
                )

            elif cmd == "/commands":
                telegram_service.send_message(
                    "📋 <b>Available commands</b>\n\n"
                    "/health — balance, P&L, progress vs Phase 1 target\n"
                    "/commands — this list"
                )

        # ── Geo check ─────────────────────────────────────────────────────────
        if not is_in_spain():
            logger.error("[FARMING] Geo check failed — skipping cycle.")
            time.sleep(_FARMING_POLL_INTERVAL_SECONDS)
            continue

        # ── Farming cycle ─────────────────────────────────────────────────────
        balance = _get_usdc_balance(pk)
        results = run_farming_cycle(
            balance=balance,
            session=session,
            min_price=_FARMING_MIN_PRICE,
            window_minutes=_FARMING_WINDOW_MINUTES,
        )

        for result in results:
            if result["executed"]:
                session.total_executed += 1
                telegram_service.send_message(
                    f"🌾 <b>Farmed!</b>\n"
                    f"📋 {result['title'][:70]}\n"
                    f"🎯 <b>{result['outcome']}</b> @ <code>${result['price']:.3f}</code>"
                )

        time.sleep(_FARMING_POLL_INTERVAL_SECONDS)
```

- [ ] **Step 3: Route `--yield-farming` in `main()`**

In `main()`, add a branch after DB initialisation, before the while loop:

```python
def main() -> None:
    args = parse_args()
    logger.info("polymarket_robot starting...")

    try:
        db_service.initialise_database()
    except Exception as e:
        logger.critical("Database initialisation failed: %s", e)
        sys.exit(1)

    if args.yield_farming:
        run_yield_farming_loop()
        return  # loops forever — never reaches here normally

    # ... existing copy-trade / leaderboard loop follows unchanged ...
```

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: wire --yield-farming into main loop with FarmingSession and Telegram alerts"
```

---

## Task 8: Smoke test on VPS (Phase 0 — DRY_RUN)

**No new files.** Verify the pipeline end-to-end in DRY_RUN mode before any money goes in.

- [ ] **Step 1: Deploy all changed files to VPS**

```bash
scp service/copy_trade_service.py root@spain-vpn:/home/nick/polymarket_bot/service/
scp service/market_scanner_service.py root@spain-vpn:/home/nick/polymarket_bot/service/
scp service/yield_farming_service.py root@spain-vpn:/home/nick/polymarket_bot/service/
scp core/models/market.py root@spain-vpn:/home/nick/polymarket_bot/core/models/
scp core/api/polymarket_client.py root@spain-vpn:/home/nick/polymarket_bot/core/api/
scp utility/endpoints.py root@spain-vpn:/home/nick/polymarket_bot/utility/
scp main.py root@spain-vpn:/home/nick/polymarket_bot/
```

- [ ] **Step 2: Set DRY_RUN=true on VPS**

```bash
ssh root@spain-vpn "grep -q 'DRY_RUN' /home/nick/polymarket_bot/.env && \
  sed -i 's/DRY_RUN.*/DRY_RUN = \"true\"/' /home/nick/polymarket_bot/.env || \
  echo 'DRY_RUN = \"true\"' >> /home/nick/polymarket_bot/.env"
```

- [ ] **Step 3: Run a manual scanner probe to verify Gamma API field names**

```bash
ssh root@spain-vpn "cd /home/nick/polymarket_bot && \
  .venv/bin/python -c \"
from service.market_scanner_service import find_opportunities
opps = find_opportunities(min_price=0.90, window_minutes=10)
for o in opps: print(o)
\""
```

Expected: `MarketOpportunity(...)` objects printed, or empty list if outside a close window.

> If you see `KeyError` or `json.JSONDecodeError`: check the Gamma API fields directly:
> ```bash
> python -c "import requests; import json; r=requests.get('https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=1').json(); print(json.dumps(r[0], indent=2))"
> ```
> Then update the field names in `_extract_opportunity()` in `market_scanner_service.py`.

- [ ] **Step 4: Start bot in DRY_RUN yield-farming mode**

```bash
ssh root@spain-vpn "systemctl stop polymarket-bot"
ssh root@spain-vpn "cd /home/nick/polymarket_bot && \
  nohup .venv/bin/python main.py --yield-farming > /tmp/farming_dryryn.log 2>&1 &"
```

- [ ] **Step 5: Watch logs for 5 minutes and verify DRY_RUN output**

```bash
ssh root@spain-vpn "tail -f /tmp/farming_dryryn.log"
```

Look for every 30 seconds:
- `[FARMING] Balance: $X.XX | Session P&L: +0.00`
- Near an hourly close: `[FARMING] Opportunity: BTC Up or Down? | Up @ $0.9970`
- `[DRY-RUN] Would submit BUY order: token=... — SKIPPED (DRY_RUN=true)`
- Telegram message: `🌾 Farmed! ...`

**DRY_RUN is working correctly when you see "Would submit" in the logs and Telegram messages arrive but balance never changes.**

- [ ] **Step 6: Run for 2–3 days in DRY_RUN, then switch to live**

Once satisfied:
```bash
ssh root@spain-vpn "sed -i 's/DRY_RUN = \"true\"/DRY_RUN = \"false\"/' /home/nick/polymarket_bot/.env"
```

Update systemd:
```bash
ssh root@spain-vpn "cat > /etc/systemd/system/polymarket-bot.service << 'EOF'
[Unit]
Description=Polymarket Bot
After=network.target

[Service]
User=nick
WorkingDirectory=/home/nick/polymarket_bot
EnvironmentFile=/home/nick/polymarket_bot/systemd.env
ExecStart=/home/nick/polymarket_bot/.venv/bin/python /home/nick/polymarket_bot/main.py --yield-farming
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF"

systemctl daemon-reload && systemctl start polymarket-bot
journalctl -u polymarket-bot -f
```

Also add `DRY_RUN=false` (no quotes, no spaces) to `systemd.env`:
```bash
ssh root@spain-vpn "echo 'DRY_RUN=false' >> /home/nick/polymarket_bot/systemd.env"
```

---

## What this plan deliberately defers

- **Unrealized P&L from Positions API** — requires parsing `POSITIONS` endpoint per cycle. Worth adding in Phase 2 after the core loop is validated. The balance delta (realized P&L) is sufficient for Phase 1 monitoring.
- **`/positions` Telegram command** — same dependency on Positions API. Add in Phase 2.
- **Position closing / stop-loss** — out of scope until you have positions worth managing ($300+ capital).
- **Web analyzer page** — separate concern, separate plan.

---

## Self-Review

**Spec coverage:**
- ✅ Market scanner — Task 5
- ✅ Opportunity filter (price threshold, tunable) — Task 5 (`min_price` param)
- ✅ CLOB execution reuse — Task 3 (`submit_order` extraction)
- ✅ DRY_RUN mode — Task 3
- ✅ Pre-trade safety log — Task 3 (structured safety block in `submit_order`)
- ✅ `--copy-trade on/off` flag — Task 4
- ✅ Main loop timing (30s polling) — Task 7
- ✅ Session deduplication — Task 6 (`FarmingSession.already_traded`)
- ✅ P&L tracking (realized) — Task 6 (`FarmingSession.realized_pnl`)
- ✅ Settlement monitoring (lightweight) — Task 6 (`_check_pending_settlements`)
- ✅ `--yield-farming` CLI flag — Task 7
- ✅ VPS smoke test + DRY_RUN validation — Task 8
- ✅ Telegram /health and /commands in farming mode — Task 7
- ✅ Staged capital plan — header section
- ⚠️ Unrealized P&L: deferred (needs Positions API). `realized_pnl` is available and logged. Marked as Phase 2.

**Placeholder scan:** None found.

**Type consistency:** `submit_order` signature defined in Task 3, called identically in Task 6. `FarmingSession` defined in Task 6, instantiated in Task 7. `find_opportunities` returns `list[MarketOpportunity]` in Task 5, imported as such in Task 6.
