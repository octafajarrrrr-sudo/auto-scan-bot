"""
Crypto Intraday Bot — Telegram Interface v8
InlineKeyboard dengan sub-menu + ConversationHandler untuk free-text input.
"""
import logging, asyncio
import aiohttp
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, ContextTypes,
                          CommandHandler, CallbackQueryHandler,
                          ConversationHandler, MessageHandler, filters)
from analyzer import CryptoBiasAnalyzer, HIGH_THRESH
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from settings import load as cfg_load, set_val, summary as cfg_summary, reset as cfg_reset, get as cfg_get
from backtest import run_backtest, format_backtest_report
from paper_trader import get_open_positions, get_paper_stats, close_position_manual, open_position
from journal import get_full_stats

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
analyzer = CryptoBiasAnalyzer()

# ConversationHandler states
AWAIT_INPUT = 1

# ── Auth ─────────────────────────────────────────────────────────────────────
def ok(update: Update) -> bool:
    return str((update.effective_user or update.callback_query.from_user).id) == str(TELEGRAM_CHAT_ID)

async def deny(update: Update):
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg: await msg.reply_text("⛔ Akses ditolak.")

# ── Helpers ───────────────────────────────────────────────────────────────────
async def send_long(bot, chat_id, text, parse_mode="Markdown"):
    for i in range(0, max(len(text), 1), 4000):
        await bot.send_message(chat_id=chat_id, text=text[i:i+4000],
                               parse_mode=parse_mode)
        if len(text) > 4000: await asyncio.sleep(0.4)

async def edit_or_new(q, text, kb=None, pm="Markdown"):
    markup = InlineKeyboardMarkup(kb) if kb else None
    try:
        await q.edit_message_text(text, reply_markup=markup, parse_mode=pm)
    except Exception:
        await q.message.reply_text(text, reply_markup=markup, parse_mode=pm)

def back(dest): return [[InlineKeyboardButton("« Kembali", callback_data=dest)]]

# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARD BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def kb_main():
    return [
        [InlineKeyboardButton("📊 Analisa",       callback_data="m:analyze"),
         InlineKeyboardButton("🔍 Scan Market",   callback_data="m:scan")],
        [InlineKeyboardButton("📝 Paper Trade",   callback_data="m:paper"),
         InlineKeyboardButton("🔬 Backtest",      callback_data="m:backtest")],
        [InlineKeyboardButton("⚙️ Settings",      callback_data="m:settings"),
         InlineKeyboardButton("📈 Statistik",     callback_data="m:stats")],
        [InlineKeyboardButton("🟢 Status Bot",    callback_data="a:status")],
    ]

def kb_analyze():
    return [
        [InlineKeyboardButton("🔎 Analisa 1 Koin",      callback_data="a:analyze_ask")],
        [InlineKeyboardButton("🚀 Scan → Top N Sinyal", callback_data="a:scan")],
        [InlineKeyboardButton("📋 Scan → Semua Sinyal", callback_data="a:scanall")],
        back("m:main"),
    ]

def kb_scan():
    cfg = cfg_load()
    return [
        [InlineKeyboardButton(f"🚀 Scan Sekarang (Top {cfg['top_n_signals']})",
                              callback_data="a:scan")],
        [InlineKeyboardButton("📋 Scan Semua",   callback_data="a:scanall")],
        [InlineKeyboardButton(f"💰 Min MCap: ${cfg['min_mcap_usd']/1e6:.0f}M",
                              callback_data="s:mcap")],
        [InlineKeyboardButton(f"🎯 Min Score: {cfg['min_score']}/14",
                              callback_data="s:score")],
        back("m:main"),
    ]

def kb_paper():
    return [
        [InlineKeyboardButton("👁 Open Trades",    callback_data="a:paper_list")],
        [InlineKeyboardButton("📊 Statistik",      callback_data="a:paper_stats")],
        [InlineKeyboardButton("✏️ Input Koin Manual", callback_data="a:paper_manual_ask")],
        [InlineKeyboardButton("❌ Tutup Trade",    callback_data="a:paper_close_ask")],
        back("m:main"),
    ]

