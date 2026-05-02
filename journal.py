"""
JOURNAL — SQLite Trade Signal Logger
Tracks: signal entries, SL/TP hits, winrate, running positions.
"""

import sqlite3
import json
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "journal.db")


def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    """Inisialisasi tabel jika belum ada."""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                bias        TEXT    NOT NULL,
                entry       REAL    NOT NULL,
                sl          REAL    NOT NULL,
                tp1         REAL    NOT NULL,
                tp2         REAL    NOT NULL,
                tp3         REAL    NOT NULL,
                sl_pct      REAL    NOT NULL,
                rr          TEXT    NOT NULL,
                confidence  TEXT    NOT NULL,
                htf_bias    TEXT,
                defi_score  INTEGER DEFAULT 0,
                status      TEXT    DEFAULT 'RUNNING',
                exit_price  REAL    DEFAULT NULL,
                exit_ts     TEXT    DEFAULT NULL,
                pnl_pct     REAL    DEFAULT NULL,
                notes       TEXT    DEFAULT ''
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS scan_runs (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT    NOT NULL,
                total   INTEGER DEFAULT 0,
                signals INTEGER DEFAULT 0,
                top2    TEXT    DEFAULT '[]'
            )
        """)


def log_signal(symbol, bias, entry, sl, tp1, tp2, tp3, sl_pct,
               rr, confidence, htf_bias="", defi_score=0, notes="") -> int:
    """Catat sinyal baru ke journal. Return signal ID."""
    with _conn() as c:
        cur = c.execute("""
            INSERT INTO signals
            (ts, symbol, bias, entry, sl, tp1, tp2, tp3, sl_pct, rr,
             confidence, htf_bias, defi_score, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            symbol.upper(), bias.upper(),
            entry, sl, tp1, tp2, tp3, sl_pct,
            rr, confidence, htf_bias, defi_score, notes
        ))
        return cur.lastrowid


def log_scan_run(total_scanned: int, signals_found: int, top2: list):
    """Catat ringkasan setiap scan run."""
    with _conn() as c:
        c.execute("""
            INSERT INTO scan_runs (ts, total, signals, top2)
            VALUES (?,?,?,?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            total_scanned, signals_found,
            json.dumps(top2)
        ))


def update_signal_status(signal_id: int, status: str,
                          exit_price: float = None, notes: str = ""):
    """
    Update status sinyal: RUNNING → WIN_TP1/WIN_TP2/WIN_TP3/LOSS/CANCELLED.
    """
    pnl = None
    with _conn() as c:
        row = c.execute("SELECT entry, sl, tp1, tp2, tp3, bias FROM signals WHERE id=?",
                        (signal_id,)).fetchone()
        if row:
            entry, sl, tp1, tp2, tp3, bias = row
            if exit_price is not None:
                if bias == "LONG":
                    pnl = round((exit_price - entry) / entry * 100, 2)
                else:
                    pnl = round((entry - exit_price) / entry * 100, 2)

        c.execute("""
            UPDATE signals
            SET status=?, exit_price=?, exit_ts=?, pnl_pct=?, notes=notes||?
            WHERE id=?
        """, (
            status, exit_price,
            datetime.now(timezone.utc).isoformat() if exit_price else None,
            pnl,
            f" | {notes}" if notes else "",
            signal_id
        ))


def get_stats() -> dict:
    """Hitung statistik winrate dan performance."""
    with _conn() as c:
        rows = c.execute("""
            SELECT status, pnl_pct FROM signals
            WHERE status != 'RUNNING' AND status != 'CANCELLED'
        """).fetchall()

        total    = len(rows)
        wins     = [r for r in rows if r[0].startswith("WIN")]
        losses   = [r for r in rows if r[0] == "LOSS"]
        winrate  = round(len(wins) / total * 100, 1) if total else 0
        avg_pnl  = round(sum(r[1] for r in rows if r[1]) / total, 2) if total else 0
        avg_win  = round(sum(r[1] for r in wins if r[1]) / len(wins), 2) if wins else 0
        avg_loss = round(sum(r[1] for r in losses if r[1]) / len(losses), 2) if losses else 0

        running = c.execute(
            "SELECT COUNT(*) FROM signals WHERE status='RUNNING'"
        ).fetchone()[0]

        return {
            "total_closed": total,
            "wins": len(wins),
            "losses": len(losses),
            "winrate_pct": winrate,
            "avg_pnl_pct": avg_pnl,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "running_positions": running,
        }


def get_recent_signals(limit: int = 50) -> list:
    """Ambil sinyal terbaru untuk dashboard."""
    with _conn() as c:
        rows = c.execute("""
            SELECT id, ts, symbol, bias, entry, sl, tp1, tp2, tp3,
                   sl_pct, rr, confidence, htf_bias, defi_score,
                   status, exit_price, pnl_pct, notes
            FROM signals ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        cols = ["id","ts","symbol","bias","entry","sl","tp1","tp2","tp3",
                "sl_pct","rr","confidence","htf_bias","defi_score",
                "status","exit_price","pnl_pct","notes"]
        return [dict(zip(cols, r)) for r in rows]


def get_running_signals() -> list:
    """Sinyal yang masih RUNNING."""
    with _conn() as c:
        rows = c.execute("""
            SELECT id, ts, symbol, bias, entry, sl, tp1, tp2, tp3,
                   sl_pct, rr, confidence, defi_score
            FROM signals WHERE status='RUNNING' ORDER BY id DESC
        """).fetchall()
        cols = ["id","ts","symbol","bias","entry","sl","tp1","tp2","tp3",
                "sl_pct","rr","confidence","defi_score"]
        return [dict(zip(cols, r)) for r in rows]


def get_scan_history(limit: int = 20) -> list:
    """Riwayat scan run untuk monitoring."""
    with _conn() as c:
        rows = c.execute("""
            SELECT ts, total, signals, top2
            FROM scan_runs ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [
            {"ts": r[0], "total": r[1], "signals": r[2],
             "top2": json.loads(r[3])}
            for r in rows
        ]


# Init on import
init_db()
