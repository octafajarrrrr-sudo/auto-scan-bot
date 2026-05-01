"""
DASHBOARD — Flask monitoring server
Akses: http://localhost:8080
Jalankan terpisah: python dashboard.py
"""

import os
import sys
import json
from flask import Flask, render_template, jsonify, request

# Pastikan direktori proyek ada di path
sys.path.insert(0, os.path.dirname(__file__))

from journal import (
    get_stats, get_recent_signals, get_running_signals,
    get_scan_history, update_signal_status, init_db
)

app = Flask(__name__, template_folder="templates")
init_db()


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
