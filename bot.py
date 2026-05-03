"""
Crypto Intraday Bot — Telegram Interface
InlineKeyboard dengan sub-menu untuk semua settings.
MTF: H4(bias) -> H1(structure) -> M15(entry)
"""
import logging, asyncio, json, os
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, ContextTypes,
                          CommandHandler, CallbackQueryHandler)
from analyzer import CryptoBiasAnalyzer
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from settings import load as load_cfg, set_val, summary as settings_summary, reset as settings_reset, get as cfg_get
from backtest import (run_backtest, format_backtest_report,
                      open_paper_trade, get_open_paper_trades,
                      get_paper_stats, check_paper_trades, close_paper_trade)
from journal import get_full_stats

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
analyzer = CryptoBiasAnalyzer()

# ── Auth ─────────────────────────────────────────────────────────────────────
def auth(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    return str(uid) == str(TELEGRAM_CHAT_ID)

async def deny(update: Update):
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target:
        await target.reply_text("⛔ Akses ditolak.")

# ── Send helpers ─────────────────────────────────────────────────────────────
async def send_long(bot, chat_id, text, parse_mode="Markdown"):
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id=chat_id, text=text[i:i+4000], parse_mode=parse_mode)
        if len(text) > 4000:
            await asyncio.sleep(0.4)

async def edit_or_send(query, text, keyboard=None, parse_mode="Markdown"):
    kb = InlineKeyboardMarkup(keyboard) if keyboard else None
    try:
        await query.edit_message_text(text, reply_markup=kb, parse_mode=parse_mode)
    except Exception:
        await query.message.reply_text(text, reply_markup=kb, parse_mode=parse_mode)

# ══════════════════════════════════════════════════════════════════════════════
# INLINE KEYBOARD MENUS
# ══════════════════════════════════════════════════════════════════════════════

def kb_main():
    return [
        [InlineKeyboardButton("📊 Analisa", callback_data="menu:analyze"),
         InlineKeyboardButton("🔍 Scan", callback_data="menu:scan")],
        [InlineKeyboardButton("📝 Paper Trade", callback_data="menu:paper"),
         InlineKeyboardButton("🔬 Backtest", callback_data="menu:backtest")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="menu:settings"),
         InlineKeyboardButton("📈 Statistik", callback_data="menu:stats")],
        [InlineKeyboardButton("🟢 Status", callback_data="action:status")],
    ]

def kb_analyze():
    return [
        [InlineKeyboardButton("🔎 Analisa 1 Koin", callback_data="action:analyze_prompt")],
        [InlineKeyboardButton("🚀 Scan Market (Top N)", callback_data="action:scan")],
        [InlineKeyboardButton("📋 Scan Semua Sinyal", callback_data="action:scanall")],
        [InlineKeyboardButton("« Kembali", callback_data="menu:main")],
    ]

def kb_paper():
    return [
        [InlineKeyboardButton("👁 Lihat Open Trades", callback_data="action:paper_list")],
        [InlineKeyboardButton("📊 Statistik Paper", callback_data="action:paper_stats")],
        [InlineKeyboardButton("❌ Tutup Trade (via command)", callback_data="info:closepapr")],
        [InlineKeyboardButton("« Kembali", callback_data="menu:main")],
    ]

def kb_backtest():
    return [
        [InlineKeyboardButton("🔬 Backtest (via command)", callback_data="info:backtest")],
        [InlineKeyboardButton("« Kembali", callback_data="menu:main")],
    ]

def kb_settings():
    cfg = load_cfg()
    return [
        [InlineKeyboardButton(f"📐 Mode: {cfg['mode']}", callback_data="set:mode")],
        [InlineKeyboardButton(f"⏱ TF: {cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}", callback_data="set:tf")],
        [InlineKeyboardButton(f"💰 Min MCap: ${cfg['min_mcap_usd']/1e6:.0f}M", callback_data="set:mcap")],
        [InlineKeyboardButton(f"🎯 Min Score: {cfg['min_score']}/12", callback_data="set:score")],
        [InlineKeyboardButton(f"📤 Top N: {cfg['top_n_signals']}", callback_data="set:topn"),
         InlineKeyboardButton(f"⏰ Interval: {cfg['scan_interval_h']}h", callback_data="set:interval")],
        [InlineKeyboardButton("↩️ Reset Default", callback_data="action:resetset")],
        [InlineKeyboardButton("« Kembali", callback_data="menu:main")],
    ]

