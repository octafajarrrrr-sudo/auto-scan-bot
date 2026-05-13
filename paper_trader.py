"""
PAPER TRADER — Ultra Realistic Trading Simulator
- Menghitung Slippage Eksekusi (0.05%)
- Membebankan Komisi Maker/Taker (0.04%)
- Evaluasi Pessimistic (SL hit duluan jika SL & TP di candle yang sama)
"""
import sqlite3
import os
import logging
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "trades.db")
logger = logging.getLogger(__name__)

SLIPPAGE_PCT   = 0.0005
COMMISSION_PCT = 0.0004

@contextmanager
def _conn():
    """Context manager: buka koneksi, commit on success, rollback on error, selalu tutup."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                tp1_price REAL NOT NULL,
                tp2_price REAL NOT NULL,
                position_usd REAL NOT NULL,
                quantity REAL NOT NULL,
                status TEXT DEFAULT 'OPEN',
                highest_price REAL,
                lowest_price REAL,
                exit_price REAL,
                pnl_usd REAL,
                pnl_pct REAL,
                opened_at TEXT,
                closed_at TEXT
            )
        """)

def open_position(symbol: str, direction: str, raw_entry: float, raw_sl: float, 
                  tp1: float, tp2: float, position_usd: float, quantity: float) -> int:
    """Membuka posisi dengan perhitungan slippage eksekusi."""
    # Terapkan Slippage untuk mendapatkan harga eksekusi asli yang lebih buruk
    if direction == "LONG":
        true_entry = raw_entry * (1 + SLIPPAGE_PCT)
        true_sl = raw_sl * (1 - SLIPPAGE_PCT)
    else:
        true_entry = raw_entry * (1 - SLIPPAGE_PCT)
        true_sl = raw_sl * (1 + SLIPPAGE_PCT)
    
    # Deduct entry commission
    entry_fee_usd = position_usd * COMMISSION_PCT
    
    with _conn() as c:
        cur = c.execute("""
            INSERT INTO positions 
            (symbol, direction, entry_price, sl_price, tp1_price, tp2_price, 
             position_usd, quantity, highest_price, lowest_price, opened_at, pnl_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, direction, true_entry, true_sl, tp1, tp2, 
            position_usd, quantity, true_entry, true_entry, 
            datetime.now(timezone.utc).isoformat(),
            -entry_fee_usd # Modal awal langsung minus fee
        ))
        logger.info(f"OPEN {direction} {symbol} @ {true_entry:.5f} | Size: ${position_usd:.2f}")
        return cur.lastrowid

def evaluate_positions(current_prices: dict):
    """
    current_prices adalah dict: {"BTC": {"high": 60000, "low": 59000, "close": 59500}}
    Evaluasi menggunakan mode PESSIMISTIC.
    """
    from risk_manager import update_circuit_breaker
    
    with _conn() as c:
        # Ambil nama kolom via PRAGMA (lebih reliable dari LIMIT 0)
        cols_info = c.execute("PRAGMA table_info(positions)").fetchall()
        cols = [col[1] for col in cols_info]
        open_trades = c.execute("SELECT * FROM positions WHERE status='OPEN'").fetchall()
    
    for row in open_trades:
        trade = dict(zip(cols, row))
        sym = trade["symbol"]
        
        if sym not in current_prices:
            continue
            
        px = current_prices[sym]
        high = px["high"]
        low = px["low"]
        close = px["close"]
        
        direction = trade["direction"]
        entry = trade["entry_price"]
        sl = trade["sl_price"]
        tp1 = trade["tp1_price"]
        tp2 = trade["tp2_price"]
        qty = trade["quantity"]
        pnl_usd_base = trade["pnl_usd"] # Mengandung entry fee
        
        # Update extreme points untuk trailing stop tracking
        new_highest = max(trade["highest_price"] or entry, high)
        new_lowest = min(trade["lowest_price"] or entry, low)
        
        hit_type = None
        exit_price = None
        
        # ── PESSIMISTIC EVALUATION ──
        # Jika SL dan TP tersentuh dalam candle yang sama, SL DIEKSEKUSI.
        if direction == "LONG":
            if low <= sl:
                hit_type = "LOSS_SL"
                exit_price = sl
            elif high >= tp2:
                hit_type = "WIN_TP2"
                exit_price = tp2 * (1 - SLIPPAGE_PCT) # Terapkan slippage pada TP
            elif high >= tp1:
                # Opsi: Tutup 50% di TP1, tapi untuk kesederhanaan, asumsikan TP1/TP2 behavior penuh
                hit_type = "WIN_TP1"
                exit_price = tp1 * (1 - SLIPPAGE_PCT)
        else: # SHORT
            if high >= sl:
                hit_type = "LOSS_SL"
                exit_price = sl
            elif low <= tp2:
                hit_type = "WIN_TP2"
                exit_price = tp2 * (1 + SLIPPAGE_PCT)
            elif low <= tp1:
                hit_type = "WIN_TP1"
                exit_price = tp1 * (1 + SLIPPAGE_PCT)
                
        if hit_type:
            # Kalkulasi PnL
            if direction == "LONG":
                gross_pnl = (exit_price - entry) * qty
            else:
                gross_pnl = (entry - exit_price) * qty
                
            exit_fee = (exit_price * qty) * COMMISSION_PCT
            net_pnl_usd = gross_pnl + pnl_usd_base - exit_fee
            pnl_pct = net_pnl_usd / trade["position_usd"] * 100
            
            with _conn() as c:
                c.execute("""
                    UPDATE positions SET 
                    status=?, exit_price=?, pnl_usd=?, pnl_pct=?, closed_at=?,
                    highest_price=?, lowest_price=?
                    WHERE id=?
                """, (
                    hit_type, exit_price, round(net_pnl_usd, 2), round(pnl_pct, 2),
                    datetime.now(timezone.utc).isoformat(), new_highest, new_lowest, trade["id"]
                ))
            
            logger.info(f"CLOSE {sym} ({hit_type}) | PnL: ${net_pnl_usd:.2f} ({pnl_pct:.2f}%)")
            update_circuit_breaker(net_pnl_usd)
        else:
            # Belum hit, update extreme points
            with _conn() as c:
                c.execute("""
                    UPDATE positions SET highest_price=?, lowest_price=? WHERE id=?
                """, (new_highest, new_lowest, trade["id"]))

# ── 3. Read API untuk Dashboard & Telegram ────────────────────────

def get_open_positions() -> list[dict]:
    with _conn() as c:
        cols_info = c.execute("PRAGMA table_info(positions)").fetchall()
        cols = [col[1] for col in cols_info]
        rows = c.execute("SELECT * FROM positions WHERE status='OPEN' ORDER BY id DESC").fetchall()
    return [dict(zip(cols, r)) for r in rows]

def get_closed_positions(limit: int = 100) -> list[dict]:
    with _conn() as c:
        cols_info = c.execute("PRAGMA table_info(positions)").fetchall()
        cols = [col[1] for col in cols_info]
        rows = c.execute("SELECT * FROM positions WHERE status != 'OPEN' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(cols, r)) for r in rows]

def get_paper_stats() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT status, pnl_pct, pnl_usd FROM positions WHERE status != 'OPEN'").fetchall()
        open_count = c.execute("SELECT COUNT(*) FROM positions WHERE status='OPEN'").fetchone()[0]
        
    total = len(rows)
    wins = [r for r in rows if str(r[0]).startswith("WIN")]
    losses = [r for r in rows if r[0] == "LOSS_SL"]
    winrate = round(len(wins) / total * 100, 1) if total else 0
    avg_pnl_pct = round(sum(r[1] or 0 for r in rows) / total, 2) if total else 0
    total_pnl_usd = round(sum(r[2] or 0 for r in rows), 2)
    
    return {
        "open": open_count,
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "winrate": winrate,
        "avg_pnl": avg_pnl_pct,
        "total_pnl_usd": total_pnl_usd
    }

def get_performance_by_symbol() -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT symbol, COUNT(*) as total,
                   SUM(CASE WHEN status LIKE 'WIN%' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN status='LOSS_SL' THEN 1 ELSE 0 END) as losses,
                   ROUND(AVG(pnl_pct), 2) as avg_pnl,
                   ROUND(SUM(pnl_usd), 2) as total_pnl
            FROM positions
            WHERE status != 'OPEN'
            GROUP BY symbol
            ORDER BY total_pnl DESC
        """).fetchall()
    return [{"symbol":r[0],"total":r[1],"wins":r[2],"losses":r[3],
             "avg_pnl":r[4],"total_pnl":r[5]} for r in rows]

