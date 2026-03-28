"""
analyze_market_types.py

Fetches resolved Polymarket markets in bulk, clusters them by title pattern,
and estimates win rate per cluster — i.e. how often the "near-certain" outcome
(lastTradePrice >= threshold) actually resolved correctly.

Usage:
    python scripts/analyze_market_types.py
    python scripts/analyze_market_types.py --threshold 0.90 --pages 20
    python scripts/analyze_market_types.py --output results.json

Output:
    - Console table: cluster | count | tradeable | win_rate | examples
    - Optional JSON dump for deeper inspection
"""

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
REQUEST_TIMEOUT = 15
PAGE_SIZE = 200


# ---------------------------------------------------------------------------
# Title pattern extraction
# ---------------------------------------------------------------------------

# Order matters — first match wins. Patterns are kept intentionally broad so
# we surface clusters we haven't anticipated.
_PATTERNS = [
    ("up_or_down",      r"\bup or down\b"),
    ("higher_or_lower", r"\bhigher or lower\b"),
    ("yes_no_will",     r"^will .+\?$"),
    ("yes_no_is",       r"^is .+\?$"),
    ("yes_no_does",     r"^does .+\?$"),
    ("yes_no_has",      r"^has .+\?$"),
    ("yes_no_did",      r"^did .+\?$"),
    ("yes_no_can",      r"^can .+\?$"),
    ("price_above",     r"\babove\b.+\$[\d,]+"),
    ("price_below",     r"\bbelow\b.+\$[\d,]+"),
    ("price_reach",     r"\breach\b.+\$[\d,]+"),
    ("who_wins",        r"\bwho (will |)win"),
    ("match_result",    r"\bvs\.?\b"),
    ("percentage",      r"\d+(\.\d+)?%"),
    ("beat_or_exceed",  r"\bbeat\b|\bexceed\b"),
    ("other",           r".*"),  # catch-all, always last
]

_COMPILED = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in _PATTERNS]


def classify_title(title: str) -> str:
    for name, regex in _COMPILED:
        if regex.search(title):
            return name
    return "other"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_resolved_markets(max_pages: int, delay: float) -> list[dict]:
    """Paginate through closed/resolved markets. Returns raw market dicts."""
    all_markets: list[dict] = []
    offset = 0

    print(f"Fetching resolved markets (up to {max_pages} pages × {PAGE_SIZE})...")

    for page in range(max_pages):
        params = {
            "closed": "true",
            "archived": "false",
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        try:
            resp = requests.get(GAMMA_MARKETS, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            batch = resp.json()
        except Exception as e:
            print(f"  Page {page+1} failed: {e}")
            break

        if not batch:
            print(f"  No more data at page {page+1}.")
            break

        all_markets.extend(batch)
        offset += len(batch)
        print(f"  Page {page+1}: +{len(batch)} markets (total {len(all_markets)})")

        if len(batch) < PAGE_SIZE:
            break  # last page

        if delay > 0:
            time.sleep(delay)

    return all_markets


# ---------------------------------------------------------------------------
# Win-rate estimation
# ---------------------------------------------------------------------------

def parse_outcome_prices(raw) -> list[float]:
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        return [float(p) for p in prices]
    except Exception:
        return []


def estimate_win(market: dict, threshold: float) -> tuple[bool, bool]:
    """
    Returns (tradeable, won).

    tradeable = True if at least one outcome's lastTradePrice >= threshold.
    won       = True if the high-priced outcome actually resolved to 1.0.

    Uses lastTradePrice as a proxy for the price ~5 min before close.
    For resolved markets outcomePrices will be [1,0] or [0,1], so we use
    lastTradePrice to reconstruct which outcome was "near-certain" before close.
    """
    last_price = market.get("lastTradePrice")
    outcome_prices = parse_outcome_prices(market.get("outcomePrices", "[]"))

    if last_price is None or not outcome_prices:
        return False, False

    try:
        last_price = float(last_price)
    except (ValueError, TypeError):
        return False, False

    # lastTradePrice tracks the first/primary outcome price.
    # If it's >= threshold, the primary outcome was near-certain and we'd have bought it.
    # We check if the primary outcome resolved to 1.0 (won).
    if last_price >= threshold:
        resolved_price = outcome_prices[0] if outcome_prices else 0.0
        return True, resolved_price >= 0.99

    # If the complement is near-certain (primary is near 0, so secondary is near 1):
    complement = 1.0 - last_price
    if complement >= threshold:
        resolved_price = outcome_prices[1] if len(outcome_prices) > 1 else 0.0
        return True, resolved_price >= 0.99

    return False, False


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyse(markets: list[dict], threshold: float) -> dict:
    """
    Cluster markets by title pattern and compute per-cluster stats.

    Returns dict: cluster_name → {count, tradeable, won, examples, win_rate}
    """
    clusters: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "tradeable": 0,
        "won": 0,
        "examples": [],
    })

    for market in markets:
        title = market.get("question") or market.get("title", "")
        if not title:
            continue

        cluster = classify_title(title)
        c = clusters[cluster]
        c["count"] += 1

        tradeable, won = estimate_win(market, threshold)
        if tradeable:
            c["tradeable"] += 1
            if won:
                c["won"] += 1

        if len(c["examples"]) < 5:
            c["examples"].append(title[:90])

    # Compute win rate
    for name, c in clusters.items():
        c["win_rate"] = round(c["won"] / c["tradeable"], 4) if c["tradeable"] > 0 else None

    # Sort by total count descending
    return dict(sorted(clusters.items(), key=lambda x: x[1]["count"], reverse=True))


