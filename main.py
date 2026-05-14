"""
MAIN — Entry Point & Orchestrator (PTB v21.10+)
- Auto-scan berkala sesuai pengaturan Telegram
- Output sinyal seragam dengan scan manual (laporan lengkap)
- Execution loop untuk paper trade & circuit breaker
- Daily reset equity & cache
"""

import asyncio
import logging
from datetime import datetime, timezone
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from settings import load as cfg_load
import bot
from analyzer import CryptoBiasAnalyzer
from sentiment import purge_cache as purge_sentiment_cache
from risk_manager import calculate_position_size, state, reset_daily_equity
from paper_trader import open_position, evaluate_positions, _conn as paper_conn
from journal import log_scan_run

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO   # bisa diubah ke DEBUG jika perlu
)
logger = logging.getLogger(__name__)

# ── Shared Resources ─────────────────────────────────────────────────────────
signal_queue = asyncio.Queue()
_scanner_analyzer = None
_eval_analyzer = None


# ══════════════════════════════════════════════════════════════════════════════
# SCANNER LOOP — Periodic Auto-Scan
# ══════════════════════════════════════════════════════════════════════════════
async def scanner_loop(app):
    global _scanner_analyzer
    logger.info("Scanner loop started.")
    await asyncio.sleep(5)  # beri waktu bot siap
    _scanner_analyzer = CryptoBiasAnalyzer()

    while True:
        try:
            # ── Baca pengaturan terkini ───────────────────────────────────────
            cfg = cfg_load()
            interval_h = cfg.get("scan_interval_h", 4)
            interval_s = max(int(interval_h * 3600), 60)      # minimal 1 menit
            max_symbols = cfg.get("max_results", 100)         # koin yang dipindai
            top_n = cfg.get("top_n_signals", 5)               # batas kirim sinyal
            min_vol = cfg.get("min_mcap_usd", 5_000_000)      # filter volume (MCap)
            min_score = cfg.get("min_score", 1)               # threshold skor

            _tg = app.bot if TELEGRAM_CHAT_ID else None

            # helper kirim notifikasi
            async def _tg_send(text: str):
                if _tg:
                    try:
                        await _tg.send_message(
                            chat_id=TELEGRAM_CHAT_ID, text=text,
                            parse_mode="HTML", disable_web_page_preview=True
                        )
                    except Exception as e:
                        logger.warning(f"TG notify failed: {e}")

            await _tg_send(
                f"🔍 <b>Auto-Scan Dimulai</b>\n"
                f"Interval: {interval_h} jam · Volume ≥ ${min_vol/1e6:.0f}M · Max sinyal: {top_n}"
            )

            # ── Ambil kandidat ────────────────────────────────────────────────
            candidates = await _scanner_analyzer.scan_market_by_volume(
                min_vol=min_vol,
                max_symbols=max_symbols
            )

            if not candidates:
                await _tg_send("❌ Tidak ada koin yang lolos filter volume.")
                logger.info("Siklus selesai: tidak ada kandidat.")
                await asyncio.sleep(interval_s)
                continue

            await _tg_send(f"✅ {len(candidates)} koin lolos → analisis...")

            # ── Concurrent scan ────────────────────────────────────────────────
            sem = asyncio.Semaphore(10)

            async def _scan_one(sym: str):
                async with sem:
                    try:
                        res = await _scanner_analyzer.quick_scan(sym)
                        return sym, res
                    except Exception as e:
                        logger.error(f"quick_scan {sym}: {e}")
                        return sym, None

            tasks = [_scan_one(sym) for sym, _ in candidates]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # ── Kumpulkan sinyal valid ─────────────────────────────────────────
            signal_list = []

            for item in results:
                if isinstance(item, Exception):
                    logger.error(f"Scan task error: {item}")
                    continue
                sym, res = item
                if not res:
                    continue

                report, bias, score, ticker, tech, fund, bd = res
                if bias != "SKIP" and score >= min_score:
                    signal_list.append(
                        (score, sym, bias, report, ticker, tech, fund, bd)
                    )

            # Urutkan skor tertinggi & ambil top_n
            signal_list.sort(key=lambda x: x[0], reverse=True)
            top_signals = signal_list[:top_n]

            # ── Kirim sinyal + notifikasi ──────────────────────────────────────
            for score, sym, bias, report, ticker, tech, fund, bd in top_signals:
                exec_data = tech.get("execution", {})
                # Masukkan ke queue untuk execution loop
                await signal_queue.put({
                    "symbol": sym,
                    "bias": bias,
                    "score": score,
                    "sent_score": bd.get("L8_sentiment", 0),
                    "exec_data": exec_data,
                    "tech": tech,
                    "report": report
                })
                # Kirim laporan lengkap (seperti scan manual)
                card = _scanner_analyzer.format_signal(
                    sym, bias, score,
                    f"✅ AUTO ({score}/14)",
                    ticker, tech, bd, fund
                )
                await _tg_send(card)

            # ── Catat ke database ──────────────────────────────────────────────
            signals_found = len(top_signals)
            top_symbols = [s[1] for s in top_signals]
            try:
                log_scan_run(
                    total_scanned=len(candidates),
                    signals_found=signals_found,
                    top2=top_symbols
                )
            except Exception as e:
                logger.debug(f"log_scan_run error: {e}")

            # ── Ringkasan akhir ────────────────────────────────────────────────
            if signals_found == 0:
                summary = (
                    f"😴 <b>Scan Selesai</b> — tidak ada sinyal\n"
                    f"Dipindai: {len(candidates)} koin · "
                    f"Threshold score ≥ {min_score} · Next: {interval_h} jam"
                )
            else:
                syms_str = " · ".join(top_symbols[:10])
                summary = (
                    f"✅ <b>Scan Selesai</b> — {signals_found} sinyal\n"
                    f"Simbol: <code>{syms_str}</code>\n"
                    f"Next scan: {interval_h} jam lagi"
                )
            await _tg_send(summary)

            logger.info(
                f"Siklus selesai. {signals_found} sinyal. Menunggu {interval_h} jam..."
            )
            await asyncio.sleep(interval_s)

        except asyncio.CancelledError:
            logger.info("Scanner loop dihentikan (shutdown).")
            break
        except Exception as e:
            logger.error(f"Error scanner loop: {e}", exc_info=True)
            await asyncio.sleep(60)  # tunggu sebelum retry


