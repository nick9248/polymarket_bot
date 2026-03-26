"""
monitoring/app.py
Standalone Flask dashboard for yield farming monitoring.
Run separately from the main bot: python monitoring/app.py
Reads from the shared PostgreSQL database — no direct bot coupling.

Endpoints:
  GET /                    — dashboard HTML
  GET /api/status          — bot liveness + mode
  GET /api/balance         — USDC balance + drawdown
  GET /api/risk            — per-breaker circuit breaker state
  GET /api/pnl/summary     — aggregate P&L stats
  GET /api/pnl/chart       — daily cumulative P&L for chart
  GET /api/trades          — paginated yield_trades rows
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
    """Current USDC balance, session start, and drawdown."""
    heartbeat = db_service.get_bot_heartbeat()
    if not heartbeat:
        return jsonify({"current_balance": None, "session_start_balance": None, "drawdown_pct": None, "floor_warning": False})

    current = heartbeat.get("current_balance") or 0.0
    start = heartbeat.get("session_start_balance") or 0.0
    drawdown_pct = ((start - current) / start * 100) if start > 0 else 0.0
    floor_warning = current < get_balance_floor() * 2

    return jsonify({
        "current_balance": current,
        "session_start_balance": start,
        "drawdown_pct": round(drawdown_pct, 2),
        "floor": get_balance_floor(),
        "floor_warning": floor_warning,
    })


@app.route("/api/risk")
def risk():
    """Per-breaker circuit breaker states."""
    heartbeat = db_service.get_bot_heartbeat()
    current = heartbeat.get("current_balance", 0.0) if heartbeat else 0.0
    start = heartbeat.get("session_start_balance", 0.0) if heartbeat else 0.0
    state = get_risk_dashboard_state(current_balance=current or 0.0, session_start_balance=start or 0.0)
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


if __name__ == "__main__":
    port = int(os.environ.get("MONITOR_PORT", 5051))
    print(f"\n  Yield Farming Monitor → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
