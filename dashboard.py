"""
DASHBOARD — Flask monitoring server
Akses: http://localhost:8080
Jalankan terpisah: python dashboard.py
"""

import os
import sys
import json
import time
import threading
import requests as _req
from flask import Flask, render_template, jsonify, request

# Pastikan direktori proyek ada di path
sys.path.insert(0, os.path.dirname(__file__))

from journal import (
    get_stats, get_recent_signals, get_running_signals,
    get_scan_history, update_signal_status, init_db
)

app = Flask(__name__, template_folder="templates")
init_db()

# ── Background price checker ──────────────────────────────────────────────────
_BINANCE_TICKER = "https://fapi.binance.com/fapi/v1/ticker/price"

def _fetch_price(symbol: str) -> float | None:
    """Ambil harga futures terbaru dari Binance public endpoint."""
    try:
        r = _req.get(_BINANCE_TICKER, params={"symbol": f"{symbol}USDT"}, timeout=4)
        if r.status_code == 200:
            return float(r.json().get("price", 0))
    except Exception:
        pass
    return None

def _check_running_signals():
    """
    Background thread: cek running signals tiap 60 detik.
    Jika harga menyentuh SL/TP1/TP2 → update status otomatis.
    """
    from journal import get_running_signals, update_signal_status
    while True:
        try:
            running = get_running_signals()
            for sig in running:
                price = _fetch_price(sig["symbol"])
                if price is None:
                    continue
                is_long = sig["bias"].upper() == "LONG"
                hit = None
                if is_long:
                    if price <= sig["sl"]:
                        hit = ("LOSS", sig["sl"])
                    elif price >= sig["tp2"]:
                        hit = ("WIN_TP2", sig["tp2"])
                    elif price >= sig["tp1"]:
                        hit = ("WIN_TP1", sig["tp1"])
                else:
                    if price >= sig["sl"]:
                        hit = ("LOSS", sig["sl"])
                    elif price <= sig["tp2"]:
                        hit = ("WIN_TP2", sig["tp2"])
                    elif price <= sig["tp1"]:
                        hit = ("WIN_TP1", sig["tp1"])
                if hit:
                    update_signal_status(sig["id"], hit[0], exit_price=hit[1],
                                         notes="Auto-hit by dashboard checker")
        except Exception:
            pass
        time.sleep(60)

# Start background checker dalam daemon thread
_checker = threading.Thread(target=_check_running_signals, daemon=True)
_checker.start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/signals")
def api_signals():
    limit = int(request.args.get("limit", 100))
    return jsonify(get_recent_signals(limit))


@app.route("/api/running")
def api_running():
    return jsonify(get_running_signals())


@app.route("/api/scans")
def api_scans():
    return jsonify(get_scan_history(20))


@app.route("/api/prices")
def api_prices():
    """Harga live semua running signals (untuk update dashboard realtime)."""
    from journal import get_running_signals
    running = get_running_signals()
    prices  = {}
    for sig in running:
        price = _fetch_price(sig["symbol"])
        if price:
            is_long = sig["bias"].upper() == "LONG"
            entry   = sig["entry"]
            pnl_pct = round((price - entry) / entry * 100, 2) if is_long else round((entry - price) / entry * 100, 2)
            prices[sig["symbol"]] = {
                "price":   price,
                "pnl_pct": pnl_pct,
                "id":      sig["id"],
            }
    return jsonify(prices)

@app.route("/api/update", methods=["POST"])
def api_update():
    data       = request.get_json()
    signal_id  = data.get("id")
    status     = data.get("status")
    exit_price = data.get("exit_price")
    notes      = data.get("notes", "")
    if not signal_id or not status:
        return jsonify({"error": "id dan status wajib diisi"}), 400
    update_signal_status(signal_id, status, exit_price, notes)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("DASH_PORT", 8080))
    print(f"🖥️  Dashboard running at http://localhost:{port}")
    print("   Tekan Ctrl+C untuk berhenti.")
    app.run(host="0.0.0.0", port=port, debug=False)
