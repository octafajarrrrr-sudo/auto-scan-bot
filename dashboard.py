"""
DASHBOARD — Flask monitoring server (port 8080)
Akses: http://IP_VPS:8080
"""
import os, sys, json, time, threading
import requests as _req
from flask import Flask, render_template, jsonify, request

sys.path.insert(0, os.path.dirname(__file__))

from journal import (
    get_full_stats, get_recent_signals, get_running_signals,
    get_scan_history, get_scan_detail, get_equity_curve,
    get_performance_by_symbol, update_signal_status, init_db
)

app = Flask(__name__, template_folder="templates")
init_db()

# ── Background price checker ──────────────────────────────────────────────────
_FAPI_PRICE = "https://fapi.binance.com/fapi/v1/ticker/price"

def _fetch_price(symbol: str) -> float | None:
    try:
        r = _req.get(_FAPI_PRICE, params={"symbol": f"{symbol}USDT"}, timeout=4)
        if r.status_code == 200:
            return float(r.json().get("price", 0) or 0)
    except Exception:
        pass
    return None

def _check_running_signals():
    while True:
        try:
            for sig in get_running_signals():
                price = _fetch_price(sig["symbol"])
                if price is None:
                    continue
                is_long = sig["bias"].upper() == "LONG"
                hit = None
                if is_long:
                    if price <= sig["sl"]:          hit = ("LOSS",    sig["sl"])
                    elif price >= sig["tp2"]:       hit = ("WIN_TP2", sig["tp2"])
                    elif price >= sig["tp1"]:       hit = ("WIN_TP1", sig["tp1"])
                else:
                    if price >= sig["sl"]:          hit = ("LOSS",    sig["sl"])
                    elif price <= sig["tp2"]:       hit = ("WIN_TP2", sig["tp2"])
                    elif price <= sig["tp1"]:       hit = ("WIN_TP1", sig["tp1"])
                if hit:
                    update_signal_status(sig["id"], hit[0], exit_price=hit[1],
                                         notes="Auto-hit")
        except Exception:
            pass
        time.sleep(60)

threading.Thread(target=_check_running_signals, daemon=True).start()

# ── API Routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/stats")
def api_stats():
    return jsonify(get_full_stats())

@app.route("/api/signals")
def api_signals():
    limit = int(request.args.get("limit", 200))
    return jsonify(get_recent_signals(limit))

@app.route("/api/running")
def api_running():
    return jsonify(get_running_signals())

@app.route("/api/scans")
def api_scans():
    return jsonify(get_scan_detail(10))

@app.route("/api/equity")
def api_equity():
    return jsonify(get_equity_curve(200))

@app.route("/api/performance")
def api_performance():
    return jsonify(get_performance_by_symbol())

@app.route("/api/prices")
def api_prices():
    running = get_running_signals()
    prices  = {}
    for sig in running:
        price = _fetch_price(sig["symbol"])
        if price:
            is_long = sig["bias"].upper() == "LONG"
            entry   = float(sig["entry"] or 0)
            pnl     = round((price-entry)/entry*100, 2) if is_long and entry \
                      else round((entry-price)/entry*100, 2) if entry else 0
            prices[sig["symbol"]] = {"price": price, "pnl_pct": pnl, "id": sig["id"]}
    return jsonify(prices)

@app.route("/api/update", methods=["POST"])
def api_update():
    data = request.get_json()
    sid  = data.get("id")
    status = data.get("status")
    if not sid or not status:
        return jsonify({"error": "id dan status wajib"}), 400
    update_signal_status(sid, status, data.get("exit_price"), data.get("notes",""))
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("DASH_PORT", 8080))
    print(f"Dashboard → http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