def kb_backtest():
    cfg = cfg_load()
    return [
        [InlineKeyboardButton("🔬 Run Backtest",   callback_data="a:bt_ask")],
        [InlineKeyboardButton(f"📐 TF: {cfg['tf_bias']}/{cfg['tf_entry']}",
                              callback_data="s:tf")],
        back("m:main"),
    ]

def kb_settings():
    cfg = cfg_load()
    mode_icon = "⚡" if cfg['mode'] == 'intraday' else "📈"
    return [
        [InlineKeyboardButton(f"{mode_icon} Mode: {cfg['mode'].upper()}",
                              callback_data="s:mode")],
        [InlineKeyboardButton(f"⏱ Timeframe: {cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}",
                              callback_data="s:tf")],
        [InlineKeyboardButton(f"💰 Min MCap: ${cfg['min_mcap_usd']/1e6:.0f}M",
                              callback_data="s:mcap"),
         InlineKeyboardButton(f"🎯 Score: {cfg['min_score']}/14",
                              callback_data="s:score")],
        [InlineKeyboardButton(f"📤 Top N: {cfg['top_n_signals']}",
                              callback_data="s:topn"),
         InlineKeyboardButton(f"⏰ Interval: {cfg['scan_interval_h']}h",
                              callback_data="s:interval")],
        [InlineKeyboardButton("✏️ Input Bebas",   callback_data="s:free")],
        [InlineKeyboardButton("↩️ Reset Default", callback_data="a:resetset")],
        back("m:main"),
    ]

def kb_mode():
    cfg = cfg_load()
    tick = lambda m: " ✓" if cfg['mode'] == m else ""
    return [
        [InlineKeyboardButton(f"⚡ Intraday (H4/H1/M15){tick('intraday')}",
                              callback_data="sv:mode:intraday")],
        [InlineKeyboardButton(f"📈 Swing (1D/4H/1H){tick('swing')}",
                              callback_data="sv:mode:swing")],
        back("m:settings"),
    ]

def kb_tf():
    return [
        [InlineKeyboardButton("⚡ H4 / H1 / M15",  callback_data="sv:tf:4h:1h:15m")],
        [InlineKeyboardButton("⚡ H4 / H1 / M5",   callback_data="sv:tf:4h:1h:5m")],
        [InlineKeyboardButton("📊 H2 / H1 / M15",  callback_data="sv:tf:2h:1h:15m")],
        [InlineKeyboardButton("📈 1D / 4H / 1H",   callback_data="sv:tf:1d:4h:1h")],
        [InlineKeyboardButton("🔭 1W / 1D / 4H",   callback_data="sv:tf:1w:1d:4h")],
        back("m:settings"),
    ]

def kb_mcap():
    cfg = cfg_load()
    cur = cfg['min_mcap_usd'] / 1e6
    mk = lambda v: f"{'✓ ' if cur==v else ''}${int(v)}M"
    return [
        [InlineKeyboardButton(mk(10),  callback_data="sv:mcap:10"),
         InlineKeyboardButton(mk(30),  callback_data="sv:mcap:30"),
         InlineKeyboardButton(mk(50),  callback_data="sv:mcap:50")],
        [InlineKeyboardButton(mk(100), callback_data="sv:mcap:100"),
         InlineKeyboardButton(mk(300), callback_data="sv:mcap:300"),
         InlineKeyboardButton(mk(500), callback_data="sv:mcap:500")],
        [InlineKeyboardButton("✏️ Input Custom", callback_data="sv:mcap:custom")],
        back("m:settings"),
    ]

def kb_score():
    cfg = cfg_load()
    cur = cfg['min_score']
    mk  = lambda v: f"{'✓' if cur==v else str(v)}"
    return [
        [InlineKeyboardButton(mk(i), callback_data=f"sv:score:{i}") for i in range(6, 11)],
        [InlineKeyboardButton(mk(i), callback_data=f"sv:score:{i}") for i in range(11, 15)],
        [InlineKeyboardButton("✏️ Input Custom", callback_data="sv:score:custom")],
        back("m:settings"),
    ]

