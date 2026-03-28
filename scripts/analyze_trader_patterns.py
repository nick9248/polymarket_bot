"""
analyze_trader_patterns.py
Fetch the last 500 trades for specified traders and analyze their strategy patterns
to determine if they're copyable (enough lead time before market resolution).
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import time
import requests
from collections import defaultdict, Counter
from datetime import datetime, timezone

from utility.logger import init_logging
from utility.constants import Category, TimePeriod, OrderBy, REQUEST_TIMEOUT_SECONDS
from core.api import polymarket_client

init_logging(level="WARNING")  # suppress noise from service layer
logger = logging.getLogger(__name__)


TRADERS = ["completion"]
TRADE_LIMIT = 2000  # paginate as far as possible


# ── Helpers ───────────────────────────────────────────────────────────────────

def lookup_wallet(user_name: str) -> str:
    """Resolve a Polymarket username to its proxy wallet address."""
    # Try ALL and MONTH across CRYPTO/OVERALL categories
    for cat in [Category.CRYPTO, Category.OVERALL]:
        for period in [TimePeriod.ALL, TimePeriod.MONTH]:
            data = polymarket_client.get_leaderboard(
                category=cat,
                time_period=period,
                order_by=OrderBy.PNL,
                limit=50,
                user_name=user_name,
            )
            for entry in data:
                if entry.get("userName", "").lower() == user_name.lower():
                    return entry["proxyWallet"]
    return ""


def fetch_market_end_dates(condition_ids: list[str]) -> dict[str, int | None]:
    """
    Batch-fetch market end dates from the Gamma API.
    Returns {condition_id: end_timestamp_seconds} (None if not found).
    """
    result = {}
    batch_size = 20

    for i in range(0, len(condition_ids), batch_size):
        batch = condition_ids[i:i + batch_size]
        ids_param = "&".join(f"condition_id={cid}" for cid in batch)
        url = f"https://gamma-api.polymarket.com/markets?{ids_param}"
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            markets = resp.json()
            for m in markets:
                cid = m.get("conditionId", "")
                end_str = m.get("endDate") or m.get("end_date_iso") or ""
                if cid and end_str:
                    try:
                        dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        result[cid] = int(dt.timestamp())
                    except Exception:
                        result[cid] = None
                elif cid:
                    result[cid] = None
        except Exception as e:
            print(f"  [warn] Gamma API batch failed: {e}")
            for cid in batch:
                result[cid] = None
        time.sleep(0.3)  # polite rate limit

    return result


def _fetch_trades_paginated(wallet: str, target: int) -> list[dict]:
    """Paginate the /trades API (max 250 per page) until target reached or exhausted."""
    page_size = 250
    all_trades = []
    offset = 0
    while len(all_trades) < target:
        params = {"user": wallet, "limit": page_size, "offset": offset}
        resp = requests.get(
            "https://data-api.polymarket.com/v1/trades",
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        all_trades.extend(page)
        print(f"    page offset={offset}: {len(page)} trades  (total so far: {len(all_trades)})")
        if len(page) < page_size:
            break
        offset += page_size
        time.sleep(0.3)
    return all_trades[:target]


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze_trades(trades: list[dict], market_end_dates: dict[str, int | None]) -> dict:
    """
    Compute all pattern metrics for a set of raw trades.
    """
    if not trades:
        return {}

    n = len(trades)
    now_ts = int(datetime.now(timezone.utc).timestamp())

    # Sort ascending by timestamp for sequential analysis
    sorted_trades = sorted(trades, key=lambda t: int(t.get("timestamp", 0)))

    prices = [float(t.get("price", 0)) for t in trades]
    sizes = [float(t.get("size", 0)) for t in trades]
    usd_values = [p * s for p, s in zip(prices, sizes)]
    sides = [t.get("side", "").upper() for t in trades]

    # Time gaps between consecutive trades (seconds)
    timestamps = [int(t.get("timestamp", 0)) for t in sorted_trades]
    gaps = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps) - 1)]
    gaps_minutes = [g / 60 for g in gaps if g > 0]

    # Hour of day (UTC)
    hours = [datetime.fromtimestamp(ts, tz=timezone.utc).hour for ts in timestamps]

    # BUY/SELL breakdown
    buy_count = sides.count("BUY")
    sell_count = sides.count("SELL")

    # Market concentration
    condition_ids = [t.get("conditionId", "") for t in trades]
    market_counts = Counter(condition_ids)
    unique_markets = len(market_counts)
    top_market_trades = market_counts.most_common(1)[0][1] if market_counts else 0

    # Trade titles for the most concentrated market
    top_cid = market_counts.most_common(1)[0][0] if market_counts else ""
    top_market_title = next(
        (t.get("title", "") for t in trades if t.get("conditionId") == top_cid), ""
    )

    # Price bucket distribution
    price_buckets = {"<0.10": 0, "0.10-0.30": 0, "0.30-0.50": 0, "0.50-0.70": 0, "0.70-0.90": 0, ">0.90": 0}
    for p in prices:
        if p < 0.10:
            price_buckets["<0.10"] += 1
        elif p < 0.30:
            price_buckets["0.10-0.30"] += 1
        elif p < 0.50:
            price_buckets["0.30-0.50"] += 1
        elif p < 0.70:
            price_buckets["0.50-0.70"] += 1
        elif p < 0.90:
            price_buckets["0.70-0.90"] += 1
        else:
            price_buckets[">0.90"] += 1

    # Holding period: per conditionId, find BUY→SELL pairs
    positions_by_market = defaultdict(list)
    for t in sorted_trades:
        positions_by_market[t.get("conditionId", "")].append(t)

    holding_periods = []  # in hours
    for cid, mkt_trades in positions_by_market.items():
        buys = [t for t in mkt_trades if t.get("side", "").upper() == "BUY"]
        sells = [t for t in mkt_trades if t.get("side", "").upper() == "SELL"]
        if buys and sells:
            first_buy_ts = min(int(t["timestamp"]) for t in buys)
            last_sell_ts = max(int(t["timestamp"]) for t in sells)
            if last_sell_ts > first_buy_ts:
                holding_periods.append((last_sell_ts - first_buy_ts) / 3600)

    # Time-to-resolution (using market end dates)
    ttrs = []  # in hours
    unresolved_within_1h = 0
    unresolved_within_24h = 0
    trade_ttr_details = []

    for t in trades:
        cid = t.get("conditionId", "")
        trade_ts = int(t.get("timestamp", 0))
        end_ts = market_end_dates.get(cid)

        if end_ts is not None:
            diff_hours = (end_ts - trade_ts) / 3600
            ttrs.append(diff_hours)
            trade_ttr_details.append({
                "title": t.get("title", "")[:60],
                "ttr_hours": diff_hours,
                "side": t.get("side", ""),
                "price": float(t.get("price", 0)),
            })
            if diff_hours < 1:
                unresolved_within_1h += 1
            if diff_hours < 24:
                unresolved_within_24h += 1

    ttrs_covered = len(ttrs)

    # Date range of fetched trades
    oldest_ts = timestamps[0] if timestamps else 0
    newest_ts = timestamps[-1] if timestamps else 0
    oldest_dt = datetime.fromtimestamp(oldest_ts, tz=timezone.utc)
    newest_dt = datetime.fromtimestamp(newest_ts, tz=timezone.utc)
    date_range_days = (newest_ts - oldest_ts) / 86400 if oldest_ts else 0

    return {
        "n": n,
        "date_range_days": date_range_days,
        "oldest_dt": oldest_dt,
        "newest_dt": newest_dt,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "prices": prices,
        "usd_values": usd_values,
        "price_buckets": price_buckets,
        "gaps_minutes": gaps_minutes,
        "hours": hours,
        "unique_markets": unique_markets,
        "top_market_trades": top_market_trades,
        "top_market_title": top_market_title,
        "holding_periods": holding_periods,
        "ttrs": ttrs,
        "ttrs_covered": ttrs_covered,
        "pct_last_1h": (unresolved_within_1h / ttrs_covered * 100) if ttrs_covered else 0,
        "pct_last_24h": (unresolved_within_24h / ttrs_covered * 100) if ttrs_covered else 0,
        "trade_ttr_details": sorted(trade_ttr_details, key=lambda x: x["ttr_hours"]),
    }


def _pct(bucket: dict, total: int) -> str:
    return ", ".join(f"{k}: {v/total*100:.0f}%" for k, v in bucket.items() if v > 0)


def _hist(values: list[float], buckets: list[tuple[float, str]]) -> str:
    if not values:
        return "n/a"
    counts = defaultdict(int)
    for v in values:
        for threshold, label in buckets:
            if v <= threshold:
                counts[label] += 1
                break
    total = len(values)
    return "  |  ".join(f"{label}: {counts[label]/total*100:.0f}%" for _, label in buckets if counts[label])


def print_report(trader_name: str, wallet: str, stats: dict):
    sep = "=" * 90
    sub = "-" * 90
    print(f"\n{sep}")
    print(f"  TRADER: {trader_name}  ({wallet})")
    print(sep)

    n = stats["n"]
    print(f"\n  Trades fetched : {n}")
    print(f"  Date range     : {stats['oldest_dt'].strftime('%Y-%m-%d')} -> {stats['newest_dt'].strftime('%Y-%m-%d')}  ({stats['date_range_days']:.0f} days)")

    avg_tpd = n / stats["date_range_days"] if stats["date_range_days"] else 0
    print(f"  Avg tpd        : {avg_tpd:.1f}")

    print(f"\n{sub}")
    print(f"  BUY/SELL SPLIT")
    print(f"{sub}")
    print(f"  BUY  : {stats['buy_count']} ({stats['buy_count']/n*100:.0f}%)")
    print(f"  SELL : {stats['sell_count']} ({stats['sell_count']/n*100:.0f}%)")

    print(f"\n{sub}")
    print(f"  PRICE DISTRIBUTION  (entry price = implied probability at entry)")
    print(f"{sub}")
    print(f"  {_pct(stats['price_buckets'], n)}")

    prices = stats["prices"]
    if prices:
        print(f"  Median : {sorted(prices)[len(prices)//2]:.3f}   Min: {min(prices):.3f}   Max: {max(prices):.3f}")

    print(f"\n{sub}")
    print(f"  USD PER TRADE  (size × price)")
    print(f"{sub}")
    usd = sorted(stats["usd_values"])
    if usd:
        usd_buckets = [(10, "<=$10"), (50, "<=$50"), (100, "<=$100"), (500, "<=$500"), (1000, "<=$1k"), (float("inf"), ">$1k")]
        print(f"  {_hist(usd, usd_buckets)}")
        print(f"  Median: ${sorted(usd)[len(usd)//2]:,.0f}   Max: ${max(usd):,.0f}   Total: ${sum(usd):,.0f}")

    print(f"\n{sub}")
    print(f"  MARKET CONCENTRATION")
    print(f"{sub}")
    print(f"  Unique markets : {stats['unique_markets']}")
    print(f"  Top market     : {stats['top_market_trades']} trades  — \"{stats['top_market_title']}\"")

    print(f"\n{sub}")
    print(f"  TRADE FREQUENCY  (gap between consecutive trades)")
    print(f"{sub}")
    gaps = stats["gaps_minutes"]
    if gaps:
        gap_buckets = [(1, "<=1min"), (5, "<=5min"), (30, "<=30min"), (60, "<=1h"), (1440, "<=1day"), (float("inf"), ">1day")]
        print(f"  {_hist(gaps, gap_buckets)}")
        sorted_gaps = sorted(gaps)
        print(f"  Median gap: {sorted_gaps[len(sorted_gaps)//2]:.1f}min   Min: {min(gaps):.1f}min   Max: {max(gaps)/60:.1f}h")

    print(f"\n{sub}")
    print(f"  ACTIVE HOURS (UTC)")
    print(f"{sub}")
    hour_counts = Counter(stats["hours"])
    top_hours = hour_counts.most_common(6)
    print(f"  Most active: {', '.join(f'{h:02d}h({c})' for h, c in sorted(top_hours))}")

    print(f"\n{sub}")
    print(f"  HOLDING PERIOD  (first BUY to last SELL per market)")
    print(f"{sub}")
    hp = stats["holding_periods"]
    if hp:
        hp_buckets = [(1, "<=1h"), (6, "<=6h"), (24, "<=1day"), (168, "<=1week"), (float("inf"), ">1week")]
        print(f"  {_hist(hp, hp_buckets)}")
        shp = sorted(hp)
        print(f"  Median: {shp[len(shp)//2]:.1f}h   Min: {min(hp):.1f}h   Max: {max(hp):.0f}h")
    else:
        print(f"  No BUY->SELL round-trips found in this window")

    print(f"\n{sub}")
    print(f"  TIME-TO-RESOLUTION  (hours before market closes at time of trade)")
    print(f"{sub}")
    ttrs = stats["ttrs"]
    if ttrs:
        print(f"  Trades with resolution data : {stats['ttrs_covered']}/{n}")
        ttr_buckets = [
            (1, "<=1h"), (6, "<=6h"), (24, "<=1day"), (72, "<=3days"),
            (168, "<=1week"), (720, "<=1month"), (float("inf"), ">1month")
        ]
        print(f"  {_hist(ttrs, ttr_buckets)}")
        sttrs = sorted(ttrs)
        print(f"  Median TTR : {sttrs[len(sttrs)//2]:.1f}h")
        print(f"  Trades in last 1h before close  : {stats['pct_last_1h']:.0f}%  (stingo43 was ~100%)")
        print(f"  Trades in last 24h before close : {stats['pct_last_24h']:.0f}%")

        if stats["trade_ttr_details"]:
            print(f"\n  EARLIEST trades (lowest TTR — hardest to copy):")
            for td in stats["trade_ttr_details"][:5]:
                print(f"    {td['ttr_hours']:7.1f}h  {td['side']:<4}  @{td['price']:.3f}  {td['title']}")
            print(f"\n  LATEST trades (highest TTR — easiest to copy):")
            for td in stats["trade_ttr_details"][-5:]:
                print(f"    {td['ttr_hours']:7.1f}h  {td['side']:<4}  @{td['price']:.3f}  {td['title']}")
    else:
        print(f"  No resolution data available")

    print(f"\n{sep}")
    print(f"  COPYABILITY VERDICT")
    print(sep)

    ttrs_pct_last1h = stats["pct_last_1h"]
    avg_usd = sum(stats["usd_values"]) / len(stats["usd_values"]) if stats["usd_values"] else 0
    median_usd = sorted(stats["usd_values"])[len(stats["usd_values"])//2] if stats["usd_values"] else 0

    if ttrs_pct_last1h > 50:
        verdict = "POOR  — majority of trades happen within 1h of close (API lag = too late)"
    elif ttrs_pct_last1h > 20:
        verdict = "RISKY — significant last-minute trading, copy only where TTR > 6h"
    else:
        verdict = "GOOD  — most trades have sufficient lead time to copy"

    print(f"  Last-1h trades : {ttrs_pct_last1h:.0f}%")
    print(f"  Verdict        : {verdict}")
    print(f"  Avg trade size : ${avg_usd:,.0f}  (median: ${median_usd:,.0f})")
    print(f"  Unique markets : {stats['unique_markets']} — {'concentrated' if stats['unique_markets'] < 10 else 'diversified'}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    for trader_name in TRADERS:
        print(f"\nLooking up wallet for {trader_name}...")
        wallet = lookup_wallet(trader_name)
        if not wallet:
            print(f"  ERROR: Could not find wallet for {trader_name}")
            continue
        print(f"  Found: {wallet}")

        print(f"  Fetching up to {TRADE_LIMIT} trades (paginated)...")
        raw_trades = _fetch_trades_paginated(wallet, TRADE_LIMIT)
        print(f"  Got {len(raw_trades)} trades")

        # Deduplicate condition IDs for gamma lookups
        condition_ids = list({t.get("conditionId", "") for t in raw_trades if t.get("conditionId")})
        print(f"  Resolving end dates for {len(condition_ids)} unique markets via Gamma API...")
        market_end_dates = fetch_market_end_dates(condition_ids)
        resolved = sum(1 for v in market_end_dates.values() if v is not None)
        print(f"  Resolved {resolved}/{len(condition_ids)} market end dates")

        # Also pull end dates from open positions (these have endDate directly)
        print(f"  Fetching open positions (for live end dates)...")
        open_pos = polymarket_client.get_user_positions(wallet)
        print(f"  Got {len(open_pos)} open positions")
        for p in open_pos:
            cid = p.get("conditionId", "")
            end_str = p.get("endDate", "")
            if cid and end_str and cid not in market_end_dates:
                try:
                    dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    market_end_dates[cid] = int(dt.timestamp())
                except Exception:
                    pass

        print(f"  Fetching closed positions...")
        closed = polymarket_client.get_user_closed_positions(wallet, max_results=500)
        print(f"  Got {len(closed)} closed positions")

        print(f"  Fetching activity (500 events)...")
        activity = polymarket_client.get_user_activity(wallet, limit=500)
        print(f"  Got {len(activity)} activity events")

        sep = "=" * 90
        sub = "-" * 90

        stats = analyze_trades(raw_trades, market_end_dates)
        print_report(trader_name, wallet, stats)

        # ── Closed positions deep-dive ─────────────────────────────────────────
        print(f"\n{sep}")
        print(f"  CLOSED POSITIONS DEEP-DIVE")
        print(sep)
        _print_closed_positions(closed)

        # ── Open positions ─────────────────────────────────────────────────────
        print(f"\n{sep}")
        print(f"  OPEN POSITIONS (what they hold RIGHT NOW)")
        print(sep)
        _print_open_positions(open_pos)

        # ── Recent activity ────────────────────────────────────────────────────
        print(f"\n{sep}")
        print(f"  RECENT ACTIVITY")
        print(sep)
        _print_activity(activity)


def _print_closed_positions(closed: list[dict]) -> None:
    if not closed:
        print("  No closed positions found.")
        return

    pnls = [float(p.get("realizedPnl", 0)) for p in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0

    invested = [float(p.get("totalBought", 0)) * float(p.get("avgPrice", 0)) for p in closed]
    rois = [pnl / inv * 100 for pnl, inv in zip(pnls, invested) if inv > 0]
    sorted_rois = sorted(rois)
    median_roi = sorted_rois[len(sorted_rois) // 2] if sorted_rois else 0

    print(f"  Closed positions  : {len(closed)}")
    print(f"  Win rate          : {win_rate:.1f}%  ({len(wins)} wins / {len(losses)} losses)")
    print(f"  Total realized PnL: ${sum(pnls):,.0f}")
    print(f"  Avg win           : ${sum(wins)/len(wins):,.0f}" if wins else "  Avg win: n/a")
    print(f"  Avg loss          : ${sum(losses)/len(losses):,.0f}" if losses else "  Avg loss: n/a")
    print(f"  Median ROI        : {median_roi:.1f}%")

    print(f"\n  TOP 10 WINNERS:")
    top_wins = sorted(closed, key=lambda p: float(p.get("realizedPnl", 0)), reverse=True)[:10]
    for p in top_wins:
        pnl = float(p.get("realizedPnl", 0))
        inv = float(p.get("totalBought", 0)) * float(p.get("avgPrice", 0))
        roi = pnl / inv * 100 if inv > 0 else 0
        ts = int(p.get("timestamp", 0))
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "?"
        title = p.get("title", "")[:55]
        outcome = p.get("outcome", "")
        print(f"    +${pnl:7,.0f}  ROI:{roi:6.0f}%  [{dt}]  {outcome:<4}  {title}")

    print(f"\n  TOP 5 LOSERS:")
    top_losses = sorted(closed, key=lambda p: float(p.get("realizedPnl", 0)))[:5]
    for p in top_losses:
        pnl = float(p.get("realizedPnl", 0))
        inv = float(p.get("totalBought", 0)) * float(p.get("avgPrice", 0))
        roi = pnl / inv * 100 if inv > 0 else 0
        ts = int(p.get("timestamp", 0))
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "?"
        title = p.get("title", "")[:55]
        outcome = p.get("outcome", "")
        print(f"    -${abs(pnl):7,.0f}  ROI:{roi:6.0f}%  [{dt}]  {outcome:<4}  {title}")


def _print_open_positions(open_pos: list[dict]) -> None:
    now_ts = int(datetime.now(timezone.utc).timestamp())

    if not open_pos:
        print("  No open positions.")
        return

    total_invested = sum(float(p.get("initialValue", 0)) for p in open_pos)
    total_current = sum(float(p.get("currentValue", 0)) for p in open_pos)
    unrealized = total_current - total_invested
    print(f"  Open positions    : {len(open_pos)}")
    print(f"  Total invested    : ${total_invested:,.0f}")
    print(f"  Current value     : ${total_current:,.0f}  (unrealized: {'+'if unrealized>=0 else ''}{unrealized:,.0f})")

    print(f"\n  ALL OPEN POSITIONS (sorted by current value):")
    sorted_pos = sorted(open_pos, key=lambda p: float(p.get("currentValue", 0)), reverse=True)
    for p in sorted_pos:
        title = p.get("title", "")[:50]
        outcome = p.get("outcome", "")
        cur_price = float(p.get("curPrice", 0))
        avg_price = float(p.get("avgPrice", 0))
        cur_val = float(p.get("currentValue", 0))
        cash_pnl = float(p.get("cashPnl", 0))
        end_str = p.get("endDate", "")
        ttr_str = ""
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                ttr_h = (end_dt.timestamp() - now_ts) / 3600
                if ttr_h > 0:
                    ttr_str = f"  TTR:{ttr_h:.0f}h"
                else:
                    ttr_str = "  CLOSING SOON"
            except Exception:
                pass
        pnl_str = f"{'+'if cash_pnl>=0 else ''}{cash_pnl:,.0f}"
        print(f"    ${cur_val:7,.0f}  @cur:{cur_price:.3f} avg:{avg_price:.3f}  PnL:{pnl_str:<10}{ttr_str}  [{outcome}]  {title}")


def _print_activity(activity: list[dict]) -> None:
    if not activity:
        print("  No activity found.")
        return

    trades = [a for a in activity if a.get("type") == "TRADE"]
    redeems = [a for a in activity if a.get("type") == "REDEEM"]
    merges = [a for a in activity if a.get("type") == "MERGE"]
    splits = [a for a in activity if a.get("type") == "SPLIT"]
    total_redeemed = sum(float(r.get("usdcSize", 0)) for r in redeems)

    print(f"  Events: {len(activity)}  (trades:{len(trades)}  redeems:{len(redeems)}  merges:{len(merges)}  splits:{len(splits)})")
    if redeems:
        print(f"  Total redeemed    : ${total_redeemed:,.0f}")

    sorted_act = sorted(activity, key=lambda a: int(a.get("timestamp", 0)), reverse=True)
    print(f"\n  MOST RECENT 15 EVENTS:")
    for a in sorted_act[:15]:
        ts = int(a.get("timestamp", 0))
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        atype = a.get("type", "")
        title = a.get("title", "")[:48]
        size_usd = float(a.get("usdcSize", 0))
        side = a.get("side", "")
        price = float(a.get("price", 0)) if a.get("price") else 0
        detail = f"{side} @{price:.3f}" if side else ""
        print(f"    [{dt}]  {atype:<8}  ${size_usd:7,.0f}  {detail:<16}  {title}")


if __name__ == "__main__":
    main()
