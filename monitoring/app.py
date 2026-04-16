"""
monitoring/app.py
Standalone Flask dashboard for yield farming monitoring.
Run separately from the main bot: python monitoring/app.py
Reads from the shared PostgreSQL database — no direct bot coupling.

Endpoints:
  GET /                      — overview dashboard HTML
  GET /analytics             — analytics page HTML
  GET /health                — health check page HTML
  GET /api/status            — bot liveness + mode
  GET /api/balance           — USDC balance + drawdown
  GET /api/risk              — per-breaker circuit breaker state
  GET /api/pnl/summary       — aggregate P&L stats
  GET /api/pnl/chart         — daily cumulative P&L for chart
  GET /api/trades            — paginated yield_trades rows
  GET /api/analytics/data    — aggregated analytics (asset breakdown, heatmap, etc.)
  GET /api/health/check      — health score with per-check breakdown
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from utility.logger import init_logging
from service import db_service
from service.risk_guard_service import get_risk_dashboard_state, get_balance_floor

init_logging(level="WARNING")

app = Flask(__name__, static_folder=os.path.dirname(os.path.abspath(__file__)))
CORS(app)

_BOT_ALIVE_THRESHOLD_SECONDS = 30


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/analytics")
def analytics_page():
    return send_from_directory(app.static_folder, "analytics.html")


@app.route("/health")
def health_page():
    return send_from_directory(app.static_folder, "health.html")


@app.route("/api/status")
def status():
    """Bot liveness, mode, last seen."""
    heartbeat = db_service.get_bot_heartbeat()
    if not heartbeat:
        return jsonify({"alive": False, "mode": None, "last_seen": None})

    last_seen_str = heartbeat.get("last_seen")
    alive = False
    if last_seen_str:
        try:
            last_seen_dt = datetime.fromisoformat(last_seen_str)
            if last_seen_dt.tzinfo is None:
                last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - last_seen_dt).total_seconds()
            alive = age < _BOT_ALIVE_THRESHOLD_SECONDS
        except Exception:
            pass

    return jsonify({
        "alive": alive,
        "mode": heartbeat.get("mode"),
        "last_seen": last_seen_str,
    })


@app.route("/api/balance")
def balance():
    """Current USDC balance, session start, and realized drawdown."""
    from datetime import timedelta
    heartbeat = db_service.get_bot_heartbeat()
    if not heartbeat:
        return jsonify({"current_balance": None, "session_start_balance": None, "drawdown_pct": None, "floor_warning": False})

    current = heartbeat.get("current_balance") or 0.0
    start = heartbeat.get("session_start_balance") or 0.0
    floor_warning = current < get_balance_floor() * 2

    # Drawdown = confirmed losses only (last 24h window) — not CLOB balance dips from in-flight positions
    window_start = datetime.now(timezone.utc) - timedelta(hours=24)
    realized_losses = db_service.get_session_realized_losses(window_start)
    drawdown_pct = (realized_losses / start * 100) if start > 0 else 0.0

    return jsonify({
        "current_balance": current,
        "session_start_balance": start,
        "drawdown_pct": round(drawdown_pct, 2),
        "realized_losses": round(realized_losses, 4),
        "floor": get_balance_floor(),
        "floor_warning": floor_warning,
    })


@app.route("/api/risk")
def risk():
    """Per-breaker circuit breaker states."""
    from datetime import timedelta
    heartbeat = db_service.get_bot_heartbeat()
    current = heartbeat.get("current_balance", 0.0) if heartbeat else 0.0
    start = heartbeat.get("session_start_balance", 0.0) if heartbeat else 0.0
    window_start = datetime.now(timezone.utc) - timedelta(hours=24)
    state = get_risk_dashboard_state(current_balance=current or 0.0, session_start_balance=start or 0.0, session_start_time=window_start)
    return jsonify(state)


@app.route("/api/pnl/summary")
def pnl_summary():
    """Aggregate P&L statistics."""
    try:
        summary = db_service.get_yield_pnl_summary()
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pnl/chart")
def pnl_chart():
    """Daily cumulative P&L data points."""
    try:
        chart = db_service.get_yield_pnl_chart()
        return jsonify(chart)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trades")
def trades():
    """Paginated yield_trades rows. Query params: status, limit, offset."""
    status_filter = request.args.get("status") or None
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "limit and offset must be integers"}), 400
    try:
        rows = db_service.get_yield_trades_page(status=status_filter, limit=limit, offset=offset)
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics/data")
def analytics_data():
    """Aggregated analytics: asset breakdown, hourly heatmap, rolling win rate, daily frequency."""
    from collections import defaultdict
    try:
        trades = db_service.get_yield_trades_for_analytics()

        asset_map = defaultdict(lambda: {"total": 0, "won": 0, "lost": 0, "error": 0, "pending": 0})
        hourly = defaultdict(int)
        daily = defaultdict(int)
        resolved_statuses = []  # 1=won, 0=lost — in chronological order

        for t in trades:
            title = (t.get("title") or "Unknown")[:60]
            status = t.get("status") or "unknown"

            asset_map[title]["total"] += 1
            if status == "won":
                asset_map[title]["won"] += 1
                resolved_statuses.append(1)
            elif status == "lost":
                asset_map[title]["lost"] += 1
                resolved_statuses.append(0)
            elif status == "error":
                asset_map[title]["error"] += 1
            else:
                asset_map[title]["pending"] += 1

            if t.get("submitted_at"):
                try:
                    dt = datetime.fromisoformat(t["submitted_at"])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    hourly[dt.hour] += 1
                    daily[dt.strftime("%Y-%m-%d")] += 1
                except Exception:
                    pass

        # Rolling win rate over a 10-trade window
        WINDOW = 10
        rolling = []
        for i in range(len(resolved_statuses)):
            start = max(0, i - WINDOW + 1)
            window = resolved_statuses[start:i + 1]
            rolling.append({"index": i + 1, "win_rate": round(sum(window) / len(window), 3)})

        total = len(trades)
        errors = sum(1 for t in trades if t.get("status") == "error")
        pending = sum(1 for t in trades if t.get("status") in ("submitted", "filled"))

        asset_breakdown = sorted(
            [{"title": k, **v} for k, v in asset_map.items()],
            key=lambda x: x["total"], reverse=True,
        )[:15]

        return jsonify({
            "asset_breakdown": asset_breakdown,
            "hourly_heatmap": [{"hour": h, "count": hourly.get(h, 0)} for h in range(24)],
            "rolling_win_rate": rolling,
            "daily_frequency": [{"date": d, "count": c} for d, c in sorted(daily.items())],
            "error_rate": round(errors / total * 100, 1) if total > 0 else 0,
            "pending_settlement": pending,
            "total_trades": total,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health/check")
def health_check_api():
    """Run all health checks and return a scored breakdown."""
    from datetime import timedelta
    checks = {}
    total_score = 0

    # 1. Bot liveness — 30 pts
    heartbeat = db_service.get_bot_heartbeat()
    alive = False
    if heartbeat and heartbeat.get("last_seen"):
        try:
            last_seen_dt = datetime.fromisoformat(heartbeat["last_seen"])
            if last_seen_dt.tzinfo is None:
                last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
            alive = (datetime.now(timezone.utc) - last_seen_dt).total_seconds() < _BOT_ALIVE_THRESHOLD_SECONDS
        except Exception:
            pass
    score_alive = 30 if alive else 0
    checks["bot_alive"] = {
        "label": "Bot Liveness",
        "score": score_alive, "max": 30, "passed": alive,
        "detail": "Bot is actively running" if alive else "No heartbeat in the last 30 seconds",
    }
    total_score += score_alive

    # 2. Balance health — 20 pts
    current = float(heartbeat.get("current_balance") or 0) if heartbeat else 0.0
    floor = get_balance_floor()
    if current >= floor * 2:
        score_balance, balance_detail = 20, f"${current:.2f} — healthy (above 2× floor)"
    elif current >= floor:
        score_balance, balance_detail = 10, f"${current:.2f} — near floor (< 2× ${floor:.2f})"
    else:
        score_balance, balance_detail = 0, f"${current:.2f} — below floor (${floor:.2f} minimum)"
    checks["balance"] = {
        "label": "Balance Health",
        "score": score_balance, "max": 20, "passed": score_balance == 20,
        "detail": balance_detail,
    }
    total_score += score_balance

    # 3. Risk circuit breakers — 20 pts (6–7 pts each)
    start = float(heartbeat.get("session_start_balance") or 0) if heartbeat else 0.0
    window_start = datetime.now(timezone.utc) - timedelta(hours=24)
    risk_state = get_risk_dashboard_state(
        current_balance=current, session_start_balance=start, session_start_time=window_start,
    )
    risk_passed = sum(1 for v in risk_state.values() if not v["triggered"])
    score_risk = round(risk_passed / 3 * 20)
    triggered_names = [k.replace("_", " ") for k, v in risk_state.items() if v["triggered"]]
    checks["risk_guards"] = {
        "label": "Risk Circuit Breakers",
        "score": score_risk, "max": 20, "passed": risk_passed == 3,
        "detail": (
            "All 3 breakers clear" if risk_passed == 3
            else f"{3 - risk_passed} triggered: {', '.join(triggered_names)}"
        ),
    }
    total_score += score_risk

    # 4. Win rate — 20 pts
    try:
        summary = db_service.get_yield_pnl_summary()
        win_rate = float(summary.get("win_rate") or 0)
        resolved = (summary.get("won") or 0) + (summary.get("lost") or 0)
        if resolved < 3:
            score_wr, wr_detail = 10, f"Insufficient data ({resolved} resolved trades — neutral score)"
        else:
            score_wr = round(win_rate * 20)
            wr_detail = f"{win_rate * 100:.1f}% win rate over {resolved} resolved trades"
        checks["win_rate"] = {
            "label": "Win Rate",
            "score": score_wr, "max": 20, "passed": score_wr >= 14,
            "detail": wr_detail,
        }
        total_score += score_wr
    except Exception as e:
        checks["win_rate"] = {"label": "Win Rate", "score": 0, "max": 20, "passed": False, "detail": f"Error: {e}"}

    # 5. Stuck trades — 10 pts
    try:
        open_trades = db_service.get_open_yield_trades()
        stuck = []
        for t in open_trades:
            sub = t.get("submitted_at")
            if sub:
                try:
                    dt = datetime.fromisoformat(sub)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - dt).total_seconds() > 1800:
                        stuck.append(t)
                except Exception:
                    pass
        score_stuck = 0 if stuck else 10
        checks["stuck_trades"] = {
            "label": "Stuck Trades",
            "score": score_stuck, "max": 10, "passed": not stuck,
            "detail": "No trades pending >30 min" if not stuck else f"{len(stuck)} trade(s) pending >30 minutes",
        }
        total_score += score_stuck
    except Exception as e:
        checks["stuck_trades"] = {"label": "Stuck Trades", "score": 0, "max": 10, "passed": False, "detail": f"Error: {e}"}

    if total_score >= 90:
        grade, grade_color = "A", "#4ade80"
    elif total_score >= 75:
        grade, grade_color = "B", "#86efac"
    elif total_score >= 60:
        grade, grade_color = "C", "#fbbf24"
    elif total_score >= 50:
        grade, grade_color = "D", "#f97316"
    else:
        grade, grade_color = "F", "#f87171"

    return jsonify({
        "score": total_score,
        "max_score": 100,
        "grade": grade,
        "grade_color": grade_color,
        "checks": checks,
    })


@app.route("/api/risk/reset", methods=["POST"])
def risk_reset():
    """
    Request a risk guard reset. Sets reset_requested=TRUE in bot_heartbeat.
    The main bot loop picks this up on the next cycle, re-fetches the CLOB balance,
    and resets session_start_balance + session_start_time.
    """
    try:
        heartbeat = db_service.get_bot_heartbeat()
        if not heartbeat:
            return jsonify({"error": "Bot has never run — no heartbeat found"}), 404
        db_service.request_risk_reset()
        return jsonify({"status": "reset_requested", "message": "Bot will reset session on next cycle (within 5s)"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health/db")
def db_health():
    """DB health check: stuck trades, error rate, pending settlement count."""
    try:
        health = db_service.get_db_health()
        return jsonify(health)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("MONITOR_PORT", 5051))
    print(f"\n  Yield Farming Monitor → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
