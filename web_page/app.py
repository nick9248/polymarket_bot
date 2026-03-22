"""
app.py
Flask web server for the Polymarket Analyzer dashboard.
Run from the project root:  python web_page/app.py
"""
import sys
import os

# Add project root to path so we can import existing modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from utility.logger import init_logging
from utility.constants import Category, TimePeriod, OrderBy
from service import leaderboard_service, trades_service
from core.api import polymarket_client
from analysis.analyzer import Analyzer
from analysis.strategy import StrategyAnalyzer

init_logging(level="WARNING")  # keep server logs quiet

app = Flask(__name__, static_folder=os.path.dirname(os.path.abspath(__file__)))
CORS(app)

# ── Static serving ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ── Meta endpoint ─────────────────────────────────────────────────────────────

@app.route("/api/meta")
def meta():
    """Return available filter options for the UI."""
    return jsonify({
        "categories": [c.value for c in Category],
        "periods":    [p.value for p in TimePeriod],
        "order_by":   [o.value for o in OrderBy],
    })


# ── Main analysis endpoint ────────────────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
def run_analysis():
    """
    Body (JSON):
      {
        "categories": ["CRYPTO", "POLITICS"],   // at least one
        "periods":    ["ALL", "MONTH"],          // at least one
        "limit":      10,                        // 1–50
        "order_by":   "PNL"                      // PNL | VOL
      }
    """
    body = request.get_json(silent=True) or {}

    raw_cats  = body.get("categories", ["CRYPTO"])
    raw_pds   = body.get("periods",    ["ALL"])
    limit     = int(body.get("limit",  10))
    raw_ob    = body.get("order_by",   "PNL")

    # Validate & convert
    try:
        cats     = [Category(c)   for c in raw_cats]
        periods  = [TimePeriod(p) for p in raw_pds]
        order_by = OrderBy(raw_ob)
        if not (1 <= limit <= 50):
            return jsonify({"error": "limit must be 1–50"}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # ── Fetch unique traders across all combos ────────────────────────────────
    unique_traders: dict = {}
    for cat in cats:
        for period in periods:
            try:
                entries = leaderboard_service.fetch_leaderboard(
                    category=cat, time_period=period,
                    order_by=order_by, limit=limit
                )
            except Exception as exc:
                return jsonify({"error": f"API error: {exc}"}), 502

            for e in entries:
                if e.proxy_wallet not in unique_traders:
                    e.lists = [period.value]
                    unique_traders[e.proxy_wallet] = e
                else:
                    if period.value not in unique_traders[e.proxy_wallet].lists:
                        unique_traders[e.proxy_wallet].lists.append(period.value)

    wallets = list(unique_traders.values())
    if not wallets:
        return jsonify({"overlapping": [], "ranking": []})

    # ── Overlapping super-traders ─────────────────────────────────────────────
    overlapping = Analyzer.get_overlapping_traders(wallets)
    overlapping_out = [
        {
            "user_name":      t.user_name,
            "proxy_wallet":   t.proxy_wallet,
            "x_username":     t.x_username,
            "profile_image":  t.profile_image,
            "verified_badge": t.verified_badge,
            "lists":          t.lists,
            "pnl":            t.pnl,
            "vol":            t.vol,
        }
        for t in overlapping
    ]

    # ── Efficiency ranking + bot detection + quality metrics ──────────────────
    ranked = Analyzer.get_efficiency_ranking(wallets)
    for res in ranked:
        trader = res["trader"]
        wallet = trader.proxy_wallet
        try:
            trades    = trades_service.fetch_user_trades(wallet, limit=500)
            bot_check = Analyzer.detect_hft_patterns(trades)
            positions = StrategyAnalyzer.extract_positions(trades)
            profile   = StrategyAnalyzer.determine_profile(bot_check, positions)
        except Exception:
            bot_check = {"is_bot_likely": False, "reasons": [], "frequency_stats": {"trades_per_day": 0}}
            profile   = {"classification": "Unknown", "description": "", "tpd": 0, "is_bot": False}

        try:
            closed   = polymarket_client.get_user_closed_positions(wallet, max_results=500)
            open_pos = polymarket_client.get_user_positions(wallet)
            activity = polymarket_client.get_user_activity(wallet, limit=500)
            quality      = Analyzer.analyze_closed_positions(closed)
            open_stats   = Analyzer.analyze_open_positions(open_pos)
            activity_stats = Analyzer.analyze_activity(activity)
        except Exception:
            quality        = {"closed_position_count": 0, "confidence_tier": "unknown",
                              "realized_win_rate": 0, "total_realized_pnl": 0,
                              "total_invested_closed": 0, "realized_rov": 0,
                              "avg_roi_per_position": 0, "median_roi_per_position": 0}
            open_stats     = {"open_position_count": 0, "total_open_exposure": 0,
                              "total_unrealized_pnl": 0, "redeemable_count": 0, "mergeable_count": 0}
            activity_stats = {"redeem_count": 0, "total_redeemed_usdc": 0,
                              "merge_count": 0, "split_count": 0, "arb_signal": False}

        res["bot_check"]      = bot_check
        res["profile"]        = profile
        res["quality"]        = quality
        res["open_stats"]     = open_stats
        res["activity_stats"] = activity_stats

    ranking_out = []
    for i, res in enumerate(ranked, 1):
        t   = res["trader"]
        bc  = res["bot_check"]
        pf  = res["profile"]
        q   = res["quality"]
        os_ = res["open_stats"]
        act = res["activity_stats"]
        tpd = bc.get("frequency_stats", {}).get("trades_per_day", 0.0)
        ranking_out.append({
            "rank":                    i,
            "user_name":               t.user_name,
            "proxy_wallet":            t.proxy_wallet,
            "x_username":              t.x_username,
            "profile_image":           t.profile_image,
            "verified_badge":          t.verified_badge,
            "lists":                   getattr(t, "lists", []),
            "pnl":                     t.pnl,
            "vol":                     t.vol,
            "rov_percentage":          res["rov_percentage"],
            "is_bot":                  bc.get("is_bot_likely", False),
            "trades_per_day":          round(tpd, 1),
            "classification":          pf.get("classification", ""),
            "description":             pf.get("description", ""),
            "bot_reasons":             bc.get("reasons", []),
            # Quality metrics
            "closed_position_count":   q["closed_position_count"],
            "confidence_tier":         q["confidence_tier"],
            "realized_win_rate":       round(q["realized_win_rate"] * 100, 1),
            "total_realized_pnl":      round(q["total_realized_pnl"], 2),
            "realized_rov":            round(q["realized_rov"], 2),
            "avg_roi_per_position":    round(q["avg_roi_per_position"], 1),
            "median_roi_per_position": round(q["median_roi_per_position"], 1),
            # Exposure
            "open_position_count":     os_["open_position_count"],
            "total_open_exposure":     round(os_["total_open_exposure"], 2),
            "total_unrealized_pnl":    round(os_["total_unrealized_pnl"], 2),
            # Activity
            "redeem_count":            act["redeem_count"],
            "total_redeemed_usdc":     round(act["total_redeemed_usdc"], 2),
            "arb_signal":              act["arb_signal"],
            "merge_count":             act["merge_count"],
        })

    return jsonify({"overlapping": overlapping_out, "ranking": ranking_out})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\n  Polymarket Dashboard -> http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