def kb_set_mode():
    cfg = load_cfg()
    return [
        [InlineKeyboardButton("⚡ Intraday (H4/H1/M15)" + (" ✓" if cfg["mode"]=="intraday" else ""),
                              callback_data="setval:mode:intraday")],
        [InlineKeyboardButton("📈 Swing (1D/4H/1H)" + (" ✓" if cfg["mode"]=="swing" else ""),
                              callback_data="setval:mode:swing")],
        [InlineKeyboardButton("« Kembali", callback_data="menu:settings")],
    ]

def kb_set_tf():
    return [
        [InlineKeyboardButton("⚡ Intraday H4/H1/M15", callback_data="setval:tf:4h:1h:15m")],
        [InlineKeyboardButton("📊 Intraday H4/H1/M5",  callback_data="setval:tf:4h:1h:5m")],
        [InlineKeyboardButton("📈 Swing 1D/4H/1H",     callback_data="setval:tf:1d:4h:1h")],
        [InlineKeyboardButton("🔭 Swing 1W/1D/4H",     callback_data="setval:tf:1w:1d:4h")],
        [InlineKeyboardButton("« Kembali", callback_data="menu:settings")],
    ]

def kb_set_mcap():
    return [
        [InlineKeyboardButton("$10M",  callback_data="setval:mcap:10"),
         InlineKeyboardButton("$30M",  callback_data="setval:mcap:30")],
        [InlineKeyboardButton("$50M",  callback_data="setval:mcap:50"),
         InlineKeyboardButton("$100M", callback_data="setval:mcap:100")],
        [InlineKeyboardButton("$300M", callback_data="setval:mcap:300"),
         InlineKeyboardButton("$500M", callback_data="setval:mcap:500")],
        [InlineKeyboardButton("« Kembali", callback_data="menu:settings")],
    ]

def kb_set_score():
    return [
        [InlineKeyboardButton(str(i), callback_data=f"setval:score:{i}")
         for i in range(5, 9)],
        [InlineKeyboardButton(str(i), callback_data=f"setval:score:{i}")
         for i in range(9, 13)],
        [InlineKeyboardButton("« Kembali", callback_data="menu:settings")],
    ]

def kb_set_topn():
    return [
        [InlineKeyboardButton(str(i), callback_data=f"setval:topn:{i}")
         for i in range(1, 6)],
        [InlineKeyboardButton("« Kembali", callback_data="menu:settings")],
    ]

def kb_set_interval():
    return [
        [InlineKeyboardButton("1h", callback_data="setval:interval:1"),
         InlineKeyboardButton("2h", callback_data="setval:interval:2"),
         InlineKeyboardButton("4h", callback_data="setval:interval:4")],
        [InlineKeyboardButton("6h", callback_data="setval:interval:6"),
         InlineKeyboardButton("8h", callback_data="setval:interval:8"),
         InlineKeyboardButton("12h", callback_data="setval:interval:12")],
        [InlineKeyboardButton("« Kembali", callback_data="menu:settings")],
    ]