def kb_topn():
    cfg = cfg_load()
    cur = cfg['top_n_signals']
    mk  = lambda v: f"{'✓' if cur==v else str(v)}"
    return [
        [InlineKeyboardButton(mk(i), callback_data=f"sv:topn:{i}") for i in range(1, 6)],
        [InlineKeyboardButton(mk(i), callback_data=f"sv:topn:{i}") for i in range(6, 11)],
        back("m:settings"),
    ]

def kb_interval():
    cfg  = cfg_load()
    cur  = cfg['scan_interval_h']
    mk   = lambda v: f"{'✓ ' if cur==v else ''}{v}h"
    return [
        [InlineKeyboardButton(mk(1),  callback_data="sv:interval:1"),
         InlineKeyboardButton(mk(2),  callback_data="sv:interval:2"),
         InlineKeyboardButton(mk(4),  callback_data="sv:interval:4")],
        [InlineKeyboardButton(mk(6),  callback_data="sv:interval:6"),
         InlineKeyboardButton(mk(8),  callback_data="sv:interval:8"),
         InlineKeyboardButton(mk(12), callback_data="sv:interval:12")],
        [InlineKeyboardButton("✏️ Custom (jam)", callback_data="sv:interval:custom")],
        back("m:settings"),
    ]

def kb_free_settings():
    """Pilih parameter mana yang mau diinput bebas."""
    return [
        [InlineKeyboardButton("💰 Min MCap (juta USD)",   callback_data="fi:mcap")],
        [InlineKeyboardButton("🎯 Min Score (1-14)",      callback_data="fi:score")],
        [InlineKeyboardButton("📤 Top N sinyal",          callback_data="fi:topn")],
        [InlineKeyboardButton("⏰ Interval scan (jam)",   callback_data="fi:interval")],
        [InlineKeyboardButton("⏱ Timeframe (bias str entry)", callback_data="fi:tf")],
        back("m:settings"),
    ]

