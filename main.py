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
            
            logger.info("Mulai siklus scan market...")
            
            candidates = await _scanner_analyzer.scan_market_by_volume(
                min_vol=10_000_000,
                max_symbols=max_results,
            )
            
            for sym, vol in candidates:
                logger.info(f"Scanning {sym}...")
                res = await _scanner_analyzer.quick_scan(sym)
                if not res:
                    continue

                # quick_scan always returns 7-tuple; SKIP sentinel has score=0
                report, bias, score, ticker, tech, fund, bd = res

                # HIGH CONVICTION only — use exported constant, not magic number
                if bias != "SKIP" and score >= HIGH_THRESH:
                    exec_data = tech.get("execution", {})
                    await signal_queue.put({
                        "symbol":    sym,
                        "bias":      bias,
                        "score":     score,
                        "exec_data": exec_data,
                        "tech":      tech,
                        "report":    report,
                    })
            
            logger.info(f"Siklus selesai. Menunggu {interval_hours} jam...")
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
                    current_prices = {}
                    for sym in open_syms:
                        try:
                            ohlcv = await _eval_analyzer.binance.fetch_ohlcv(
                                f"{sym}/USDT", "15m", limit=1
                            )
                            if ohlcv:
                                current_prices[sym] = {
                                    "high":  ohlcv[-1][2],
                                    "low":   ohlcv[-1][3],
                                    "close": ohlcv[-1][4],
                                }
                        except Exception:
                            pass
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
                
            # Hitung sentiment score untuk multiplier
            direction    = "BULLISH" if "LONG" in bias else "BEARISH"
            # exec_data sudah mengandung entry/sl/tp dari technical engine
            sent_score_100 = 50  # netral default; sentiment pre-computed saat scan
            
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

