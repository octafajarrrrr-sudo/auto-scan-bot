"""
TEST ALL — Validasi semua modul bot tanpa network call.
Jalankan: python test_all.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

results = []

def ok(label, detail=""):
    results.append(("OK", label, detail))
    print(f"  [OK]   {label}" + (f" — {detail}" if detail else ""))

def fail(label, err):
    results.append(("FAIL", label, str(err)))
    print(f"  [FAIL] {label} — {err}")

print("=" * 50)
print("  CRYPTO BOT v9 — FULL MODULE TEST")
print("=" * 50)

# ── 1. config ──────────────────────────────────────────
print("\n[1] config.py")
try:
    import config
    tok = bool(config.TELEGRAM_BOT_TOKEN)
    cmc = bool(config.CMC_API_KEY)
    bnb = bool(config.BINANCE_API_KEY)
    ok("config", f"TOKEN={'SET' if tok else 'EMPTY'} CMC={'SET' if cmc else 'EMPTY'} BNB={'SET' if bnb else 'EMPTY'}")
except Exception as e:
    fail("config", e)

# ── 2. settings ────────────────────────────────────────
print("\n[2] settings.py")
try:
    import settings
    cfg = settings.load()
    detail = "tf_bias=" + cfg["tf_bias"] + " tf_entry=" + cfg["tf_entry"] + " scan=" + str(cfg["scan_interval_h"]) + "h"
    ok("settings.load()", detail)
    ok("settings.get()", "tf_bias=" + str(settings.get("tf_bias")))
except Exception as e:
    fail("settings", e)

# ── 3. risk_manager ────────────────────────────────────
print("\n[3] risk_manager.py")
try:
    from risk_manager import calculate_position_size, state, update_circuit_breaker
    r1 = calculate_position_size(1000, 100, 97, 50)
    ok("calculate_position_size", "approved=" + str(r1["approved"]) + " size=" + str(r1.get("position_usd")))
    r2 = calculate_position_size(1000, 100, 97, 95)  # extreme sentiment
    ok("sentiment filter (score=95)", "approved=" + str(r2["approved"]) + " reason=" + str(r2.get("reason", "OK")))
    ok("RiskState", "equity=" + str(state.current_equity) + " losses=" + str(state.consecutive_losses))
except Exception as e:
    fail("risk_manager", e)

# ── 4. paper_trader ────────────────────────────────────
print("\n[4] paper_trader.py")
try:
    from paper_trader import get_paper_stats, get_open_positions, get_closed_positions
    stats = get_paper_stats()
    opens = get_open_positions()
    closed = get_closed_positions(10)
    ok("get_paper_stats()", "total=" + str(stats["total"]) + " open=" + str(stats["open"]) + " winrate=" + str(stats["winrate"]) + "% pnl=$" + str(stats["total_pnl_usd"]))
    ok("get_open_positions()", str(len(opens)) + " posisi terbuka")
    ok("get_closed_positions()", str(len(closed)) + " trade terakhir")
except Exception as e:
    fail("paper_trader", e)

# ── 5. technical (logic only, no network) ──────────────
print("\n[5] technical.py")
try:
    import pandas as pd
    import numpy as np
    from technical import TechnicalEngine

    # Buat dummy exchange object
    class DummyExchange:
        pass

    engine = TechnicalEngine(DummyExchange())

    # Buat dummy OHLCV DataFrame (200 candles)
    np.random.seed(42)
    n = 200
    closes = 100 + np.cumsum(np.random.randn(n) * 0.5)
    highs  = closes + abs(np.random.randn(n) * 0.3)
    lows   = closes - abs(np.random.randn(n) * 0.3)
    opens  = closes + np.random.randn(n) * 0.1
    vols   = np.random.uniform(1000, 5000, n)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h"),
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": vols,
    })

    ema  = engine.calc_ema(df)
    ok("calc_ema()", "trend=" + ema["trend"] + " ema20=" + str(round(ema["ema20"],2)))

    struct = engine.detect_structure(df)
    ok("detect_structure()", "structure=" + struct["structure"])

    wyck = engine.detect_wyckoff_phase(df)
    ok("detect_wyckoff_phase()", "phase=" + wyck["phase"])

    elliott = engine.detect_elliott_wave(df)
    ok("detect_elliott_wave()", "wave=" + elliott["wave_position"] + " dir=" + elliott["direction"])

    fvg = engine.detect_fvg(df)
    ok("detect_fvg()", "bull=" + str(fvg["total_bullish"]) + " bear=" + str(fvg["total_bearish"]))

    liq = engine.detect_liquidity_sweep(df)
    ok("detect_liquidity_sweep()", "sweeps=" + str(len(liq["sweeps"])))

    disp = engine.detect_displacement(df)
    ok("detect_displacement()", "displacements=" + str(len(disp["displacements"])))

    ob = engine.detect_order_blocks(df)
    ok("detect_order_blocks()", "bull_obs=" + str(len(ob["bullish_obs"])) + " bear_obs=" + str(len(ob["bearish_obs"])))

    gcdc = engine.detect_golden_death_cross(df)
    ok("detect_golden_death_cross()", "cross=" + gcdc["cross"] + " bull=" + str(gcdc["bull_bias"]))

    tp = engine.calc_fibonacci_tp(100, 97, "LONG")
    ok("calc_fibonacci_tp(LONG)", "TP1=" + str(tp["TP1"]) + " TP2=" + str(tp["TP2"]) + " TP3=" + str(tp["TP3"]))

    tp2 = engine.calc_fibonacci_tp(100, 103, "SHORT")
    ok("calc_fibonacci_tp(SHORT)", "TP1=" + str(tp2["TP1"]) + " TP2=" + str(tp2["TP2"]))

except Exception as e:
    import traceback
    fail("technical", e)
    traceback.print_exc()

# ── 6. analyzer (scoring logic, no network) ────────────
print("\n[6] analyzer.py — _compute_score()")
try:
    from analyzer import CryptoBiasAnalyzer, MAX_SCORE, HIGH_THRESH, MOD_THRESH

    class DummyExchange2:
        pass

    ana = CryptoBiasAnalyzer.__new__(CryptoBiasAnalyzer)

    # Build dummy tech context
    dummy_tech = {
        "regime": {"regime_btc": "BULL_MARKET", "regime_coin": "ABOVE_EMA200"},
        "htf": {
            "structure": {"structure": "BULLISH (HH + HL)", "bos": "✅ BOS BULLISH", "choch": False},
            "ema": {"ema_score": 5, "trend": "STRONG_BULL"},
            "elliott": {"direction": "BULLISH", "score": 1, "wave_position": "Wave 3 Aktif"},
            "gc_dc": {"bull_bias": True, "bear_bias": False},
            "ob": {"has_bull_setup": True, "has_bear_setup": False},
        },
        "ltf": {"bull_confirm": True, "bear_confirm": False},
    }
    dummy_fund = {"mcap": 1e9, "fdv": 1.2e9}
    dummy_ticker = {"price": 100, "vol": 5e7, "change": 5.0, "btc_change": 2.0}
    dummy_defi = {}

    # Patch score_defi_confluence
    import analyzer as _ana_mod
    original_sdc = _ana_mod.score_defi_confluence
    _ana_mod.score_defi_confluence = lambda ctx, direction: (1, ["mock defi ok"])

    long_score, long_bd = ana._compute_score("BULLISH", dummy_tech, dummy_fund, dummy_ticker, dummy_defi, 1, 1)
    short_score, short_bd = ana._compute_score("BEARISH", dummy_tech, dummy_fund, dummy_ticker, dummy_defi, 0, 0)

    ok("_compute_score(BULLISH)", "score=" + str(long_score) + "/" + str(MAX_SCORE) + " bd=" + str(long_bd))
    ok("_compute_score(BEARISH)", "score=" + str(short_score) + "/" + str(MAX_SCORE))

    verdict = "HIGH CONVICTION" if long_score >= HIGH_THRESH else "MODERATE" if long_score >= MOD_THRESH else "NO TRADE"
    ok("Verdict logic", "LONG=" + str(long_score) + " SHORT=" + str(short_score) + " => " + verdict)

    _ana_mod.score_defi_confluence = original_sdc  # restore

except Exception as e:
    import traceback
    fail("analyzer._compute_score", e)
    traceback.print_exc()

# ── 7. sentiment (module import) ───────────────────────
print("\n[7] sentiment.py")
try:
    import sentiment
    ok("sentiment module", "functions: get_full_sentiment, score_sentiment, get_funding_rate")
    ok("TIMEOUT", str(sentiment.TIMEOUT))
    ok("FAPI url", sentiment.FAPI)
except Exception as e:
    fail("sentiment", e)

# ── 8. defillama ───────────────────────────────────────
print("\n[8] defillama.py")
try:
    import defillama
    ok("defillama module", "loaded OK")
    # Check key functions exist
    fns = ["get_full_defi_context", "score_defi_confluence"]
    for fn in fns:
        if hasattr(defillama, fn):
            ok(fn + "()", "exists")
        else:
            fail(fn, "function not found")
except Exception as e:
    fail("defillama", e)

# ── 9. whale_tracker ───────────────────────────────────
print("\n[9] whale_tracker.py")
try:
    import whale_tracker
    ok("whale_tracker module", "loaded OK")
    fns = ["get_whale_context", "score_whale"]
    for fn in fns:
        if hasattr(whale_tracker, fn):
            ok(fn + "()", "exists")
        else:
            fail(fn, "function not found")
except Exception as e:
    fail("whale_tracker", e)

# ── 10. journal ────────────────────────────────────────
print("\n[10] journal.py")
try:
    import journal
    fns = ["log_signal", "log_scan_run"]
    for fn in fns:
        if hasattr(journal, fn):
            ok(fn + "()", "exists")
        else:
            fail(fn, "function not found")
except Exception as e:
    fail("journal", e)

# ── 11. bot.py ─────────────────────────────────────────
print("\n[11] bot.py")
try:
    import bot
    fns = ["cmd_start", "cmd_analyze", "cmd_backtest", "handle_callback", "post_init"]
    for fn in fns:
        if hasattr(bot, fn):
            ok(fn + "()", "exists")
        else:
            fail(fn, "function not found")
except Exception as e:
    fail("bot", e)

# ── 12. backtest ───────────────────────────────────────
print("\n[12] backtest.py")
try:
    import backtest
    ok("backtest module", "loaded OK")
except Exception as e:
    fail("backtest", e)

# ── SUMMARY ────────────────────────────────────────────
print()
print("=" * 50)
total_ok   = sum(1 for r in results if r[0] == "OK")
total_fail = sum(1 for r in results if r[0] == "FAIL")
print(f"  SUMMARY: {total_ok} PASSED | {total_fail} FAILED")
if total_fail:
    print("\n  FAILED ITEMS:")
    for r in results:
        if r[0] == "FAIL":
            print(f"    - {r[1]}: {r[2]}")
print("=" * 50)