# ══════════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return await deny(update)
    cfg = cfg_load()
    text = (f"⚡ *Crypto Intraday Bot*\n"
            f"Mode: `{cfg['mode']}` · TF: `{cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}`\n"
            f"Score: `≥{cfg['min_score']}/14` · MCap: `≥${cfg['min_mcap_usd']/1e6:.0f}M`\n\n"
            f"Pilih menu:")
    await update.message.reply_text(text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb_main()))

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return await deny(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/analyze SOL`", parse_mode="Markdown")
    sym = ctx.args[0].upper()
    await update.message.reply_text(f"🔍 Menganalisis `{sym}`...", parse_mode="Markdown")
    try:
        report = await analyzer.analyze(sym)
        await send_long(ctx.bot, update.effective_chat.id, report)
    except Exception as e:
        logger.error(f"analyze {sym}: {e}")
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_backtest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return await deny(update)
    if not ctx.args:
        return await update.message.reply_text("Format: `/backtest SOL`", parse_mode="Markdown")
    sym = ctx.args[0].upper(); cfg = cfg_load()
    await update.message.reply_text(f"🔬 Backtest `{sym}`...", parse_mode="Markdown")
    try:
        r = await run_backtest(analyzer.tech, sym, cfg["tf_bias"], cfg["tf_entry"])
        await send_long(ctx.bot, update.effective_chat.id, format_backtest_report(r, sym))
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def cmd_closepapr(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ok(update): return await deny(update)
    if len(ctx.args) < 2:
        return await update.message.reply_text("Format: `/closepapr <id> <harga>`", parse_mode="Markdown")
    try:
        r = close_position_manual(int(ctx.args[0]), float(ctx.args[1]))
        if not r: return await update.message.reply_text("❌ Trade tidak ditemukan.")
        e = "✅" if r["pnl_pct"] > 0 else "❌"
        await update.message.reply_text(
            f"{e} Trade ditutup · PnL: `{r['pnl_pct']:+.2f}%` ({r['pnl_r']:+.2f}R)",
            parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Format salah. Contoh: `/closepapr 3 98000`", parse_mode="Markdown")

# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATION HANDLER — Free-text input
# ══════════════════════════════════════════════════════════════════════════════

async def conv_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Entry point dari callback — set context dan minta input."""
    q = update.callback_query
    await q.answer()
    if not ok(update): return ConversationHandler.END

    data = q.data  # e.g. "fi:mcap", "a:analyze_ask", "a:bt_ask"
    ctx.user_data["conv_action"] = data
    chat_id = q.message.chat_id

    prompts = {
        "a:analyze_ask":      "Ketik *simbol koin* yang ingin dianalisa:\nContoh: `SOL`",
        "a:bt_ask":           "Ketik *simbol koin* untuk backtest:\nContoh: `SOL`",
        "a:paper_manual_ask": "Ketik *simbol koin* untuk paper trade manual:\nContoh: `ETH`",
        "a:paper_close_ask":  "Ketik *ID trade* dan *harga exit*:\nContoh: `3 98000`",
        "fi:mcap":    "Ketik *minimum Market Cap* dalam juta USD:\nContoh: `75` (artinya $75M)",
        "fi:score":   "Ketik *minimum score* (1-14):\nContoh: `9`",
        "fi:topn":    "Ketik *jumlah sinyal top* yang dikirim (1-20):\nContoh: `3`",
        "fi:interval":"Ketik *interval auto-scan* dalam jam (1-24):\nContoh: `4`",
        "fi:tf":      "Ketik *timeframe* dalam format: `bias struktur entry`\nContoh: `4h 1h 15m`",
    }

    prompt = prompts.get(data, "Ketik nilai:")
    await q.message.reply_text(prompt, parse_mode="Markdown")
    return AWAIT_INPUT

async def conv_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Terima text input dari user dan proses."""
    if not ok(update): return ConversationHandler.END
    action = ctx.user_data.get("conv_action", "")
    text   = update.message.text.strip()
    chat_id = update.effective_chat.id

    try:
        # ── Analyze ──────────────────────────────────────────────────────────
        if action == "a:analyze_ask":
            sym = text.upper().replace("/USDT","").replace("USDT","")
            msg = await update.message.reply_text(f"🔍 Menganalisis `{sym}`...", parse_mode="Markdown")
            report = await analyzer.analyze(sym)
            await send_long(ctx.bot, chat_id, report)

        # ── Backtest ─────────────────────────────────────────────────────────
        elif action == "a:bt_ask":
            sym = text.upper().replace("/USDT","").replace("USDT","")
            cfg = cfg_load()
            await update.message.reply_text(f"🔬 Backtest `{sym}` ({cfg['tf_bias']}/{cfg['tf_entry']})...", parse_mode="Markdown")
            r = await run_backtest(analyzer.tech, sym, cfg["tf_bias"], cfg["tf_entry"])
            await send_long(ctx.bot, chat_id, format_backtest_report(r, sym))

        elif action == "a:paper_manual_ask":
            sym = text.upper().replace("/USDT","").replace("USDT","")
            ticker = await analyzer.get_binance_ticker(sym)
            tech   = await analyzer.tech.full_analysis(sym)
            if not ticker or not tech:
                await update.message.reply_text(f"❌ Tidak bisa fetch data untuk {sym}")
            elif tech["execution"]["entry_mode"] == "NO TRADE":
                await update.message.reply_text(f"⚠️ {sym}: NO TRADE saat ini (tidak ada setup valid)")
            else:
                e = tech["execution"]; tp = e["tp"]
                # Assuming USD size = 10% of $1000 = $100 as default manual
                pos_usd = 100.0
                qty = pos_usd / e["entry"]
                tid = open_position(sym, e["entry_mode"].split()[0],
                    e["entry"], e["sl"], tp["TP1"], tp["TP2"], pos_usd, qty)
                await update.message.reply_text(
                    f"✅ Paper trade `#{tid}` dibuka\n"
                    f"{sym} {e['entry_mode']} @ `{e['entry']:.5f}`\n"
                    f"SL: `{e['sl']:.5f}` TP1: `{tp['TP1']:.5f}`",
                    parse_mode="Markdown")

        # ── Paper close ───────────────────────────────────────────────────────
        elif action == "a:paper_close_ask":
            parts = text.split()
            if len(parts) < 2:
                await update.message.reply_text("❌ Format: `3 98000`", parse_mode="Markdown")
            else:
                tid = int(parts[0]); ep = float(parts[1])
                r = close_position_manual(tid, ep)
                if not r: await update.message.reply_text(f"❌ Trade #{tid} tidak ditemukan.")
                else:
                    e = "✅" if r["pnl_pct"] > 0 else "❌"
                    await update.message.reply_text(
                        f"{e} Trade `#{tid}` ditutup · PnL: `{r['pnl_pct']:+.2f}%`",
                        parse_mode="Markdown")

        # ── Free input settings ────────────────────────────────────────────
        elif action == "fi:mcap":
            val = float(text) * 1_000_000
            if val <= 0: raise ValueError
            set_val("min_mcap_usd", val)
            await update.message.reply_text(f"✅ Min MCap: `${val/1e6:.0f}M`\n\n{cfg_summary()}", parse_mode="Markdown")

        elif action == "fi:score":
            val = int(text)
            if not 1 <= val <= 14: raise ValueError("1-14")
            set_val("min_score", val)
            await update.message.reply_text(f"✅ Min Score: `{val}/14`\n\n{cfg_summary()}", parse_mode="Markdown")

        elif action == "fi:topn":
            val = int(text)
            if not 1 <= val <= 20: raise ValueError("1-20")
            set_val("top_n_signals", val)
            await update.message.reply_text(f"✅ Top N: `{val}`", parse_mode="Markdown")

        elif action == "fi:interval":
            val = int(text)
            if not 1 <= val <= 24: raise ValueError("1-24")
            set_val("scan_interval_h", val)
            await update.message.reply_text(
                f"✅ Interval: `{val}h`\n⚠️ Restart bot agar berlaku.", parse_mode="Markdown")

        elif action == "fi:tf":
            parts = text.lower().split()
            valid = {"1m","3m","5m","15m","30m","1h","2h","4h","6h","8h","12h","1d","3d","1w"}
            if len(parts) < 3 or not all(p in valid for p in parts[:3]):
                raise ValueError(f"Format: bias struktur entry (contoh: 4h 1h 15m)")
            set_val("tf_bias", parts[0]); set_val("tf_structure", parts[1]); set_val("tf_entry", parts[2])
            await update.message.reply_text(
                f"✅ TF: `{parts[0]}/{parts[1]}/{parts[2]}`\n\n{cfg_summary()}", parse_mode="Markdown")

    except ValueError as e:
        await update.message.reply_text(f"❌ Input tidak valid: {e}\nCoba lagi.", parse_mode="Markdown")
        return AWAIT_INPUT
    except Exception as e:
        logger.error(f"conv_receive error ({action}): {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}")

    return ConversationHandler.END

