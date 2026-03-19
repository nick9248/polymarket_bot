# ANTIGRAVITY.md

This file provides guidance to Antigravity (Google DeepMind's agentic AI coding assistant) when working with code in this repository.

## Project Overview

- **Purpose**: Polymarket prediction market automation — leaderboard analysis, trader profiling, and signal generation
- **Hardware**: Intel 14900K CPU + NVIDIA 5090 Suprim SOC Liquid GPU
- **Python Version**: 3.13
- **Testing Framework**: pytest
- **OS**: Windows
- **IDE**: PyCharm 2025.2.3

## Setup

### Environment Variables

API credentials and secrets are stored in a `.env` file (not committed to git):

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and fill in your credentials:
   ```
   POLYMARKET_API_KEY=your_key_here
   ```

The application will automatically load credentials from `.env` on startup.

### Virtual Environment

```bash
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Permissions

**Granted**:
- Read (all files)
- Write (create, edit)
- Execute (bash, scripts)
- File management (move, rename)

**Requires Approval**:
- Delete operations (files, directories)

## Communication Style

- Always say the truth without sugar coating
- Mention potential problems and risks proactively
- Explain trade-offs clearly
- Don't use over-the-top validation or excessive praise
- Focus on technical accuracy over emotional validation
- If uncertain about something, investigate to find the truth first rather than confirming user's beliefs
- **BE PRECISE** - Never make assumptions or generalizations without verifying facts
- **NO HYPOCRISY** - If there's a problem, admit it directly and investigate thoroughly
- **NO QUICK FIXES** - Search deeply, think carefully, find the root cause, implement future-proof solutions
- When investigating issues:
  1. Don't assume - verify with actual data
  2. Don't quick patch - find the structural root cause
  3. Don't sugar coat - state the problem clearly
  4. Think through all possibilities before concluding
  5. Implement solutions that prevent the issue class, not just the symptom

## MANDATORY VERIFICATION CHECKLIST

**CRITICAL: Never say a task is "done" or "working" without completing ALL verification steps.**

For EVERY implementation task, you MUST:

1. ✅ **Code Implemented** - Write the code
2. ✅ **Code Runs Without Errors** - Execute and verify no crashes
3. ✅ **VERIFY ACTUAL RESULTS** - Check files/output/API responses contain expected data
4. ✅ **Compare Expected vs Actual** - Does the data match specifications?
5. ✅ **Test Edge Cases** - Handle API failures, rate limits, missing fields, empty responses
6. ✅ **Show Verification Results** - Provide proof (actual API responses, printed data, logs)

**"Code runs without errors" ≠ "Code works correctly"**

### Examples of Proper Verification:

**BAD (No Verification):**
- "I implemented the leaderboard fetcher. It's running successfully. ✓ Done!"

**GOOD (With Verification):**
- "I implemented the leaderboard fetcher. Let me verify:
  - Script runs: ✓
  - API responded with 200: ✓
  - Parsed 25 traders correctly: ✓
  - PnL values are non-zero and plausible: ✓
  - Sample output: [shows actual printed data]"

## Documentation Rules

Only create detailed documentation summaries when:
1. The entire project phase is finished
2. The user explicitly confirms it's time to document
3. Requested by the user

For small task completions: write concise summaries in console output only. Do NOT create separate documentation files unless requested.

## Project Structure

Layered architecture: **Core** (models/definitions) → **Service** (orchestration/business logic) → **CLI/GUI** (presentation)

Key directories (to be created):
- `core/` - Data models, API client definitions, base classes
- `service/` - Business logic, leaderboard analysis, signal generation
- `scripts/` - One-off utilities, data validation, diagnostics
- `tests/` - Unit and integration tests

**Structure Rule**: Code files must be inside related folders (e.g., `core/api/polymarket_client.py` not `core/polymarket_client.py`).

## API Reference

### Polymarket Data API (Free, No Auth Required)

**Base URL**: `https://data-api.polymarket.com/v1`

| Endpoint | Description |
|---|---|
| `GET /leaderboard` | Trader leaderboard (PnL or Volume ranked) |
| `GET /builders/leaderboard` | Builder/platform leaderboard |

**Leaderboard Parameters**:
- `category`: `OVERALL`, `POLITICS`, `SPORTS`, `CRYPTO`, `CULTURE`, `ECONOMICS`, `TECH`, `FINANCE`, `WEATHER`
- `timePeriod`: `DAY`, `WEEK`, `MONTH`, `ALL`
- `orderBy`: `PNL`, `VOL`
- `limit`: 1–50 (default 25)
- `offset`: 0–1000 (pagination)
- `user`: filter by wallet address
- `userName`: filter by username

**Response Fields per Trader**: `rank`, `proxyWallet`, `userName`, `xUsername`, `vol`, `pnl`, `profileImage`, `verifiedBadge`

**Rate Limits**: ~1,000 calls/hour on the free tier (no API key needed).

### CLOB API (Authentication Required — for trading)

Requires L1 (wallet private key) + L2 (API key/secret/passphrase) authentication. Only needed if placing/canceling orders.

## Architecture Principles

Layered architecture with clear separation:

```
Core (data models, API client, definitions)
    ↓
Services (business logic: fetching, parsing, scoring, analysis)
    ↓
CLI / Scripts (presentation, entry points, scheduled jobs)
```

- **Core**: Never contains business logic. Only models and base API methods.
- **Service**: All orchestration, filtering, scoring, signal logic goes here.
- **CLI/Scripts**: Never contains business logic. Only calls services.

## Coding Preferences

- Simple and clear without unnecessary complexity
- Scalable for future expansion
- Completely modular — one class/function per responsibility
- Clear, understandable docstrings on all public methods
- Type hints on all function signatures

## Code Quality Checklist (MANDATORY)

**Before completing ANY code task, verify:**

1. **Layered Architecture**: Does the code follow Core → Service → CLI flow?
   - CLI/Scripts should NEVER contain business logic or direct API calls
   - Services orchestrate operations using core components
   - Core contains definitions, models, and base methods

2. **Modularity**: Is each class/function doing ONE thing?
   - No monolithic classes with multiple responsibilities
   - Each analysis/strategy type should be its own class

3. **Right Layer**: Is the code in the correct layer?
   - API calls → Core API client (called by Service)
   - Data models → Core layer
   - Business logic → Service layer (NOT CLI)
   - Output/display → CLI layer

4. **No Shortcuts**: Even if it works, is it architecturally correct?
   - "It works" is not sufficient — it must be clean

## Problem-Solving Approach

When fixing bugs or issues, follow structural thinking — not quick patches:

1. **Understand the flow first**: Before fixing, trace the data flow and understand WHY the problem exists
2. **Find the root cause**: Don't patch symptoms
3. **Fix at the right layer**: The fix should be in the component responsible for that logic
4. **Maintain clean architecture**: Don't add workarounds that bypass the established flow

## Logging System

Use `logging` module. **Never use `print()`** in production code.

```python
import logging

# For standalone scripts/entry points:
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# For modules (logging already initialized by entry point):
logger = logging.getLogger(__name__)

# Usage:
logger.info("Fetching leaderboard...")
logger.warning("Rate limit approaching")
logger.error(f"API request failed: {error}")
logger.debug("Raw response: %s", response)
```

## Naming Conventions

- Descriptive names related to the method's purpose
- No abbreviations (use `leaderboard_entry` not `lb_entry`)
- Leading underscores for internal/private methods (`_parse_response`)
- Snake_case for files, functions, variables
- PascalCase for classes

## Testing and Verification Methodology

**MANDATORY for all new feature implementations and significant code changes.**

Core process:
1. Fetch real API data using the Polymarket Data API
2. Manually verify expected values step-by-step
3. Compare implementation output to manual calculations
4. Test edge cases (empty responses, missing fields, API timeouts)
5. Document findings and fix issues before marking done

Skip verification only for: trivial changes, pure refactoring with existing tests, config-only changes.

## Git Workflow

- **Main branch**: `main`

For each task:
1. Create a new branch from main
2. Implement the task
3. Wait for user confirmation
4. Push and merge to main

## Commands

```bash
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run unit tests only
pytest tests/unit/

# Run integration tests only
pytest tests/integration/

# Run a specific test
pytest tests/unit/test_file.py::test_function_name

# Run with verbose output
pytest -v
```
