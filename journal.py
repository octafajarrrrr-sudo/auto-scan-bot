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
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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


def get_equity_curve(limit: int = 100) -> list:
    """Data equity curve untuk chart — cumulative PnL dari closed signals."""
    with _conn() as c:
        rows = c.execute("""
            SELECT ts, symbol, bias, pnl_pct, status
            FROM signals
            WHERE status NOT IN ('RUNNING','CANCELLED') AND pnl_pct IS NOT NULL
            ORDER BY id ASC LIMIT ?
        """, (limit,)).fetchall()
    cumulative = 0.0
    result = []
    for ts, sym, bias, pnl, status in rows:
        cumulative = round(cumulative + (pnl or 0), 2)
        result.append({
            "ts": ts, "symbol": sym, "bias": bias,
            "pnl": pnl, "cumulative": cumulative,
            "win": status.startswith("WIN")
        })
    return result


def get_performance_by_symbol() -> list:
    """Performance per simbol untuk leaderboard."""
    with _conn() as c:
        rows = c.execute("""
            SELECT symbol,
                   COUNT(*) as total,
                   SUM(CASE WHEN status LIKE 'WIN%' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN status='LOSS' THEN 1 ELSE 0 END) as losses,
                   ROUND(AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct END), 2) as avg_pnl,
                   ROUND(SUM(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE 0 END), 2) as total_pnl
            FROM signals
            WHERE status NOT IN ('RUNNING','CANCELLED')
            GROUP BY symbol
            ORDER BY total_pnl DESC
        """).fetchall()
    return [{"symbol":r[0],"total":r[1],"wins":r[2],"losses":r[3],
             "avg_pnl":r[4],"total_pnl":r[5]} for r in rows]


def get_scan_detail(limit: int = 5) -> list:
    """Scan history dengan daftar sinyal yang ditemukan."""
    with _conn() as c:
        scans = c.execute("""
            SELECT id, ts, total, signals, top2
            FROM scan_runs ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()

    result = []
    for sid, ts, total, found, top2_json in scans:
        try:
            symbols = json.loads(top2_json)
        except Exception:
            symbols = []
        result.append({
            "scan_id": sid, "ts": ts, "total_scanned": total,
            "signals_found": found, "top_symbols": symbols
        })
    return result


def get_full_stats() -> dict:
    """Stats lengkap: winrate, expectancy, profit factor, streak."""
    with _conn() as c:
        rows = c.execute("""
            SELECT status, pnl_pct FROM signals
            WHERE status NOT IN ('RUNNING','CANCELLED')
        """).fetchall()

        all_closed = len(rows)
        wins   = [r[1] for r in rows if r[0].startswith("WIN") and r[1]]
        losses = [abs(r[1]) for r in rows if r[0]=="LOSS" and r[1]]

        winrate     = round(len(wins)/all_closed*100, 1) if all_closed else 0
        avg_win     = round(sum(wins)/len(wins), 2)   if wins   else 0
        avg_loss    = round(sum(losses)/len(losses), 2) if losses else 0
        expectancy  = round((winrate/100 * avg_win) - ((1 - winrate/100) * avg_loss), 2)
        profit_fac  = round(sum(wins)/sum(losses), 2) if losses and sum(losses)>0 else 0
        total_pnl   = round(sum(r[1] for r in rows if r[1]), 2)

        # Streak
        streak = 0; max_streak = 0; cur_streak = 0
        for r in rows:
            if r[0].startswith("WIN"):
                cur_streak += 1
                max_streak = max(max_streak, cur_streak)
            else:
                cur_streak = 0
        streak = cur_streak

        running = c.execute(
            "SELECT COUNT(*) FROM signals WHERE status='RUNNING'"
        ).fetchone()[0]

        return {
            "total_closed": all_closed,
            "wins": len(wins),
            "losses": len(losses),
            "winrate_pct": winrate,
            "avg_win_pct": avg_win,
            "avg_loss_pct": -avg_loss,
            "avg_pnl_pct": round(total_pnl/all_closed, 2) if all_closed else 0,
            "expectancy": expectancy,
            "profit_factor": profit_fac,
            "total_pnl_pct": total_pnl,
            "current_streak": streak,
            "max_streak": max_streak,
            "running_positions": running,
        }


# Init on import
init_db()