async def conv_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Dibatalkan.")
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════════════

CONV_TRIGGERS = {"a:analyze_ask","a:bt_ask","a:paper_manual_ask",
                 "a:paper_close_ask","fi:mcap","fi:score","fi:topn","fi:interval","fi:tf"}

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not ok(update):
        await q.answer("⛔ Akses ditolak.", show_alert=True); return
    await q.answer()

    d       = q.data
    chat_id = q.message.chat_id

    # ── Navigation ───────────────────────────────────────────────────────────
    if d == "m:main":
        cfg = cfg_load()
        await edit_or_new(q,
            f"⚡ *Crypto Intraday Bot*\n"
            f"Mode: `{cfg['mode']}` · TF: `{cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}`\n"
            f"Score: `≥{cfg['min_score']}/14` · MCap: `≥${cfg['min_mcap_usd']/1e6:.0f}M`\n\nPilih menu:",
            kb_main())

    elif d == "m:analyze": await edit_or_new(q, "📊 *Analisa & Scan*\nPilih aksi:", kb_analyze())
    elif d == "m:scan":    await edit_or_new(q, "🔍 *Scan Market*\nPilih aksi:", kb_scan())
    elif d == "m:paper":   await edit_or_new(q, "📝 *Paper Trade*\nPilih aksi:", kb_paper())
    elif d == "m:backtest":await edit_or_new(q, "🔬 *Backtest*\nPilih aksi:", kb_backtest())
    elif d == "m:settings":await edit_or_new(q, f"⚙️ *Settings*\n\n{cfg_summary()}", kb_settings())
    elif d == "m:stats":
        s = get_full_stats(); ps = get_paper_stats()
        await edit_or_new(q,
            f"📊 *Statistik*\n\n"
            f"*Signals*\nClosed:{s['total_closed']} Win:{s['wins']} Loss:{s['losses']}\n"
            f"WR:{s['winrate_pct']}% · PF:{s['profit_factor']} · Exp:{s['expectancy']:+.2f}%\n"
            f"Running:{s['running_positions']} · Max Streak:{s['max_streak']}\n\n"
            f"*Paper Trade*\nOpen:{ps['open']} WR:{ps['winrate']}% PnL:${ps['total_pnl_usd']}",
            back("m:main"))

    # ── Settings sub-menus ────────────────────────────────────────────────────
    elif d == "s:mode":     await edit_or_new(q, "⚙️ *Mode Trading*\nPilih:", kb_mode())
    elif d == "s:tf":       await edit_or_new(q, "⚙️ *Timeframe*\nPilih kombinasi:", kb_tf())
    elif d == "s:mcap":     await edit_or_new(q, f"⚙️ *Min MCap*\nSaat ini: `${cfg_get('min_mcap_usd')/1e6:.0f}M`\nPilih:", kb_mcap())
    elif d == "s:score":    await edit_or_new(q, f"⚙️ *Min Score*\nSaat ini: `{cfg_get('min_score')}/14`\nPilih:", kb_score())
    elif d == "s:topn":     await edit_or_new(q, f"⚙️ *Top N*\nSaat ini: `{cfg_get('top_n_signals')}`\nPilih:", kb_topn())
    elif d == "s:interval": await edit_or_new(q, f"⚙️ *Auto-Scan Interval*\nSaat ini: `{cfg_get('scan_interval_h')}h`\nPilih:", kb_interval())
    elif d == "s:free":     await edit_or_new(q, "✏️ *Input Bebas Settings*\nPilih parameter:", kb_free_settings())

    # ── Set values ────────────────────────────────────────────────────────────
    elif d.startswith("sv:"):
        parts = d.split(":")
        key, val = parts[1], parts[2] if len(parts) > 2 else ""

        if key == "mode":
            set_val("mode", val)
            if val == "intraday":
                for k,v in [("tf_bias","4h"),("tf_structure","1h"),("tf_entry","15m"),("tf_regime","1d")]:
                    set_val(k, v)
            else:
                for k,v in [("tf_bias","1d"),("tf_structure","4h"),("tf_entry","1h"),("tf_regime","1w")]:
                    set_val(k, v)
            await edit_or_new(q, f"✅ Mode → `{val}` + TF preset diterapkan\n\n{cfg_summary()}", back("m:settings"))

        elif key == "tf":
            b,s,e = parts[2], parts[3], parts[4]
            set_val("tf_bias",b); set_val("tf_structure",s); set_val("tf_entry",e)
            await edit_or_new(q, f"✅ TF → `{b}/{s}/{e}`\n\n{cfg_summary()}", back("m:settings"))

        elif key == "mcap":
            if val == "custom":
                ctx.user_data["conv_action"] = "fi:mcap"
                await q.message.reply_text("Ketik *min MCap* dalam juta USD (contoh: `75`):", parse_mode="Markdown")
                ctx.user_data["_in_conv"] = True
            else:
                mv = float(val) * 1_000_000; set_val("min_mcap_usd", mv)
                await edit_or_new(q, f"✅ Min MCap → `${mv/1e6:.0f}M`\n\n{cfg_summary()}", back("m:settings"))

        elif key == "score":
            if val == "custom":
                ctx.user_data["conv_action"] = "fi:score"
                await q.message.reply_text("Ketik *min score* (1-14):", parse_mode="Markdown")
                ctx.user_data["_in_conv"] = True
            else:
                set_val("min_score", int(val))
                await edit_or_new(q, f"✅ Min Score → `{val}/14`\n\n{cfg_summary()}", back("m:settings"))

        elif key == "topn":
            set_val("top_n_signals", int(val))
            await edit_or_new(q, f"✅ Top N → `{val}`", back("m:settings"))

        elif key == "interval":
            if val == "custom":
                ctx.user_data["conv_action"] = "fi:interval"
                await q.message.reply_text("Ketik *interval scan* dalam jam (1-24):", parse_mode="Markdown")
                ctx.user_data["_in_conv"] = True
            else:
                set_val("scan_interval_h", int(val))
                await edit_or_new(q, f"✅ Interval → `{val}h`\n⚠️ Restart bot agar berlaku.", back("m:settings"))

    # ── Actions ────────────────────────────────────────────────────────────────
    elif d == "a:status":
        try:
            await analyzer.binance.fetch_ticker("BTC/USDT")
            bk = "✅"
        except: bk = "❌"
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get("https://api.coingecko.com/api/v3/ping", timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    cg = "✅" if resp.status == 200 else "❌"
        except: cg = "❌"
        cfg = cfg_load()
        await edit_or_new(q,
            f"🟢 *Bot Online*\nBinance: {bk} · CoinGecko: {cg}\n"
            f"Mode: `{cfg['mode']}` · TF: `{cfg['tf_bias']}/{cfg['tf_structure']}/{cfg['tf_entry']}`\n"
            f"Score ≥`{cfg['min_score']}/14` · MCap ≥`${cfg['min_mcap_usd']/1e6:.0f}M`",
            back("m:main"))

    elif d == "a:scan":
        await q.message.reply_text("🔍 Memulai scan Futures...", parse_mode="Markdown")
        await _run_scan(ctx.bot, chat_id, full=False)

    elif d == "a:scanall":
        await q.message.reply_text("📡 Memulai full scan...", parse_mode="Markdown")
        await _run_scan(ctx.bot, chat_id, full=True)

    elif d == "a:paper_list":
        trades = get_open_positions()
        if not trades:
            await edit_or_new(q, "📝 Tidak ada paper trade terbuka.", back("m:paper"))
        else:
            lines = ["📝 *Open Paper Trades:*\n"]
            for t in trades:
                icon = "🟢" if t["direction"] == "LONG" else "🔴"
                lines.append(f"{icon} `#{t['id']}` *{t['symbol']}* {t['direction']}\n"
                             f"  Entry:`{t['entry_price']:.5f}` SL:`{t['sl_price']:.5f}` TP1:`{t['tp1_price']:.5f}` Size:`${t['position_usd']:.0f}` PnL:`${t['pnl_usd']:.2f}`")
            await edit_or_new(q, "\n".join(lines), back("m:paper"))

    elif d == "a:paper_stats":
        ps = get_paper_stats()
        await edit_or_new(q,
            f"📊 *Paper Trade Stats*\nOpen:{ps['open']} Closed:{ps['total']}\n"
            f"Win:{ps['wins']} Loss:{ps['losses']} WR:{ps['winrate']}%\n"
            f"Total PnL: ${ps['total_pnl_usd']:.2f}",
            back("m:paper"))

    elif d == "a:resetset":
        cfg_reset()
        await edit_or_new(q, f"✅ Settings direset ke default.\n\n{cfg_summary()}", back("m:settings"))

# ══════════════════════════════════════════════════════════════════════════════
# SCAN LOGIC
# ══════════════════════════════════════════════════════════════════════════════

async def _run_scan(bot, chat_id, full=False):
    cfg       = cfg_load()
    min_mcap  = cfg["min_mcap_usd"]
    top_n     = cfg["top_n_signals"]
    min_score = max(cfg["min_score"], HIGH_THRESH)  # never go below HIGH_THRESH

    await bot.send_message(chat_id=chat_id,
        text=f"📋 Scan Futures USDT Perp (MCap ≥ `${min_mcap/1e6:.0f}M`)...",
        parse_mode="Markdown")

    try:
        market = await analyzer.scan_market_by_volume(min_vol=min_mcap) # reuse config value temporarily
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"❌ Gagal fetch market: {e}"); return

    if not market:
        await bot.send_message(chat_id=chat_id, text="❌ Tidak ada koin lolos volume filter."); return

    await bot.send_message(chat_id=chat_id,
        text=f"✅ {len(market)} koin lolos → analisis...")

    candidates = []
    _SEM = asyncio.Semaphore(8)

    async def _scan_sym(sym: str, mcap_val):
        async with _SEM:
            try:
                res = await analyzer.quick_scan(sym)
                if not res:
                    return None
                report, direction, score, ticker, tech, fund, bd = res
                if report and direction != "SKIP" and score >= min_score:
                    return (score, sym, report, direction, ticker, tech, fund, bd)
            except Exception as e:
                logger.warning(f"scan {sym}: {e}")
            return None

    tasks   = [_scan_sym(sym, mcap) for sym, mcap in market]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    candidates = [r for r in results if r and not isinstance(r, Exception)]

    candidates.sort(key=lambda x: x[0], reverse=True)

    try:
        from journal import log_scan_run
        log_scan_run(len(market), len(candidates), [c[1] for c in candidates[:top_n]])
    except Exception:
        pass

    if not candidates:
        await bot.send_message(chat_id=chat_id, text="😴 Tidak ada sinyal yang lolos threshold."); return

    top = candidates if full else candidates[:top_n]
    await bot.send_message(chat_id=chat_id,
        text=f"🏆 *{len(candidates)} sinyal → menampilkan {'semua' if full else f'top {len(top)}'}:*",
        parse_mode="Markdown")

    for score, sym, report, direction, ticker, tech, fund, bd in top:
        if ticker and tech:
            card = analyzer.format_signal(sym, direction, score,
                f"✅ HIGH ({score}/14)", ticker, tech, bd, fund)
            await send_long(bot, chat_id, card, parse_mode=None)
        else:
            await send_long(bot, chat_id, report)

        await asyncio.sleep(1.5)

    await bot.send_message(chat_id=chat_id,
        text=f"✅ Scan selesai · {len(candidates)} sinyal.", parse_mode="Markdown")