# ══════════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return await deny(update)
    cfg = load_cfg()
    txt = (f"⚡ *Crypto Intraday Bot*\n"
           f"Mode: `{cfg['mode']}` | TF: `{cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}`\n"
           f"Min MCap: `${cfg['min_mcap_usd']/1e6:.0f}M` | Score: `{cfg['min_score']}/12`\n\n"
           f"Pilih menu:")
    await update.message.reply_text(
        txt, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb_main()))

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return await deny(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/analyze SOL`", parse_mode="Markdown")
    sym = ctx.args[0].upper()
    msg = await update.message.reply_text(f"🔍 Menganalisis `{sym}`...", parse_mode="Markdown")
    try:
        report = analyzer.analyze(sym)
        await send_long(ctx.bot, update.effective_chat.id, report)
    except Exception as e:
        logger.error(f"analyze {sym}: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_closepapr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return await deny(update)
    if len(ctx.args) < 2:
        return await update.message.reply_text("Format: `/closepapr 3 98000`", parse_mode="Markdown")
    try:
        tid = int(ctx.args[0]); ep = float(ctx.args[1])
        r = close_paper_trade(tid, ep, "MANUAL")
        if not r: return await update.message.reply_text(f"❌ Trade #{tid} tidak ditemukan.")
        emoji = "✅" if r["pnl_pct"] > 0 else "❌"
        await update.message.reply_text(
            f"{emoji} Paper `#{tid}` ditutup\nPnL: `{r['pnl_pct']:+.2f}%` ({r['pnl_r']:+.2f}R)",
            parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Format salah.")

async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not auth(update): return await deny(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/backtest SOL`", parse_mode="Markdown")
    sym = ctx.args[0].upper(); cfg = load_cfg()
    await update.message.reply_text(f"🔬 Backtest `{sym}`...", parse_mode="Markdown")
    try:
        r = run_backtest(analyzer.tech, sym, cfg["tf_bias"], cfg["tf_entry"])
        await send_long(ctx.bot, update.effective_chat.id, format_backtest_report(r, sym))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

# ══════════════════════════════════════════════════════════════════════════════
# CALLBACK HANDLER (semua tombol inline)
# ══════════════════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not auth(update): return await q.answer("⛔ Akses ditolak.", show_alert=True)

    data = q.data
    chat_id = q.message.chat_id

    # ── Navigation ──────────────────────────────────────────────────────────
    if data == "menu:main":
        cfg = load_cfg()
        await edit_or_send(q,
            f"⚡ *Crypto Intraday Bot*\n"
            f"Mode: `{cfg['mode']}` | TF: `{cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}`\n"
            f"Min MCap: `${cfg['min_mcap_usd']/1e6:.0f}M` | Score: `{cfg['min_score']}/12`\n\nPilih menu:",
            kb_main())

    elif data == "menu:analyze":
        await edit_or_send(q, "📊 *Analisa & Scan*\nPilih aksi:", kb_analyze())

    elif data == "menu:paper":
        await edit_or_send(q, "📝 *Paper Trade*\nPilih aksi:", kb_paper())

    elif data == "menu:backtest":
        await edit_or_send(q, "🔬 *Backtest*\nPilih aksi:", kb_backtest())

    elif data == "menu:settings":
        await edit_or_send(q, f"⚙️ *Settings*\n\n{settings_summary()}", kb_settings(),
                           parse_mode="Markdown")

    elif data == "menu:stats":
        s = get_full_stats()
        ps = get_paper_stats()
        txt = (f"📊 *Statistik*\n\n"
               f"*Journal Signals*\n"
               f"Closed : `{s['total_closed']}`\n"
               f"Win/Loss: `{s['wins']}/{s['losses']}`\n"
               f"Winrate : `{s['winrate_pct']}%`\n"
               f"Avg PnL : `{s['avg_pnl_pct']:+.2f}%`\n"
               f"PF      : `{s['profit_factor']}`\n"
               f"Running : `{s['running_positions']}`\n\n"
               f"*Paper Trade*\n"
               f"Open    : `{ps['open']}`\n"
               f"Winrate : `{ps['winrate']}%`\n"
               f"Avg PnL : `{ps['avg_pnl']:+.2f}%`")
        await edit_or_send(q, txt, [[InlineKeyboardButton("« Kembali", callback_data="menu:main")]])

    # ── Settings sub-menus ──────────────────────────────────────────────────
    elif data == "set:mode":
        await edit_or_send(q, "⚙️ *Mode Trading*\nPilih mode:", kb_set_mode())

    elif data == "set:tf":
        await edit_or_send(q, "⚙️ *Timeframe*\nPilih kombinasi TF:", kb_set_tf())

    elif data == "set:mcap":
        cfg = load_cfg()
        await edit_or_send(q,
            f"⚙️ *Minimum Market Cap*\nSaat ini: `${cfg['min_mcap_usd']/1e6:.0f}M`\nPilih threshold:",
            kb_set_mcap())

    elif data == "set:score":
        cfg = load_cfg()
        await edit_or_send(q,
            f"⚙️ *Minimum Score*\nSaat ini: `{cfg['min_score']}/12`\nPilih threshold:",
            kb_set_score())

    elif data == "set:topn":
        cfg = load_cfg()
        await edit_or_send(q,
            f"⚙️ *Top N Sinyal*\nSaat ini: `{cfg['top_n_signals']}`\nPilih jumlah:",
            kb_set_topn())

    elif data == "set:interval":
        cfg = load_cfg()
        await edit_or_send(q,
            f"⚙️ *Auto-Scan Interval*\nSaat ini: `{cfg['scan_interval_h']}h`\nPilih interval:",
            kb_set_interval())

    # ── Set values ──────────────────────────────────────────────────────────
    elif data.startswith("setval:"):
        parts = data.split(":")
        key = parts[1]

        if key == "mode":
            mode = parts[2]
            set_val("mode", mode)
            if mode == "intraday":
                set_val("tf_bias","4h"); set_val("tf_structure","1h")
                set_val("tf_entry","15m"); set_val("tf_regime","1d")
            else:
                set_val("tf_bias","1d"); set_val("tf_structure","4h")
                set_val("tf_entry","1h"); set_val("tf_regime","1w")
            await edit_or_send(q,
                f"✅ Mode diubah ke `{mode}`\nPreset TF diterapkan.\n\n{settings_summary()}",
                [[InlineKeyboardButton("« Settings", callback_data="menu:settings")]])

        elif key == "tf":
            b,s,e = parts[2], parts[3], parts[4]
            set_val("tf_bias",b); set_val("tf_structure",s); set_val("tf_entry",e)
            await edit_or_send(q,
                f"✅ TF diubah: `{b}/{s}/{e}`",
                [[InlineKeyboardButton("« Settings", callback_data="menu:settings")]])

        elif key == "mcap":
            val = float(parts[2]) * 1_000_000
            set_val("min_mcap_usd", val)
            await edit_or_send(q,
                f"✅ Min MCap: `${val/1e6:.0f}M`",
                [[InlineKeyboardButton("« Settings", callback_data="menu:settings")]])

        elif key == "score":
            val = int(parts[2]); set_val("min_score", val)
            await edit_or_send(q,
                f"✅ Min Score: `{val}/12`",
                [[InlineKeyboardButton("« Settings", callback_data="menu:settings")]])

        elif key == "topn":
            val = int(parts[2]); set_val("top_n_signals", val)
            await edit_or_send(q,
                f"✅ Top N sinyal: `{val}`",
                [[InlineKeyboardButton("« Settings", callback_data="menu:settings")]])

        elif key == "interval":
            val = int(parts[2]); set_val("scan_interval_h", val)
            await edit_or_send(q,
                f"✅ Auto-scan interval: `{val}h`\n⚠️ Restart bot agar berlaku.",
                [[InlineKeyboardButton("« Settings", callback_data="menu:settings")]])

    # ── Actions ─────────────────────────────────────────────────────────────
    elif data == "action:status":
        try:
            analyzer.binance.fetch_ticker("BTC/USDT"); bk="✅"
        except: bk="❌"
        try:
            import requests as rq; rq.get("https://api.coingecko.com/api/v3/ping",timeout=4); cg="✅"
        except: cg="❌"
        cfg = load_cfg()
        await edit_or_send(q,
            f"🟢 *Bot Online*\nBinance: {bk} | CoinGecko: {cg}\n"
            f"Mode: `{cfg['mode']}` | TF: `{cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}`\n"
            f"Scan: Futures USDT Perp | MCap ≥ `${cfg['min_mcap_usd']/1e6:.0f}M`",
            [[InlineKeyboardButton("« Kembali", callback_data="menu:main")]])

    elif data == "action:scan":
        await q.message.reply_text("🔍 Memulai scan...", parse_mode="Markdown")
        await _run_scan(ctx.bot, chat_id, full=False)

    elif data == "action:scanall":
        await q.message.reply_text("📡 Memulai full scan...", parse_mode="Markdown")
        await _run_scan(ctx.bot, chat_id, full=True)

    elif data == "action:analyze_prompt":
        await edit_or_send(q,
            "📊 Ketik command berikut untuk analisa:\n`/analyze SOL`\n\n"
            "Ganti SOL dengan simbol koin yang ingin dianalisa.",
            [[InlineKeyboardButton("« Kembali", callback_data="menu:analyze")]])

    elif data == "action:paper_list":
        trades = get_open_paper_trades()
        if not trades:
            txt = "📝 Tidak ada paper trade terbuka."
        else:
            lines = ["📝 *Open Paper Trades:*\n"]
            for t in trades:
                d = "🟢" if t["bias"]=="LONG" else "🔴"
                lines.append(f"{d} `#{t['id']}` *{t['symbol']}* {t['bias']}\n"
                             f"   Entry:`{t['entry']:.5f}` SL:`{t['sl']:.5f}`\n"
                             f"   TP1:`{t['tp1']:.5f}` RR:{t['rr']}")
            txt = "\n".join(lines)
        await edit_or_send(q, txt,
            [[InlineKeyboardButton("« Kembali", callback_data="menu:paper")]])

    elif data == "action:paper_stats":
        ps = get_paper_stats()
        txt = (f"📊 *Paper Trade Stats*\n\n"
               f"Open    : `{ps['open']}`\nClosed  : `{ps['total']}`\n"
               f"Win/Loss: `{ps['wins']}/{ps['losses']}`\n"
               f"Winrate : `{ps['winrate']}%`\n"
               f"Avg PnL : `{ps['avg_pnl']:+.2f}%`")
        await edit_or_send(q, txt,
            [[InlineKeyboardButton("« Kembali", callback_data="menu:paper")]])

    elif data == "action:resetset":
        settings_reset()
        await edit_or_send(q,
            f"✅ Settings direset ke default.\n\n{settings_summary()}",
            [[InlineKeyboardButton("« Settings", callback_data="menu:settings")]])

    elif data == "info:closepapr":
        await edit_or_send(q,
            "Untuk menutup paper trade secara manual:\n`/closepapr <id> <harga_exit>`\n\nContoh:\n`/closepapr 3 98000`",
            [[InlineKeyboardButton("« Kembali", callback_data="menu:paper")]])

    elif data == "info:backtest":
        cfg = load_cfg()
        await edit_or_send(q,
            f"Untuk menjalankan backtest:\n`/backtest <simbol>`\n\nContoh:\n`/backtest SOL`\n\n"
            f"TF saat ini: `{cfg['tf_bias']}/{cfg['tf_entry']}`",
            [[InlineKeyboardButton("« Kembali", callback_data="menu:backtest")]])

# ══════════════════════════════════════════════════════════════════════════════
# SCAN LOGIC
# ══════════════════════════════════════════════════════════════════════════════

async def _run_scan(bot, chat_id, full=False):
    cfg = load_cfg()
    min_mcap  = cfg["min_mcap_usd"]
    top_n     = cfg["top_n_signals"]
    min_score = cfg["min_score"]

    await bot.send_message(chat_id=chat_id,
        text=f"🔍 Scan Binance Futures (MCap ≥ `${min_mcap/1e6:.0f}M`)...",
        parse_mode="Markdown")

    try:
        market = analyzer.scan_market_by_mcap(min_mcap)
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"❌ Gagal fetch market: {e}")
        return

    if not market:
        await bot.send_message(chat_id=chat_id, text="❌ Tidak ada koin lolos filter MCap.")
        return

    await bot.send_message(chat_id=chat_id,
        text=f"📋 {len(market)} koin lolos → analisis dimulai...")

    candidates = []
    for sym, mcap in market:
        try:
            report, direction, score = analyzer.quick_scan(sym)
            if report and direction != "SKIP" and score >= min_score:
                candidates.append((score, sym, report, direction))
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"scan {sym}: {e}")

    candidates.sort(key=lambda x: x[0], reverse=True)

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
        text=f"🏆 *{len(candidates)} sinyal → menampilkan {"semua" if full else f"top {len(top)}"}:*",
        parse_mode="Markdown")

    for score, sym, report, direction in top:
        ticker = analyzer.get_binance_ticker(sym)
        tech   = analyzer.tech.full_analysis(sym,
                     htf=cfg["tf_bias"], mtf=cfg["tf_structure"],
                     ltf=cfg["tf_entry"], regime_tf=cfg["tf_regime"])
        fund   = analyzer.get_fundamental_data(sym)
        if ticker and tech:
            dir_key = "BULLISH" if "LONG" in direction else "BEARISH"
            _, bd = analyzer._compute_score(dir_key, tech, fund,
                                             {**ticker,"symbol":sym}, {}, {})
            card = analyzer.format_signal(sym, direction, score,
                f"✅ HIGH ({score}/12)", ticker, tech, bd, fund)
            await send_long(bot, chat_id, card, parse_mode=None)
        else:
            await send_long(bot, chat_id, report)

        if tech and tech["execution"]["entry_mode"] != "NO TRADE":
            e = tech["execution"]; tp = e["tp"]
            try:
                tid = open_paper_trade(sym, direction, e["entry"], e["sl"],
                    tp["TP1"],tp["TP2"],tp["TP3"],e.get("sl_pct",0),e.get("rr","N/A"),score)
                await bot.send_message(chat_id=chat_id,
                    text=f"📝 Paper `#{tid}` dibuka — {sym} {direction}",
                    parse_mode="Markdown")
            except Exception:
                pass
        await asyncio.sleep(1.5)

    await bot.send_message(chat_id=chat_id,
        text=f"✅ Scan selesai. {len(candidates)} sinyal ditemukan.", parse_mode="Markdown")

