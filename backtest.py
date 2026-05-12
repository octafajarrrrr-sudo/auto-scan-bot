"""
BACKTEST / PAPER TRADE ENGINE
Paper trade: eksekusi sinyal real-time tanpa uang nyata, track PnL.
Backtest   : replay sinyal pada historical OHLCV untuk estimasi winrate.
"""
import json, os, time
from datetime import datetime, timezone
from journal import _conn, init_db

# ══════════════════════════════════════════════════════════════════════════════
# PAPER TRADE — Real-time signal tracking tanpa eksekusi nyata
# ══════════════════════════════════════════════════════════════════════════════

def open_paper_trade(symbol, bias, entry, sl, tp1, tp2, tp3, sl_pct, rr, score):
    """Buka posisi paper trade baru. Return trade_id."""
    init_db()
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_open   TEXT,
                symbol    TEXT,
                bias      TEXT,
                entry     REAL,
                sl        REAL,
                tp1       REAL,
                tp2       REAL,
                tp3       REAL,
                sl_pct    REAL,
                rr        TEXT,
                score     INTEGER,
                status    TEXT DEFAULT 'OPEN',
                exit_price REAL,
                ts_close  TEXT,
                pnl_pct   REAL,
                pnl_r     REAL,
                hit_level TEXT
            )
        """)
        cur = c.execute("""
            INSERT INTO paper_trades
            (ts_open,symbol,bias,entry,sl,tp1,tp2,tp3,sl_pct,rr,score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (datetime.now(timezone.utc).isoformat(),
              symbol.upper(), bias.upper(),
              entry, sl, tp1, tp2, tp3, sl_pct, rr, score))
        return cur.lastrowid

def close_paper_trade(trade_id: int, exit_price: float, hit_level: str = "MANUAL"):
    """Tutup paper trade dengan exit price. Return ringkasan."""
    with _conn() as c:
        row = c.execute(
            "SELECT entry, sl, tp1, tp2, tp3, bias FROM paper_trades WHERE id=?",
            (trade_id,)
        ).fetchone()
        if not row:
            return None
        entry, sl, tp1, tp2, tp3, bias = row
        if bias == "LONG":
            pnl_pct = (exit_price - entry) / entry * 100
            pnl_r   = (exit_price - entry) / abs(entry - sl) if entry != sl else 0
        else:
            pnl_pct = (entry - exit_price) / entry * 100
            pnl_r   = (entry - exit_price) / abs(sl - entry) if sl != entry else 0

        pnl_pct = round(pnl_pct, 2)
        pnl_r   = round(pnl_r, 2)

        c.execute("""
            UPDATE paper_trades
            SET status='CLOSED', exit_price=?, ts_close=?, pnl_pct=?, pnl_r=?, hit_level=?
            WHERE id=?
        """, (exit_price,
              datetime.now(timezone.utc).isoformat(),
              pnl_pct, pnl_r, hit_level, trade_id))
        return {"pnl_pct": pnl_pct, "pnl_r": pnl_r, "hit_level": hit_level}