# ══════════════════════════════════════════════════════════════════════════════
# MAIN EXPORTS
# ══════════════════════════════════════════════════════════════════════════════

# ConversationHandler — untuk semua input teks bebas
conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(conv_entry,
        pattern="^(a:analyze_ask|a:bt_ask|a:paper_manual_ask|a:paper_close_ask|fi:.+)$")],
    states={AWAIT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_receive)]},
    fallbacks=[CommandHandler("cancel", conv_cancel)],
    per_chat=True,
)

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",     "Buka menu utama"),
        BotCommand("analyze",   "Analisa koin: /analyze SOL"),
        BotCommand("backtest",  "Backtest: /backtest SOL"),
        BotCommand("closepapr", "Tutup paper: /closepapr 3 98000"),
        BotCommand("cancel",    "Batalkan input"),
    ])

if __name__ == "__main__":
    import asyncio
    from config import TELEGRAM_BOT_TOKEN

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Tambahkan handler-handler yang sudah didefinisikan di atas
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    app.add_handler(CommandHandler("closepapr", cmd_closepapr))

    # ConversationHandler HARUS di atas CallbackQueryHandler tanpa pattern.
    # Handler diperiksa berurutan — jika handle_callback masuk duluan,
    # ia menangkap SEMUA callback termasuk milik conv → conv tidak pernah mulai.
    app.add_handler(conv)

    # CallbackQueryHandler untuk navigasi menu & aksi (tanpa pattern = tangkap semua sisa)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Jalankan bot (blocking)
    app.run_polling()
