"""
MAIN — Entry Point & Orchestrator
Menjalankan Telegram Bot dan Scanner Loop secara concurrent menggunakan asyncio.
"""
import asyncio
import logging
from datetime import datetime, timezone
from telegram.ext import ApplicationBuilder

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from settings import load as cfg_load
import bot  # Import handlers dari bot.py

from analyzer import CryptoBiasAnalyzer, HIGH_THRESH  # single source of truth
from sentiment import score_sentiment, purge_cache as purge_sentiment_cache
from risk_manager import calculate_position_size, state, reset_daily_equity
from paper_trader import open_position, evaluate_positions, init_db, _conn as paper_conn
from journal import log_scan_run

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Queue internal untuk memisahkan Scanner dan Execution
signal_queue = asyncio.Queue()

# Shared analyzer instances (closed on shutdown via post_shutdown)
_scanner_analyzer  = None
_eval_analyzer     = None

async def scanner_loop(app):
    """Loop utama untuk memindai market secara periodik."""
    global _scanner_analyzer
    logger.info("Scanner loop started.")
    
    # Tunggu bot siap sebelum mulai
    await asyncio.sleep(5)
    
    _scanner_analyzer = CryptoBiasAnalyzer()
    
    while True:
        try:
            cfg = cfg_load()
            interval_hours   = cfg.get("scan_interval_h", 4)
            interval_seconds = interval_hours * 3600
            max_results      = cfg.get("max_results", 100)  # respect user setting
            
            # ── Issue 3 fix: notifikasi Telegram — scan dimulai ──────
            _tg = app.bot if TELEGRAM_CHAT_ID else None
            async def _tg_send(text: str):
                if _tg:
                    try:
                        await _tg.send_message(
                            chat_id=TELEGRAM_CHAT_ID, text=text,
                            parse_mode="HTML",
                        )
                    except Exception as te:
                        logger.warning(f"TG notify failed: {te}")

            await _tg_send(
                f"🔍 <b>Auto-Scan Dimulai</b>\n"
                f"Interval: {interval_hours}h · Max: {max_results} koin"
            )

            candidates = await _scanner_analyzer.scan_market_by_volume(
                min_vol=10_000_000,
                max_symbols=max_results,
            )

            if not candidates:
                await _tg_send("❌ Tidak ada koin yang lolos volume filter ($10M).")
                logger.info("Siklus selesai: tidak ada kandidat.")
                await asyncio.sleep(interval_seconds)
                continue

            await _tg_send(f"✅ {len(candidates)} koin lolos filter → mulai analisis...")

            # ── Bug fix ②: concurrent scan dengan Semaphore ──────────
            # Sequential: 100 syms × ~3s = ~5 menit. Concurrent (10): ~30 detik.
            _SEM = asyncio.Semaphore(10)

            async def _scan_one(sym: str):
                async with _SEM:
                    return sym, await _scanner_analyzer.quick_scan(sym)

            tasks   = [_scan_one(sym) for sym, _vol in candidates]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            signals_found  = 0
            top_symbols    = []

            for item in results:
                if isinstance(item, Exception):
                    logger.debug(f"Scan task error: {item}")
                    continue
                sym, res = item
                if not res:
                    continue

                report, bias, score, ticker, tech, fund, bd = res
                if bias != "SKIP" and score >= HIGH_THRESH:
                    exec_data = tech.get("execution", {})
                    await signal_queue.put({
                        "symbol":     sym,
                        "bias":       bias,
                        "score":      score,
                        "sent_score": bd.get("L8_sentiment", 0),
                        "exec_data":  exec_data,
                        "tech":       tech,
                        "report":     report,
                    })
                    signals_found += 1
                    top_symbols.append(sym)
                    logger.info(f"Signal enqueued: {sym} {bias} score={score}")

                    # Kirim notifikasi singkat per-sinyal ke Telegram
                    e  = tech.get("execution", {})
                    tp = e.get("tp", {})
                    bd_str = (
                        f"L1:{bd.get('L1_regime',0)} L2:{bd.get('L2_structure',0)} "
                        f"L3:{bd.get('L3_ema',0)} L4:{bd.get('L4_elliott',0)} "
                        f"L5:{bd.get('L5_ltf',0)} L8:{bd.get('L8_sentiment',0)} "
                        f"L9:{bd.get('L9_whale',0)} L10:{bd.get('L10_gc_dc',0)}"
                    )
                    await _tg_send(
                        f"⚡ <b>SIGNAL [{bias}] {sym}</b>  Score: {score}/14\n"
                        f"Entry: <code>{e.get('entry',0):.5f}</code>  "
                        f"SL: <code>{e.get('sl',0):.5f}</code>  "
                        f"TP1: <code>{tp.get('TP1',0):.5f}</code>\n"
                        f"RR: {e.get('rr','N/A')}  SL%: {e.get('sl_pct',0):.2f}%\n"
                        f"<code>{bd_str}</code>"
                    )

            # ── Bug fix ⑤: catat ke DB agar Scan History tampil di dashboard ──
            try:
                log_scan_run(
                    total_scanned=len(candidates),
                    signals_found=signals_found,
                    top2=top_symbols,
                )
            except Exception as e:
                logger.debug(f"log_scan_run error: {e}")

            # ── Issue 3 fix: ringkasan akhir siklus ke Telegram ──────
            if signals_found == 0:
                summary_msg = (
                    f"😴 <b>Scan Selesai</b> — tidak ada sinyal HIGH CONVICTION\n"
                    f"Dipindai: {len(candidates)} koin · "
                    f"Next scan dalam {interval_hours}h"
                )
            else:
                syms_str = " · ".join(top_symbols[:10])
                summary_msg = (
                    f"✅ <b>Scan Selesai</b> — {signals_found} sinyal ditemukan\n"
                    f"Simbol: <code>{syms_str}</code>\n"
                    f"Next scan dalam {interval_hours}h"
                )
            await _tg_send(summary_msg)

            logger.info(f"Siklus selesai. {signals_found} signal(s). Menunggu {interval_hours} jam...")
            await asyncio.sleep(interval_seconds)
            
        except asyncio.CancelledError:
            logger.info("Scanner loop dibatalkan (shutdown).")
            break
        except Exception as e:
            logger.error(f"Error di scanner loop: {e}", exc_info=True)
            await asyncio.sleep(60)

