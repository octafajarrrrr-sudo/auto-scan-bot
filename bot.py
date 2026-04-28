"""
Crypto Bias Bot — Telegram Interface
Auto-scan 3x/day + Manual commands + Dynamic watchlist
"""
import logging
import asyncio
import json
import os
import threading
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from analyzer import CryptoBiasAnalyzer
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

WATCHLIST_FILE = "watchlist.json"
DEFAULT_WATCHLIST = {
    "MAJOR": ["BTC", "ETH", "SOL", "BNB"],
    "MIDCAP": ["ONDO", "PENDLE", "RNDR", "TIA", "INJ", "SUI", "SEI"],
    "LOWCAP": ["PEPE", "WIF", "BRETT", "BOME", "TURBO", "FLOKI"]
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r") as f:
            return json.load(f)
    return DEFAULT_WATCHLIST.copy()


def save_watchlist(wl):
    with WATCHLIST_LOCK:
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(wl, f, indent=2)


WATCHLIST = load_watchlist()
WATCHLIST_LOCK = threading.Lock()
analyzer = CryptoBiasAnalyzer()


def is_authorized(update: Update) -> bool:
    """Cek apakah pengirim adalah pemilik bot (TELEGRAM_CHAT_ID)."""
    return str(update.effective_user.id) == str(TELEGRAM_CHAT_ID)


async def unauthorized(update: Update):
    await update.message.reply_text("⛔ Akses ditolak.")


def flat_list():
    return [c for coins in WATCHLIST.values() for c in coins]


# ─── Command Handlers ─── #

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *Crypto Bias Bot Pro — AKTIF*\n\n"
        "━━━ *Perintah Analisa* ━━━\n"
        "▫️ /analyze `<symbol>` — Analisa lengkap 1 koin\n"
        "▫️ /scan — Scan semua watchlist (kirim sinyal kuat)\n"
        "▫️ /scanall — Scan + kirim laporan semua koin\n\n"
        "━━━ *Perintah Konfigurasi* ━━━\n"
        "▫️ /list — Lihat watchlist\n"
        "▫️ /add `<kat> <symbol>` — Tambah koin\n"
        "▫️ /remove `<symbol>` — Hapus koin\n"
        "▫️ /status — Cek status bot\n\n"
        "━━━ *Auto-Scan* ━━━\n"
        "Bot otomatis scan setiap 8 jam dan kirim sinyal *High Conviction* saja."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    total = len(flat_list())
    msg = f"📋 *Watchlist ({total} koin):*\n\n"
    for cat, coins in WATCHLIST.items():
        msg += f"*{cat}:*  " + ", ".join(coins) + "\n"
    msg += "\nGunakan /add atau /remove untuk mengubah."
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if len(ctx.args) < 2:
        await update.message.reply_text("Format: /add MAJOR BTC")
        return
    cat = ctx.args[0].upper()
    sym = ctx.args[1].upper()
    if cat not in WATCHLIST:
        await update.message.reply_text(f"Kategori '{cat}' tidak valid. Pilih: {', '.join(WATCHLIST.keys())}")
        return
    if sym in WATCHLIST[cat]:
        await update.message.reply_text(f"⚠️ {sym} sudah ada di {cat}.")
        return
    WATCHLIST[cat].append(sym)
    save_watchlist(WATCHLIST)
    await update.message.reply_text(f"✅ {sym} ditambahkan ke {cat}.")


async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if not ctx.args:
        await update.message.reply_text("Format: /remove BTC")
        return
    sym = ctx.args[0].upper()
    for cat, coins in WATCHLIST.items():
        if sym in coins:
            coins.remove(sym)
            save_watchlist(WATCHLIST)
            await update.message.reply_text(f"🗑️ {sym} dihapus dari {cat}.")
            return
    await update.message.reply_text(f"⚠️ {sym} tidak ditemukan.")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    total = len(flat_list())
    # Cek koneksi Binance secara nyata
    try:
        analyzer.binance.fetch_ticker("BTC/USDT")
        binance_status = "✅ Terhubung"
    except Exception as e:
        binance_status = f"❌ Error: {str(e)[:40]}"
    # Cek CoinGecko
    try:
        import requests as _req
        r = _req.get("https://api.coingecko.com/api/v3/ping", timeout=4)
        cg_status = "✅ Terhubung" if r.status_code == 200 else f"⚠️ HTTP {r.status_code}"
    except Exception:
        cg_status = "❌ Timeout"
    msg = (
        "🟢 *Status Bot: ONLINE*\n\n"
        f"📊 Koin Terpantau: {total}\n"
        "⚙️ API:\n"
        f"  - Binance: {binance_status}\n"
        f"  - CoinGecko: {cg_status}\n"
        "  - News Scraper: ✅ Aktif\n"
        "  - Technical Engine: ✅ ICT/SMC Active\n\n"
        "⏱️ Auto-Scan: Setiap 8 jam\n"
        "📡 Analisa: Fundamental + Technical (Multi-TF)"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if not ctx.args:
        await update.message.reply_text("Format: /analyze SOL")
        return
    sym = ctx.args[0].upper()
    await update.message.reply_text(f"🔍 Menganalisis {sym} (Fundamental + ICT/SMC)...")
    try:
        report = analyzer.analyze(sym)
        # Telegram has 4096 char limit — split if needed
        if len(report) > 4000:
            parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
            for p in parts:
                await update.message.reply_text(p)
                await asyncio.sleep(1)
        else:
            await update.message.reply_text(report)
    except Exception as e:
        logger.error(f"Analyze error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Scan watchlist — hanya kirim sinyal High Conviction."""
    if not is_authorized(update): return await unauthorized(update)
    chat_id = update.effective_chat.id
    await ctx.bot.send_message(chat_id=chat_id, text="🚀 *MEMULAI SCAN MARKET...*\nHanya mengirim sinyal High Conviction.", parse_mode="Markdown")

    found = 0
    coins = flat_list()
    for sym in coins:
        try:
            report, bias = analyzer.quick_scan(sym)
            if report and bias != "SKIP":
                if len(report) > 4000:
                    parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
                    for p in parts:
                        await ctx.bot.send_message(chat_id=chat_id, text=p)
                        await asyncio.sleep(1)
                else:
                    await ctx.bot.send_message(chat_id=chat_id, text=report)
                found += 1
                await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"Scan skip {sym}: {e}")

    if found == 0:
        await ctx.bot.send_message(chat_id=chat_id, text="😴 *Scan Selesai.* Tidak ada setup High Conviction saat ini.", parse_mode="Markdown")
    else:
        await ctx.bot.send_message(chat_id=chat_id, text=f"✅ *Scan Selesai.* Ditemukan {found} sinyal potensial.", parse_mode="Markdown")


async def cmd_scanall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Scan dan kirim laporan SEMUA koin di watchlist."""
    if not is_authorized(update): return await unauthorized(update)
    chat_id = update.effective_chat.id
    coins = flat_list()
    await ctx.bot.send_message(chat_id=chat_id, text=f"📡 *FULL SCAN {len(coins)} KOIN...*", parse_mode="Markdown")

    for sym in coins:
        try:
            report = analyzer.analyze(sym)
            if len(report) > 4000:
                parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
                for p in parts:
                    await ctx.bot.send_message(chat_id=chat_id, text=p)
                    await asyncio.sleep(1)
            else:
                await ctx.bot.send_message(chat_id=chat_id, text=report)
            await asyncio.sleep(3)
        except Exception as e:
            await ctx.bot.send_message(chat_id=chat_id, text=f"⚠️ {sym}: {e}")

    await ctx.bot.send_message(chat_id=chat_id, text="✅ *Full Scan Selesai.*", parse_mode="Markdown")


async def auto_scan_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Background job — runs every 8 hours."""
    chat_id = TELEGRAM_CHAT_ID
    await ctx.bot.send_message(chat_id=chat_id, text="📢 *AUTO-SCAN TERJADWAL DIMULAI...*", parse_mode="Markdown")

    found = 0
    for sym in flat_list():
        try:
            report, bias = analyzer.quick_scan(sym)
            if report and bias != "SKIP":
                if len(report) > 4000:
                    parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
                    for p in parts:
                        await ctx.bot.send_message(chat_id=chat_id, text=p)
                        await asyncio.sleep(1)
                else:
                    await ctx.bot.send_message(chat_id=chat_id, text=report)
                found += 1
                await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"Auto-scan skip {sym}: {e}")

    summary = f"✅ *Auto-Scan Selesai.* {found} sinyal ditemukan." if found > 0 else "😴 *Auto-Scan Selesai.* Market flat — tidak ada setup."
    await ctx.bot.send_message(chat_id=chat_id, text=summary, parse_mode="Markdown")


async def post_init(app):
    """Set bot commands menu di Telegram."""
    commands = [
        BotCommand("start", "Tampilkan menu utama"),
        BotCommand("analyze", "Analisa 1 koin (contoh: /analyze SOL)"),
        BotCommand("scan", "Scan watchlist — sinyal kuat saja"),
        BotCommand("scanall", "Full scan semua koin"),
        BotCommand("list", "Lihat watchlist"),
        BotCommand("add", "Tambah koin (contoh: /add MAJOR SOL)"),
        BotCommand("remove", "Hapus koin (contoh: /remove SOL)"),
        BotCommand("status", "Cek status bot"),
    ]
    await app.bot.set_my_commands(commands)


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Auto-scan setiap 8 jam (28800 detik), mulai 10 detik setelah start
    app.job_queue.run_repeating(auto_scan_job, interval=28800, first=10)

    # Register all handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("scanall", cmd_scanall))

    logger.info("Bot is running with ICT/SMC Technical Engine...")
    app.run_polling(drop_pending_updates=True)