def get_open_paper_trades() -> list:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts_open TEXT, symbol TEXT,
                bias TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
                sl_pct REAL, rr TEXT, score INTEGER, status TEXT DEFAULT 'OPEN',
                exit_price REAL, ts_close TEXT, pnl_pct REAL, pnl_r REAL, hit_level TEXT
            )
        """)
        rows = c.execute("""
            SELECT id,ts_open,symbol,bias,entry,sl,tp1,tp2,tp3,sl_pct,rr,score
            FROM paper_trades WHERE status='OPEN' ORDER BY id DESC
        """).fetchall()
    cols = ["id","ts_open","symbol","bias","entry","sl","tp1","tp2","tp3","sl_pct","rr","score"]
    return [dict(zip(cols, r)) for r in rows]

def get_paper_stats() -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT pnl_pct, pnl_r, hit_level FROM paper_trades WHERE status='CLOSED'"
        ).fetchall()
    if not rows:
        return {"total":0,"wins":0,"losses":0,"winrate":0,"avg_pnl":0,"avg_r":0,"open":0}

    wins   = [r for r in rows if r[0] and r[0] > 0]
    losses = [r for r in rows if r[0] and r[0] <= 0]
    total  = len(rows)

    with _conn() as c:
        open_count = c.execute(
            "SELECT COUNT(*) FROM paper_trades WHERE status='OPEN'"
        ).fetchone()[0]

    return {
        "total":   total,
        "wins":    len(wins),
        "losses":  len(losses),
        "winrate": round(len(wins)/total*100, 1) if total else 0,
        "avg_pnl": round(sum(r[0] for r in rows if r[0])/total, 2) if total else 0,
        "avg_r":   round(sum(r[1] for r in rows if r[1])/total, 2) if total else 0,
        "open":    open_count,
    }

def check_paper_trades(exchange) -> list:
    """
    Cek semua open paper trades terhadap harga current.
    Auto-close jika SL/TP1/TP2 tersentuh. Return list of events.
    """
    trades = get_open_paper_trades()
    events = []
    for t in trades:
        try:
            ticker = exchange.fetch_ticker(f"{t['symbol']}/USDT")
            high   = ticker["high"]
            low    = ticker["low"]
            is_long = t["bias"] == "LONG"

            hit = None
            exit_px = None

            if is_long:
                if low <= t["sl"]:
                    hit = "LOSS_SL"; exit_px = t["sl"]
                elif high >= t["tp2"]:
                    hit = "WIN_TP2"; exit_px = t["tp2"]
                elif high >= t["tp1"]:
                    hit = "WIN_TP1"; exit_px = t["tp1"]
            else:
                if high >= t["sl"]:
                    hit = "LOSS_SL"; exit_px = t["sl"]
                elif low <= t["tp2"]:
                    hit = "WIN_TP2"; exit_px = t["tp2"]
                elif low <= t["tp1"]:
                    hit = "WIN_TP1"; exit_px = t["tp1"]

            if hit:
                result = close_paper_trade(t["id"], exit_px, hit)
                events.append({
                    "symbol":    t["symbol"],
                    "bias":      t["bias"],
                    "hit_level": hit,
                    "pnl_pct":   result["pnl_pct"],
                    "pnl_r":     result["pnl_r"],
                    "trade_id":  t["id"],
                })
        except Exception:
            pass
    return events


# ══════════════════════════════════════════════════════════════════════════════
# BACKTEST — Replay signal logic pada historical OHLCV
# ══════════════════════════════════════════════════════════════════════════════

async def run_backtest(engine, symbol: str, tf_bias: str = "4h",
                       tf_entry: str = "15m", lookback: int = 500) -> dict:
    """
    Backtest sederhana berbasis forward-walk:
    1. Sliding window pada historical data
    2. Setiap sinyal divalidasi terhadap candle berikutnya
    3. Track TP1/TP2/SL hits

    Return dict hasil backtest.
    """
    df_bias  = await engine.fetch_ohlcv(symbol, tf_bias,  lookback)
    df_entry = await engine.fetch_ohlcv(symbol, tf_entry, lookback * 4)

    if df_bias is None or df_entry is None:
        return {"error": "Gagal fetch data historis"}

    results = []
    step    = max(20, len(df_bias) // 30)   # ~30 sample points

    for i in range(100, len(df_bias) - 5, step):
        # Simulasi: gunakan data sampai index i sebagai "saat ini"
        df_window = df_bias.iloc[:i].copy()
        if len(df_window) < 50:
            continue

        # Deteksi struktur & bias pada window
        struct  = engine.detect_structure(df_window)
        ema     = engine.calc_ema(df_window)
        s_str   = struct["structure"]
        e_score = ema["ema_score"]

        # Tentukan bias (simplified dari full_analysis)
        bull = 0; bear = 0
        if "CHoCH BULLISH" in s_str:   bull += 1
        elif "CHoCH BEARISH" in s_str: bear += 1
        elif "BULLISH" in s_str:       bull += 2
        elif "BEARISH" in s_str:       bear += 2

        if e_score >= 4:   bull += 2
        elif e_score >= 3: bull += 1
        elif e_score <= 1: bear += 2
        elif e_score <= 2: bear += 1

        if bull < 3 and bear < 3:
            continue   # Tidak ada bias kuat

        bias   = "LONG" if bull > bear else "SHORT"
        is_long = bias == "LONG"

        # Entry price & ATR dari candle terakhir window
        entry = float(df_window["close"].values[-1])
        atr   = engine.detect_displacement(df_window)["atr"]

        sl    = entry - atr * 1.5 if is_long else entry + atr * 1.5
        risk  = abs(entry - sl)
        if risk <= 0:
            continue

        # Batasi SL ke 3% untuk intraday
        max_sl = entry * 0.03
        if risk > max_sl:
            sl   = entry - max_sl if is_long else entry + max_sl
            risk = max_sl

        tp1 = entry + risk * 2.0 if is_long else entry - risk * 2.0
        tp2 = entry + risk * 2.618 if is_long else entry - risk * 2.618

        # Forward-walk: cek 20 candle ke depan (di tf_entry yang lebih kecil)
        # Estimasi index di df_entry
        entry_ts = df_window["timestamp"].values[-1]
        entry_idx = None
        for j, ts in enumerate(df_entry["timestamp"].values):
            if ts >= entry_ts:
                entry_idx = j
                break

        if entry_idx is None or entry_idx + 20 >= len(df_entry):
            continue

        hit = "NO_HIT"
        pnl = 0.0
        for k in range(entry_idx + 1, min(entry_idx + 21, len(df_entry))):
            h = float(df_entry["high"].values[k])
            l = float(df_entry["low"].values[k])

            if is_long:
                if l <= sl:   hit = "LOSS"; pnl = -risk/entry*100; break
                if h >= tp2:  hit = "WIN_TP2"; pnl = (tp2-entry)/entry*100; break
                if h >= tp1:  hit = "WIN_TP1"; pnl = (tp1-entry)/entry*100; break
            else:
                if h >= sl:   hit = "LOSS"; pnl = -risk/entry*100; break
                if l <= tp2:  hit = "WIN_TP2"; pnl = (entry-tp2)/entry*100; break
                if l <= tp1:  hit = "WIN_TP1"; pnl = (entry-tp1)/entry*100; break

        results.append({
            "index": i,
            "bias":  bias,
            "entry": entry,
            "sl":    round(sl, 6),
            "tp1":   round(tp1, 6),
            "tp2":   round(tp2, 6),
            "hit":   hit,
            "pnl":   round(pnl, 2),
        })

    if not results:
        return {"error": "Tidak ada sinyal terdeteksi dalam periode historis"}

    closed  = [r for r in results if r["hit"] != "NO_HIT"]
    wins    = [r for r in closed  if r["hit"].startswith("WIN")]
    losses  = [r for r in closed  if r["hit"] == "LOSS"]
    no_hit  = [r for r in results if r["hit"] == "NO_HIT"]

    total_pnl = sum(r["pnl"] for r in closed)
    avg_pnl   = total_pnl / len(closed) if closed else 0
    winrate   = len(wins) / len(closed) * 100 if closed else 0

    return {
        "symbol":       symbol,
        "tf_bias":      tf_bias,
        "tf_entry":     tf_entry,
        "total_signals": len(results),
        "closed":       len(closed),
        "wins":         len(wins),
        "losses":       len(losses),
        "no_hit":       len(no_hit),
        "winrate_pct":  round(winrate, 1),
        "total_pnl_pct":round(total_pnl, 2),
        "avg_pnl_pct":  round(avg_pnl, 2),
        "sample_trades": results[-5:],   # 5 trade terakhir sebagai sample
    }

def format_backtest_report(r: dict, symbol: str) -> str:
    if "error" in r:
        return f"❌ Backtest {symbol}: {r['error']}"

    sample = "\n".join([
        f"  {'🟢' if t['hit'].startswith('WIN') else '🔴' if t['hit']=='LOSS' else '⚪'} "
        f"{t['bias']} @ {t['entry']:.4f} → {t['hit']} ({t['pnl']:+.2f}%)"
        for t in r["sample_trades"]
    ])

    return f"""
📊 *BACKTEST — {symbol}*
TF: {r['tf_bias']} bias · {r['tf_entry']} entry

Total Sinyal : {r['total_signals']}
Closed       : {r['closed']} (Win:{r['wins']} Loss:{r['losses']})
No Hit (20c) : {r['no_hit']}
Winrate      : {r['winrate_pct']}%
Avg PnL      : {r['avg_pnl_pct']:+.2f}%
Total PnL    : {r['total_pnl_pct']:+.2f}%

*Sample Trades (5 terakhir):*
{sample}
"""