# ══════════════════════════════════════════════════════════════════════════════
# EXECUTION LOOP — Proses sinyal & evaluasi posisi
# ══════════════════════════════════════════════════════════════════════════════
async def execution_loop(app):
    global _eval_analyzer
    logger.info("Execution loop started.")
    _eval_analyzer = CryptoBiasAnalyzer()

    while True:
        try:
            # ── Evaluasi posisi terbuka ────────────────────────────────────────
            try:
                with paper_conn() as conn:
                    open_syms = [
                        r[0] for r in conn.execute(
                            "SELECT DISTINCT symbol FROM positions WHERE status='OPEN'"
                        ).fetchall()
                    ]

                if open_syms:
                    async def _fetch_price(s: str):
                        try:
                            ohlcv = await _eval_analyzer.binance.fetch_ohlcv(
                                f"{s}/USDT", "15m", limit=1
                            )
                            if ohlcv:
                                return s, {
                                    "high": ohlcv[-1][2],
                                    "low": ohlcv[-1][3],
                                    "close": ohlcv[-1][4]
                                }
                        except Exception:
                            pass
                        return s, None

                    fetched = await asyncio.gather(
                        *[_fetch_price(s) for s in open_syms],
                        return_exceptions=True
                    )
                    current_prices = {
                        s: p
                        for item in fetched
                        if not isinstance(item, Exception)
                        for s, p in [item]
                        if p
                    }
                    if current_prices:
                        evaluate_positions(current_prices)
            except Exception as e:
                logger.debug(f"Position eval skipped: {e}")

            # ── Ambil sinyal dari queue ────────────────────────────────────────
            try:
                signal = await asyncio.wait_for(signal_queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                continue

            sym = signal["symbol"]
            bias = signal["bias"]
            exec_data = signal["exec_data"]
            report = signal["report"]

            logger.info(f"Memproses sinyal: {sym} ({bias})")

            # Blokir jika mode NO TRADE atau BLOCKED
            entry_mode = exec_data.get("entry_mode", "")
            if "NO TRADE" in entry_mode or "BLOCKED" in entry_mode:
                logger.warning(f"Sinyal {sym} diabaikan — {entry_mode}")
                signal_queue.task_done()
                continue

            # Circuit breaker
            if state.stop_trading_global:
                logger.warning(f"Sinyal {sym} diabaikan — Global Stop.")
                signal_queue.task_done()
                continue
            if state.stop_trading_until and \
               datetime.now(timezone.utc) < state.stop_trading_until:
                logger.warning(f"Sinyal {sym} diabaikan — Cooldown.")
                signal_queue.task_done()
                continue

            # ── Siapkan data eksekusi ───────────────────────────────────────────
            direction = "BULLISH" if "LONG" in bias else "BEARISH"
            sent_score = signal.get("sent_score", 0)
            sent_multiplier = 75 if sent_score >= 1 else 40

            entry = exec_data.get("entry", 0)
            sl = exec_data.get("sl", 0)
            tp = exec_data.get("tp", {})
            tp1 = tp.get("TP1", 0)
            tp2 = tp.get("TP2", 0)

            if entry == 0 or sl == 0:
                logger.warning(f"Sinyal {sym} tidak memiliki entry/SL valid.")
                signal_queue.task_done()
                continue

            # ── Hitung ukuran posisi ───────────────────────────────────────────
            risk_plan = calculate_position_size(
                state.current_equity, entry, sl, sent_multiplier
            )

            # Kirim laporan panjang ke Telegram (dari quick_scan)
            if TELEGRAM_CHAT_ID:
                await app.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID, text=report
                )

            # Eksekusi atau reject
            if risk_plan.get("approved"):
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
                        f"Size: ${risk_plan['position_usd']} "
                        f"({risk_plan['quantity']:.4f} unit)\n"
                        f"Risk (1%): ${risk_plan['base_risk_usd']}\n"
                        f"Sent Multiplier: {risk_plan['sent_factor']}x"
                    )
                    await app.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID, text=exec_msg,
                        parse_mode="HTML"
                    )
            else:
                if TELEGRAM_CHAT_ID:
                    rej_msg = (
                        f"🚫 <b>TRADE REJECTED</b> 🚫\n"
                        f"Pair: {sym}\n"
                        f"Alasan: {risk_plan.get('reason', 'Tidak diketahui')}"
                    )
                    await app.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID, text=rej_msg,
                        parse_mode="HTML"
                    )

            signal_queue.task_done()

        except asyncio.CancelledError:
            logger.info("Execution loop dihentikan.")
            break
        except Exception as e:
            logger.error(f"Error execution loop: {e}", exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
# DAILY RESET
# ══════════════════════════════════════════════════════════════════════════════
async def daily_reset_loop():
    from datetime import timedelta
    while True:
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        await asyncio.sleep((tomorrow - now).total_seconds())
        reset_daily_equity()
        purge_sentiment_cache()
        logger.info("Daily reset: equity + cache cleared.")


# ══════════════════════════════════════════════════════════════════════════════
# POST INIT / POST SHUTDOWN
# ══════════════════════════════════════════════════════════════════════════════
async def post_init(app):
    """Jadwalkan background tasks setelah bot siap."""
    await bot.post_init(app)                  # daftarkan command bot
    app.create_task(scanner_loop(app))
    app.create_task(execution_loop(app))
    app.create_task(daily_reset_loop())
    logger.info("Background tasks dijadwalkan.")


async def post_shutdown(app):
    """Bersihkan resource analyzer."""
    for obj in (_scanner_analyzer, _eval_analyzer):
        if obj is not None:
            try:
                await obj.close()
            except Exception:
                pass
    logger.info("Analyzer ditutup.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN tidak diset di .env")
        return

    logger.info("Memulai aplikasi...")
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Handler dari bot.py
    app.add_handler(bot.conv)
    app.add_handler(CallbackQueryHandler(bot.handle_callback))
    app.add_handler(CommandHandler("start", bot.cmd_start))
    app.add_handler(CommandHandler("analyze", bot.cmd_analyze))
    app.add_handler(CommandHandler("backtest", bot.cmd_backtest))
    app.add_handler(CommandHandler("closepapr", bot.cmd_closepapr))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
