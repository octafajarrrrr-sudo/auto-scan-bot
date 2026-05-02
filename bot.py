"""
Crypto Intraday Bot — Telegram Interface
MTF: H4(bias) → H1(structure) → M15(entry)
Commands: analyze, scan, scanall, paper, backtest, settings, +config commands
"""
import logging, asyncio, json, os, threading
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from analyzer import CryptoBiasAnalyzer
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from settings import load as load_cfg, set_val, summary as settings_summary, reset as settings_reset
from backtest import (run_backtest, format_backtest_report,
                      open_paper_trade, get_open_paper_trades,
                      get_paper_stats, check_paper_trades, close_paper_trade)
from journal import get_stats

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

analyzer = CryptoBiasAnalyzer()

# ── Auth ──────────────────────────────────────────────────────────────────────
def is_authorized(update: Update) -> bool:
    return str(update.effective_user.id) == str(TELEGRAM_CHAT_ID)

async def unauthorized(update: Update):
    await update.message.reply_text("⛔ Akses ditolak.")

# ── Telegram helpers ──────────────────────────────────────────────────────────
async def send_long(bot, chat_id, text, parse_mode="Markdown"):
    """Kirim pesan panjang dengan auto-split."""
    if len(text) <= 4000:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        return
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id=chat_id, text=text[i:i+4000], parse_mode=parse_mode)
        await asyncio.sleep(0.5)

# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    cfg = load_cfg()
    msg = (
        "⚡ *Crypto Intraday Bot*\n"
        f"Mode: `{cfg['mode']}` | TF: `{cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}`\n\n"
        "*📊 Analisa*\n"
        "`/analyze <sym>` — Full report 1 koin\n"
        "`/scan`          — Scan market, kirim top sinyal\n"
        "`/scanall`       — Scan + semua laporan\n\n"
        "*📝 Paper Trade*\n"
        "`/paper`         — Lihat open paper trades\n"
        "`/paperstats`    — Statistik paper trade\n"
        "`/closepapr <id> <harga>` — Tutup paper trade manual\n\n"
        "*🔬 Backtest*\n"
        "`/backtest <sym>` — Backtest koin pada data historis\n\n"
        "*⚙️ Settings*\n"
        "`/settings`       — Lihat semua setting\n"
        "`/setmcap <juta>` — Min MCap (contoh: /setmcap 100)\n"
        "`/setscore <n>`   — Min score sinyal (contoh: /setscore 8)\n"
        "`/settf <b> <s> <e>` — Set timeframe bias/struktur/entry\n"
        "                    (contoh: /settf 4h 1h 15m)\n"
        "`/setmode intraday|swing` — Ubah mode trading\n"
        "`/settopn <n>`    — Jumlah sinyal top yang dikirim\n"
        "`/setinterval <jam>` — Interval auto-scan (jam)\n"
        "`/resetset`       — Reset semua ke default\n\n"
        "*ℹ️ Info*\n"
        "`/status`         — Status bot & API\n"
        "`/stats`          — Statistik journal sinyal\n"
        "`/list`           — Watchlist\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# ── Settings commands ──────────────────────────────────────────────────────────

async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    await update.message.reply_text(settings_summary(), parse_mode="Markdown")

async def cmd_setmcap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/setmcap 100` (artinya $100 juta)", parse_mode="Markdown")
    try:
        val = float(ctx.args[0]) * 1_000_000
        set_val("min_mcap_usd", val)
        await update.message.reply_text(f"✅ Min MCap diubah ke `${val/1e6:.0f}M`", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Angka tidak valid.")

async def cmd_setscore(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/setscore 8`", parse_mode="Markdown")
    try:
        val = int(ctx.args[0])
        if not 1 <= val <= 12:
            raise ValueError
        set_val("min_score", val)
        await update.message.reply_text(f"✅ Min score diubah ke `{val}/12`", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Angka harus antara 1-12.")

async def cmd_settf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    valid_tfs = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w"}
    if len(ctx.args) < 3:
        return await update.message.reply_text(
            "Format: `/settf 4h 1h 15m`\n(bias struktur entry)", parse_mode="Markdown")
    b, s, e = ctx.args[0].lower(), ctx.args[1].lower(), ctx.args[2].lower()
    if not all(tf in valid_tfs for tf in [b, s, e]):
        return await update.message.reply_text(f"❌ TF tidak valid. Pilih dari: {', '.join(sorted(valid_tfs))}")
    set_val("tf_bias", b); set_val("tf_structure", s); set_val("tf_entry", e)
    await update.message.reply_text(
        f"✅ Timeframe diubah:\n  Bias: `{b}` | Struktur: `{s}` | Entry: `{e}`",
        parse_mode="Markdown")

async def cmd_setmode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if not ctx.args or ctx.args[0] not in ("intraday", "swing"):
        return await update.message.reply_text("Format: `/setmode intraday` atau `/setmode swing`", parse_mode="Markdown")
    mode = ctx.args[0]
    set_val("mode", mode)
    # Preset timeframes sesuai mode
    if mode == "intraday":
        set_val("tf_bias","4h"); set_val("tf_structure","1h"); set_val("tf_entry","15m")
        set_val("tf_regime","1d"); set_val("min_mcap_usd", 50_000_000)
    else:
        set_val("tf_bias","1d"); set_val("tf_structure","4h"); set_val("tf_entry","1h")
        set_val("tf_regime","1w"); set_val("min_mcap_usd", 100_000_000)
    await update.message.reply_text(
        f"✅ Mode diubah ke `{mode}` + preset TF diterapkan.\n"
        f"Cek: /settings", parse_mode="Markdown")

async def cmd_settopn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/settopn 3`", parse_mode="Markdown")
    try:
        val = int(ctx.args[0])
        if not 1 <= val <= 10: raise ValueError
        set_val("top_n_signals", val)
        await update.message.reply_text(f"✅ Top N sinyal diubah ke `{val}`", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Angka harus 1-10.")

async def cmd_setinterval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/setinterval 4` (setiap 4 jam)", parse_mode="Markdown")
    try:
        val = int(ctx.args[0])
        if not 1 <= val <= 24: raise ValueError
        set_val("scan_interval_h", val)
        await update.message.reply_text(
            f"✅ Interval auto-scan: `setiap {val} jam`\n"
            f"⚠️ Restart bot agar perubahan berlaku.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Angka harus 1-24.")

async def cmd_resetset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    settings_reset()
    await update.message.reply_text("✅ Semua setting direset ke default.\n\n" + settings_summary(), parse_mode="Markdown")

# ── Analysis commands ──────────────────────────────────────────────────────────

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/analyze SOL`", parse_mode="Markdown")
    sym = ctx.args[0].upper()
    await update.message.reply_text(f"🔍 Menganalisis `{sym}`...", parse_mode="Markdown")
    try:
        report = analyzer.analyze(sym)
        await send_long(ctx.bot, update.effective_chat.id, report)
    except Exception as e:
        logger.error(f"analyze error: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def _run_scan(bot, chat_id: str, full: bool = False):
    """Core scan logic — dipakai oleh cmd_scan, cmd_scanall, dan auto-scan."""
    cfg = load_cfg()
    min_mcap = cfg["min_mcap_usd"]
    top_n    = cfg["top_n_signals"]
    min_score= cfg["min_score"]

    await bot.send_message(chat_id=chat_id,
        text=f"🔍 Scan market (min MCap ${min_mcap/1e6:.0f}M)...",
        parse_mode="Markdown")

    try:
        market = analyzer.scan_market_by_mcap(min_mcap)
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"❌ Gagal fetch market: {e}")
        return

    if not market:
        await bot.send_message(chat_id=chat_id, text="❌ Tidak ada koin yang lolos filter MCap.")
        return

    await bot.send_message(chat_id=chat_id,
        text=f"📋 {len(market)} koin lolos filter → mulai analisis...")

    candidates = []
    for sym, mcap in market:
        try:
            report, direction, score = analyzer.quick_scan(sym)
            if report and direction != "SKIP" and score >= min_score:
                candidates.append((score, sym, report, direction))
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"scan skip {sym}: {e}")

    candidates.sort(key=lambda x: x[0], reverse=True)

    # Log scan run
    try:
        from journal import log_scan_run
        log_scan_run(len(market), len(candidates), [c[1] for c in candidates[:top_n]])
    except Exception:
        pass

    if not candidates:
        await bot.send_message(chat_id=chat_id,
            text="😴 Tidak ada sinyal yang memenuhi threshold.", parse_mode="Markdown")
        return

    top = candidates if full else candidates[:top_n]
    await bot.send_message(chat_id=chat_id,
        text=f"🏆 *{len(candidates)} sinyal ditemukan — menampilkan {'semua' if full else f'top {len(top)}'}:*",
        parse_mode="Markdown")

    for score, sym, report, direction in top:
        # Kirim compact signal card
        ticker = analyzer.get_binance_ticker(sym)
        tech   = analyzer.tech.full_analysis(sym,
                     htf=cfg["tf_bias"], mtf=cfg["tf_structure"],
                     ltf=cfg["tf_entry"], regime_tf=cfg["tf_regime"])
        fund   = analyzer.get_fundamental_data(sym)

        if ticker and tech:
            long_s, bd = analyzer._compute_score(
                "BULLISH" if "LONG" in direction else "BEARISH",
                tech, fund, {**ticker,"symbol":sym},
                {}, {}
            )
            card = analyzer.format_signal(sym, direction, score,
                f"✅ HIGH ({score}/12)", ticker, tech, bd, fund)
            await send_long(bot, chat_id, card)
        else:
            await send_long(bot, chat_id, report)

        # Auto open paper trade
        if tech and tech["execution"]["entry_mode"] != "NO TRADE":
            e = tech["execution"]; tp = e["tp"]
            try:
                tid = open_paper_trade(
                    sym, direction, e["entry"], e["sl"],
                    tp["TP1"], tp["TP2"], tp["TP3"],
                    e.get("sl_pct",0), e.get("rr","N/A"), score)
                await bot.send_message(chat_id=chat_id,
                    text=f"📝 Paper trade dibuka `#{tid}` — {sym} {direction}",
                    parse_mode="Markdown")
            except Exception:
                pass
        await asyncio.sleep(1.5)

    await bot.send_message(chat_id=chat_id,
        text=f"✅ Scan selesai. {len(candidates)} sinyal ditemukan.",
        parse_mode="Markdown")

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    await _run_scan(ctx.bot, update.effective_chat.id, full=False)

async def cmd_scanall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    await _run_scan(ctx.bot, update.effective_chat.id, full=True)

# ── Paper trade commands ───────────────────────────────────────────────────────

async def cmd_paper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    trades = get_open_paper_trades()
    if not trades:
        return await update.message.reply_text("📝 Tidak ada paper trade yang terbuka.")
    lines = ["📝 *Open Paper Trades:*\n"]
    for t in trades:
        d = "🟢" if t["bias"]=="LONG" else "🔴"
        lines.append(
            f"{d} `#{t['id']}` *{t['symbol']}* {t['bias']}\n"
            f"   Entry:`{t['entry']:.5f}` SL:`{t['sl']:.5f}` TP1:`{t['tp1']:.5f}`\n"
            f"   RR:{t['rr']} Score:{t['score']}/12"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_paperstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    s = get_paper_stats()
    msg = (
        f"📊 *Paper Trade Stats*\n\n"
        f"Open     : `{s['open']}`\n"
        f"Closed   : `{s['total']}`\n"
        f"Win/Loss : `{s['wins']}/{s['losses']}`\n"
        f"Winrate  : `{s['winrate']}%`\n"
        f"Avg PnL  : `{s['avg_pnl']:+.2f}%`\n"
        f"Avg R    : `{s['avg_r']:+.2f}R`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_closepapr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if len(ctx.args) < 2:
        return await update.message.reply_text("Format: `/closepapr 3 98000.5`", parse_mode="Markdown")
    try:
        tid  = int(ctx.args[0])
        exit_px = float(ctx.args[1])
        result = close_paper_trade(tid, exit_px, "MANUAL")
        if not result:
            return await update.message.reply_text(f"❌ Trade #{tid} tidak ditemukan.")
        emoji = "✅" if result["pnl_pct"] > 0 else "❌"
        await update.message.reply_text(
            f"{emoji} Paper trade `#{tid}` ditutup\n"
            f"PnL: `{result['pnl_pct']:+.2f}%` ({result['pnl_r']:+.2f}R)",
            parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Format salah.")

# ── Backtest ───────────────────────────────────────────────────────────────────

async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/backtest SOL`", parse_mode="Markdown")
    sym = ctx.args[0].upper()
    cfg = load_cfg()
    await update.message.reply_text(f"🔬 Backtest `{sym}` ({cfg['tf_bias']}/{cfg['tf_entry']})...", parse_mode="Markdown")
    try:
        result = run_backtest(analyzer.tech, sym, cfg["tf_bias"], cfg["tf_entry"])
        report = format_backtest_report(result, sym)
        await send_long(ctx.bot, update.effective_chat.id, report)
    except Exception as e:
        await update.message.reply_text(f"❌ Backtest error: {e}")

# ── Info commands ──────────────────────────────────────────────────────────────

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    try:
        analyzer.binance.fetch_ticker("BTC/USDT")
        binance_ok = "✅"
    except Exception as e:
        binance_ok = f"❌ ({str(e)[:30]})"
    try:
        import requests as _r
        _r.get("https://api.coingecko.com/api/v3/ping", timeout=4)
        cg_ok = "✅"
    except Exception:
        cg_ok = "❌"

    cfg = load_cfg()
    msg = (
        f"🟢 *Bot Online*\n\n"
        f"Binance   : {binance_ok}\n"
        f"CoinGecko : {cg_ok}\n"
        f"Mode      : `{cfg['mode']}`\n"
        f"TF        : `{cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}`\n"
        f"Min MCap  : `${cfg['min_mcap_usd']/1e6:.0f}M`\n"
        f"Auto-Scan : `{cfg['scan_interval_h']}h`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    s = get_stats()
    ps = get_paper_stats()
    msg = (
        f"📊 *Signal Journal Stats*\n\n"
        f"*Logged Signals*\n"
        f"Closed  : `{s['total_closed']}`\n"
        f"Win/Loss: `{s.get('wins',0)}/{s.get('losses',0)}`\n"
        f"Winrate : `{s['winrate_pct']}%`\n"
        f"Avg PnL : `{s['avg_pnl_pct']:+.2f}%`\n"
        f"Running : `{s['running_positions']}`\n\n"
        f"*Paper Trade*\n"
        f"Open    : `{ps['open']}`\n"
        f"Winrate : `{ps['winrate']}%`\n"
        f"Avg PnL : `{ps['avg_pnl']:+.2f}%`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return await unauthorized(update)
    cfg = load_cfg()
    await update.message.reply_text(
        f"🔍 Mode: `{cfg['mode']}`\n"
        f"Scan: semua koin Binance dengan MCap ≥ `${cfg['min_mcap_usd']/1e6:.0f}M`\n"
        f"Top N sinyal: `{cfg['top_n_signals']}`\n\n"
        f"Tidak ada watchlist manual — scan otomatis dari market.",
        parse_mode="Markdown")

# ── Auto-scan job ──────────────────────────────────────────────────────────────

async def auto_scan_job(ctx: ContextTypes.DEFAULT_TYPE):
    cfg = load_cfg()
    chat_id = TELEGRAM_CHAT_ID
    await ctx.bot.send_message(chat_id=chat_id,
        text="📢 *AUTO-SCAN DIMULAI*", parse_mode="Markdown")

    # Cek paper trades dulu
    events = check_paper_trades(analyzer.binance)
    for ev in events:
        emoji = "✅" if "WIN" in ev["hit_level"] else "❌"
        await ctx.bot.send_message(chat_id=chat_id,
            text=f"{emoji} Paper `#{ev['trade_id']}` {ev['symbol']} → "
                 f"`{ev['hit_level']}` PnL:`{ev['pnl_pct']:+.2f}%`",
            parse_mode="Markdown")

    await _run_scan(ctx.bot, chat_id, full=False)

# ── post_init & main ───────────────────────────────────────────────────────────

async def post_init(app):
    commands = [
        BotCommand("start",       "Menu utama"),
        BotCommand("analyze",     "Analisa 1 koin: /analyze SOL"),
        BotCommand("scan",        "Scan market → top sinyal"),
        BotCommand("scanall",     "Scan market → semua sinyal"),
        BotCommand("paper",       "Open paper trades"),
        BotCommand("paperstats",  "Statistik paper trade"),
        BotCommand("closepapr",   "Tutup paper trade: /closepapr 3 98000"),
        BotCommand("backtest",    "Backtest: /backtest SOL"),
        BotCommand("settings",    "Lihat semua setting"),
        BotCommand("setmcap",     "Set min MCap: /setmcap 100"),
        BotCommand("setscore",    "Set min score: /setscore 8"),
        BotCommand("settf",       "Set timeframe: /settf 4h 1h 15m"),
        BotCommand("setmode",     "Set mode: /setmode intraday"),
        BotCommand("settopn",     "Set top N: /settopn 3"),
        BotCommand("setinterval", "Set scan interval: /setinterval 4"),
        BotCommand("resetset",    "Reset settings ke default"),
        BotCommand("status",      "Status bot & API"),
        BotCommand("stats",       "Statistik sinyal & paper trade"),
        BotCommand("list",        "Info scan market"),
    ]
    await app.bot.set_my_commands(commands)

if __name__ == "__main__":
    cfg = load_cfg()
    interval_sec = cfg["scan_interval_h"] * 3600

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.job_queue.run_repeating(auto_scan_job, interval=interval_sec, first=30)

    for cmd, fn in [
        ("start",       cmd_start),
        ("analyze",     cmd_analyze),
        ("scan",        cmd_scan),
        ("scanall",     cmd_scanall),
        ("paper",       cmd_paper),
        ("paperstats",  cmd_paperstats),
        ("closepapr",   cmd_closepapr),
        ("backtest",    cmd_backtest),
        ("settings",    cmd_settings),
        ("setmcap",     cmd_setmcap),
        ("setscore",    cmd_setscore),
        ("settf",       cmd_settf),
        ("setmode",     cmd_setmode),
        ("settopn",     cmd_settopn),
        ("setinterval", cmd_setinterval),
        ("resetset",    cmd_resetset),
        ("status",      cmd_status),
        ("stats",       cmd_stats),
        ("list",        cmd_list),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    logger.info(f"Bot running | Mode:{cfg['mode']} TF:{cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}")
    app.run_polling(drop_pending_updates=True)
