"""
DASHBOARD — Flask monitoring server (port 8080)
Akses: http://IP_VPS:8080
Integrasi: Journal (journal.db) + Paper Trader (trades.db)
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
from paper_trader import (
    get_paper_stats, get_open_positions, get_closed_positions,
    get_performance_by_symbol as get_paper_performance,
    close_position_manual,
    init_db as paper_init_db
)

app = Flask(__name__, template_folder="templates")
init_db()
paper_init_db()

# ── Background price checker (Journal signals) ────────────────────────────────
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

# ── Journal API Routes ────────────────────────────────────────────────────────

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

# ── Paper Trader API Routes ───────────────────────────────────────────────────

@app.route("/api/paper/stats")
def api_paper_stats():
    return jsonify(get_paper_stats())

@app.route("/api/paper/open")
def api_paper_open():
    return jsonify(get_open_positions())

@app.route("/api/paper/closed")
def api_paper_closed():
    limit = int(request.args.get("limit", 100))
    return jsonify(get_closed_positions(limit))

@app.route("/api/paper/performance")
def api_paper_performance():
    return jsonify(get_paper_performance())

@app.route("/api/paper/equity")
def api_paper_equity():
    from paper_trader import _conn
    with _conn() as c:
        rows = c.execute("""
            SELECT closed_at, symbol, direction, pnl_usd, pnl_pct, status
            FROM positions
            WHERE status != 'OPEN' AND pnl_usd IS NOT NULL
            ORDER BY id ASC LIMIT 200
        """).fetchall()
    cumulative = 0.0
    result = []
    for ts, sym, direction, pnl_usd, pnl_pct, status in rows:
        cumulative = round(cumulative + (pnl_usd or 0), 2)
        result.append({
            "ts": ts, "symbol": sym, "direction": direction,
            "pnl_usd": round(pnl_usd or 0, 2),
            "pnl_pct": round(pnl_pct or 0, 2),
            "cumulative": cumulative,
            "win": status.startswith("WIN")
        })
    return jsonify(result)

@app.route("/api/paper/prices")
def api_paper_prices():
    opens = get_open_positions()
    prices = {}
    for pos in opens:
        price = _fetch_price(pos["symbol"])
        if price:
            entry = float(pos["entry_price"] or 0)
            qty   = float(pos["quantity"] or 0)
            if entry and qty:
                if pos["direction"] == "LONG":
                    unreal_pct = round((price - entry) / entry * 100, 2)
                    unreal_usd = round((price - entry) * qty, 2)
                else:
                    unreal_pct = round((entry - price) / entry * 100, 2)
                    unreal_usd = round((entry - price) * qty, 2)
                prices[pos["symbol"]] = {
                    "price": price,
                    "pnl_pct": unreal_pct,
                    "pnl_usd": unreal_usd,
                    "id": pos["id"]
                }
    return jsonify(prices)

@app.route("/api/paper/close", methods=["POST"])
def api_paper_close():
    data = request.get_json()
    pos_id = data.get("id")
    exit_price = data.get("exit_price")
    if not pos_id or not exit_price:
        return jsonify({"error": "id dan exit_price wajib"}), 400
    result = close_position_manual(int(pos_id), float(exit_price))
    if result is False:
        return jsonify({"error": "Posisi tidak ditemukan atau sudah tertutup"}), 404
    return jsonify({"ok": True, **result})

if __name__ == "__main__":
    port = int(os.environ.get("DASH_PORT", 8080))
    print(f"Dashboard → http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