async def execution_loop(app):
    """Loop untuk memproses sinyal dari queue & evaluasi posisi."""
    global _eval_analyzer
    logger.info("Execution loop started.")
    
    _eval_analyzer = CryptoBiasAnalyzer()
    
    while True:
        try:
            # 1. Evaluasi Posisi Terbuka (setiap loop / timeout)
            try:
                # Gunakan paper_conn() agar konsisten WAL mode
                with paper_conn() as conn:
                    open_syms = [r[0] for r in conn.execute(
                        "SELECT DISTINCT symbol FROM positions WHERE status='OPEN'"
                    ).fetchall()]
                
                if open_syms:
                    # ── Bug fix ⑥: parallel OHLCV fetch ─────────────
                    async def _fetch_price(s: str):
                        try:
                            ohlcv = await _eval_analyzer.binance.fetch_ohlcv(
                                f"{s}/USDT", "15m", limit=1
                            )
                            if ohlcv:
                                return s, {"high": ohlcv[-1][2], "low": ohlcv[-1][3], "close": ohlcv[-1][4]}
                        except Exception:
                            pass
                        return s, None
                    fetched = await asyncio.gather(
                        *[_fetch_price(s) for s in open_syms],
                        return_exceptions=True,
                    )
                    current_prices = {s: p for item in fetched if not isinstance(item, Exception) for s, p in [item] if p}
                    if current_prices:
                        evaluate_positions(current_prices)
            except Exception as e:
                logger.debug(f"Position eval skipped: {e}")
            
            # 2. Ambil sinyal dari queue (non-blocking, timeout 60 detik)
            try:
                signal = await asyncio.wait_for(signal_queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                continue
                
            sym       = signal["symbol"]
            bias      = signal["bias"]
            exec_data = signal["exec_data"]   # correctly sourced from tech["execution"]
            report    = signal["report"]
            
            logger.info(f"Memproses sinyal: {sym} ({bias})")
            
            if "NO TRADE" in exec_data.get("entry_mode", "NO TRADE") or "BLOCKED" in exec_data.get("entry_mode", ""):
                signal_queue.task_done()
                continue
                
            # Cek Circuit Breaker
            if state.stop_trading_global:
                logger.warning(f"Sinyal {sym} diabaikan. Circuit Breaker AKTIF (Global Stop).")
                signal_queue.task_done()
                continue
            if state.stop_trading_until and datetime.now(timezone.utc) < state.stop_trading_until:
                logger.warning(f"Sinyal {sym} diabaikan. Cooldown hingga {state.stop_trading_until.isoformat()}.")
                signal_queue.task_done()
                continue
                
            direction = "BULLISH" if "LONG" in bias else "BEARISH"

            # ── Bug fix ⑦: gunakan L8 sentiment dari signal dict ────────
            # Sebelumnya hardcoded 50 sehingga risk multiplier selalu netral.
            # L8 = 0 (tidak konfirmasi) → 40, L8 = 1 (konfirmasi) → 75
            _l8 = signal.get("sent_score", 0)
            sent_score_100 = 75 if _l8 >= 1 else 40
            
            # Risk Management
            entry = exec_data.get("entry", 0)
            sl    = exec_data.get("sl",    0)
            tp1   = exec_data.get("tp", {}).get("TP1", 0)
            tp2   = exec_data.get("tp", {}).get("TP2", 0)
            
            if entry == 0 or sl == 0:
                signal_queue.task_done()
                continue
                
            risk_plan = calculate_position_size(state.current_equity, entry, sl, sent_score_100)
            
            # Notifikasi Telegram
            if TELEGRAM_CHAT_ID:
                await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=report)
                
            if risk_plan["approved"]:
                open_position(
                    sym, 
                    "LONG" if direction == "BULLISH" else "SHORT",
                    entry, sl, tp1, tp2,
                    risk_plan["position_usd"],
                    risk_plan["quantity"]
                )
                if TELEGRAM_CHAT_ID:
                    exec_msg = (
                        f"⚡ <b>EXECUTED (Paper Trade)</b> ⚡\n"
                        f"Pair: {sym}\n"
                        f"Direction: {direction}\n"
                        f"Size: ${risk_plan['position_usd']} ({risk_plan['quantity']:.4f} coins)\n"
                        f"Risk (1%): ${risk_plan['base_risk_usd']}\n"
                        f"Sent Multiplier: {risk_plan['sent_factor']}x"
                    )
                    await app.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=exec_msg,
                        parse_mode="HTML"
                    )
            else:
                if TELEGRAM_CHAT_ID:
                    rej_msg = (
                        f"🚫 <b>TRADE REJECTED</b> 🚫\n"
                        f"Pair: {sym}\nAlasan: {risk_plan.get('reason')}"
                    )
                    await app.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=rej_msg,
                        parse_mode="HTML"
                    )
            
            signal_queue.task_done()
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error di execution loop: {e}", exc_info=True)