def print_results(results: dict, threshold: float, total: int) -> None:
    print(f"\n{'='*90}")
    print(f"  POLYMARKET RESOLVED MARKETS ANALYSIS  |  threshold={threshold}  |  total={total:,}")
    print(f"{'='*90}")
    print(f"  {'CLUSTER':<20} {'TOTAL':>7} {'TRADEABLE':>10} {'WON':>7} {'WIN RATE':>10}  EXAMPLES")
    print(f"  {'-'*20} {'-'*7} {'-'*10} {'-'*7} {'-'*10}  {'-'*40}")

    for name, c in results.items():
        win_rate_str = f"{c['win_rate']*100:.1f}%" if c["win_rate"] is not None else "  n/a"
        example = c["examples"][0][:55] if c["examples"] else ""
        print(
            f"  {name:<20} {c['count']:>7,} {c['tradeable']:>10,} {c['won']:>7,} "
            f"{win_rate_str:>10}  {example!r}"
        )

    print(f"{'='*90}\n")

    # Print full examples per cluster
    for name, c in results.items():
        if c["tradeable"] == 0:
            continue
        print(f"[{name}]  count={c['count']:,}  tradeable={c['tradeable']:,}  win_rate={c['win_rate']}")
        for ex in c["examples"]:
            print(f"  • {ex}")
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analyse Polymarket resolved market types")
    parser.add_argument("--threshold", type=float, default=0.95,
                        help="Min lastTradePrice to count as 'tradeable' (default 0.95)")
    parser.add_argument("--pages", type=int, default=50,
                        help="Max pages to fetch (200 markets/page, default 50 = 10,000 markets)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Seconds to wait between pages (default 0.3)")
    parser.add_argument("--output", type=str, default=None,
                        help="Optional path to write JSON results")
    args = parser.parse_args()

    markets = fetch_resolved_markets(max_pages=args.pages, delay=args.delay)

    if not markets:
        print("No markets fetched. Exiting.")
        return

    print(f"\nAnalysing {len(markets):,} resolved markets at threshold={args.threshold}...")
    results = analyse(markets, threshold=args.threshold)
    print_results(results, threshold=args.threshold, total=len(markets))

    if args.output:
        with open(args.output, "w") as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "threshold": args.threshold,
                "total_markets": len(markets),
                "clusters": results,
            }, f, indent=2)
        print(f"Results written to {args.output}")


if __name__ == "__main__":
    main()