# ══════════════════════════════════════════════════════════════════════════════
# AUTO-SCAN JOB
# ══════════════════════════════════════════════════════════════════════════════

async def auto_scan_job(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = TELEGRAM_CHAT_ID
    await ctx.bot.send_message(chat_id=chat_id, text="📢 *AUTO-SCAN*", parse_mode="Markdown")
    events = check_paper_trades(analyzer.binance)
    for ev in events:
        e = "✅" if "WIN" in ev["hit_level"] else "❌"
        await ctx.bot.send_message(chat_id=chat_id,
            text=f"{e} Paper `#{ev['trade_id']}` {ev['symbol']} → `{ev['hit_level']}` PnL:`{ev['pnl_pct']:+.2f}%`",
            parse_mode="Markdown")
    await _run_scan(ctx.bot, chat_id, full=False)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",     "Buka menu utama"),
        BotCommand("analyze",   "Analisa koin: /analyze SOL"),
        BotCommand("backtest",  "Backtest: /backtest SOL"),
        BotCommand("closepapr", "Tutup paper: /closepapr 3 98000"),
    ])

if __name__ == "__main__":
    cfg = load_cfg()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.job_queue.run_repeating(auto_scan_job,
        interval=cfg["scan_interval_h"]*3600, first=30)

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("analyze",   cmd_analyze))
    app.add_handler(CommandHandler("backtest",  cmd_backtest))
    app.add_handler(CommandHandler("closepapr", cmd_closepapr))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info(f"Bot | {cfg['mode']} | {cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}")
    app.run_polling(drop_pending_updates=True)