async def daily_reset_loop():
    """Reset circuit breaker setiap jam 00:00 UTC."""
    from datetime import timedelta
    while True:
        now      = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        await asyncio.sleep((tomorrow - now).total_seconds())
        reset_daily_equity()
        purge_sentiment_cache()   # bersihkan entri cache yang kadaluarsa
        logger.info("Daily reset: equity + sentiment cache purged.")

async def post_init(app):
    """Fungsi yang dijalankan saat bot mulai."""
    logger.info("Inisialisasi background tasks...")
    app.create_task(scanner_loop(app))
    app.create_task(execution_loop(app))
    app.create_task(daily_reset_loop())
    await bot.post_init(app)

async def post_shutdown(app):
    """Tutup semua koneksi saat bot berhenti."""
    for analyzer in (_scanner_analyzer, _eval_analyzer):
        if analyzer is not None:
            try:
                await analyzer.close()
            except Exception:
                pass
    logger.info("Semua koneksi analyzer ditutup.")

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN belum diset di .env")
        return

    logger.info("Memulai aplikasi...")
    
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)   # graceful cleanup
        .build()
    )
    
    from telegram.ext import CallbackQueryHandler, CommandHandler
    app.add_handler(bot.conv)
    app.add_handler(CallbackQueryHandler(bot.handle_callback))
    app.add_handler(CommandHandler("start",     bot.cmd_start))
    app.add_handler(CommandHandler("analyze",   bot.cmd_analyze))
    app.add_handler(CommandHandler("backtest",  bot.cmd_backtest))
    app.add_handler(CommandHandler("closepapr", bot.cmd_closepapr))
    
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