def close_position_manual(pos_id: int, exit_price: float):
    """Untuk tombol tutup manual dari Telegram/Dashboard."""
    from risk_manager import update_circuit_breaker
    with _conn() as c:
        # Ambil data posisi
        cols_info = c.execute("PRAGMA table_info(positions)").fetchall()
        cols = [col[1] for col in cols_info]
        row = c.execute("SELECT * FROM positions WHERE id=? AND status='OPEN'", (pos_id,)).fetchone()
        if not row: return False
        
        trade = dict(zip(cols, row))
        qty = trade["quantity"]
        entry = trade["entry_price"]
        direction = trade["direction"]
        pnl_usd_base = trade["pnl_usd"] or 0
        
        # Kalkulasi manual
        if direction == "LONG":
            gross_pnl = (exit_price - entry) * qty
        else:
            gross_pnl = (entry - exit_price) * qty
            
        exit_fee = (exit_price * qty) * COMMISSION_PCT
        net_pnl_usd = gross_pnl + pnl_usd_base - exit_fee
        pnl_pct = net_pnl_usd / trade["position_usd"] * 100
        
        c.execute("""
            UPDATE positions SET 
            status='CLOSED_MANUAL', exit_price=?, pnl_usd=?, pnl_pct=?, closed_at=?
            WHERE id=?
        """, (exit_price, round(net_pnl_usd, 2), round(pnl_pct, 2),
              datetime.now(timezone.utc).isoformat(), pos_id))
        
    logger.info(f"MANUAL CLOSE {trade['symbol']} | PnL: ${net_pnl_usd:.2f}")
    update_circuit_breaker(net_pnl_usd)
    # pnl_r = PnL dalam satuan R (risk unit): pnl / (SL distance %)
    sl_dist = abs(entry - trade["sl_price"]) / entry if entry else 1
    pnl_r   = round((net_pnl_usd / trade["position_usd"]) / sl_dist, 2) if sl_dist else 0
    return {
        "pnl_pct": round(pnl_pct, 2),
        "pnl_usd": round(net_pnl_usd, 2),
        "pnl_r":   pnl_r,
    }

# Init on load
init_db()
