"""
ANALYZER — Unified Swing Trade Signal Engine
Scoring: 14-point directional confluence system
  L1: Regime(0-2) L2: Structure(0-3) L3: EMA(0-2)
  L4: Elliott(0-1) L5: LTF(0-1) L6: Fund(0-1)
  L7: DeFi(0-1)   L8: Sentiment(0-1) L9: Whale(0-1) L10: GC/DC(0-1)
  HIGH CONVICTION >= 9 | MODERATE >= 6 | NO TRADE < 6

Exported constants (import these — don't hardcode thresholds elsewhere):
  HIGH_THRESH, MOD_THRESH, MAX_SCORE
"""
import aiohttp
import asyncio
import ccxt.async_support as ccxt
import xml.etree.ElementTree as ET
from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, CMC_API_KEY
from technical import TechnicalEngine
from defillama import get_full_defi_context, score_defi_confluence
from sentiment import get_full_sentiment, score_sentiment
from whale_tracker import get_whale_context, score_whale
from journal import log_signal, log_scan_run
from settings import get as cfg_get

MAX_SCORE   = 14   # L1-L10 + L3 max 2 + L2 max 3
HIGH_THRESH = 9    # Raised from 8 for better precision
MOD_THRESH  = 6


class CryptoNewsScraper:
    async def get_catalysts(self, symbol):
        catalysts = []
        try:
            url = (f"https://news.google.com/rss/search"
                   f"?q={symbol}+crypto+when:24h&hl=en-US&gl=US&ceid=US:en")
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as r:
                    if r.status == 200:
                        text = await r.text()
                        root = ET.fromstring(text)
                        for item in root.findall(".//item")[:2]:
                            title = item.find("title").text or ""
                            catalysts.append(f"• {title.split(' - ')[0].strip()}")
        except Exception:
            pass
        return catalysts or ["• Tidak ada katalis signifikan 24h terakhir"]


class CryptoBiasAnalyzer:

    def __init__(self):
        self.binance = ccxt.binance({
            "apiKey":        BINANCE_API_KEY,
            "secret":        BINANCE_SECRET_KEY,
            "timeout":       8000,   # ms — prevents scanner_loop from hanging
            "enableRateLimit": True, # built-in throttle; avoids 429s
        })
        self.tech    = TechnicalEngine(self.binance)
        self.scraper = CryptoNewsScraper()
        self.cg_url  = "https://api.coingecko.com/api/v3"
        self.cmc_url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        self.cmc_hdr = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
        self._session = None  # Shared aiohttp session

    async def _get_session(self):
        """Lazy-init shared aiohttp session untuk connection pooling."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self):
        """Tutup semua koneksi. WAJIB dipanggil saat shutdown."""
        if self._session and not self._session.closed:
            await self._session.close()
        await self.binance.close()

    # ── Data providers ────────────────────────────────────────────────────

    async def get_fundamental_data(self, symbol):
        """Fundamental data fetch (async). Dipanggil hanya saat analisis detail, bukan saat scan."""
        try:
            session = await self._get_session()
            async with session.get(self.cmc_url, headers=self.cmc_hdr, params={"symbol": symbol, "convert": "USD"}) as r:
                data = await r.json()
                if "data" in data and symbol in data["data"]:
                    q = data["data"][symbol]["quote"]["USD"]
                    return {
                        "mcap":     q.get("market_cap", 0),
                        "fdv":      q.get("fully_diluted_market_cap", q.get("market_cap", 0)),
                        "vol24h":   q.get("volume_24h", 0),
                        "change24h":q.get("percent_change_24h", 0),
                        "change7d": q.get("percent_change_7d", 0),
                        "source":   "CMC",
                    }
        except Exception:
            pass
        return None

    async def get_binance_ticker(self, symbol):
        try:
            t, btc = await asyncio.gather(
                self.binance.fetch_ticker(f"{symbol}/USDT"),
                self.binance.fetch_ticker("BTC/USDT")
            )
            return {
                "price":      t["last"],
                "vol":        t["quoteVolume"],
                "change":     t["percentage"],
                "btc_change": btc["percentage"],
                "high":       t["high"],
                "low":        t["low"],
            }
        except Exception:
            return None

    # ── Unified Scoring Engine ────────────────────────────────────────────

    def _compute_score(self, direction: str, tech: dict, fund: dict,
                       ticker: dict, defi_ctx: dict,
                       pre_sent_score: int = 0, pre_whale_score: int = 0) -> tuple[int, dict]:
        """
        Hitung skor 14-poin untuk satu arah (BULLISH atau BEARISH).
        L1-L10: Regime, Structure, EMA, Elliott, LTF, Fund, DeFi, Sentiment, Whale, GC/DC
        Return: (total_score, breakdown_dict)
        """
        is_long = direction == "BULLISH"
        bd      = {}   # breakdown per layer

        # ── L1: Market Regime (0-2) ────────────────────────────────────
        regime = tech.get("regime", {})
        r_btc  = regime.get("regime_btc", "UNKNOWN")
        r_coin = regime.get("regime_coin", "UNKNOWN")

        if is_long:
            if r_btc == "BULL_MARKET":
                l1 = 2 if r_coin == "ABOVE_EMA200" else 1
            elif r_btc == "TRANSITION_BULL":
                l1 = 1
            elif r_btc == "RANGING":
                l1 = 1
            elif r_btc in ("TRANSITION_BEAR", "BEAR_MARKET"):
                l1 = 0
            else:
                l1 = 1
        else:  # SHORT
            if r_btc == "BEAR_MARKET":
                l1 = 2 if r_coin == "BELOW_EMA200" else 1
            elif r_btc in ("TRANSITION_BEAR",):
                l1 = 1
            elif r_btc == "RANGING":
                l1 = 1
            elif r_btc in ("TRANSITION_BULL", "BULL_MARKET"):
                l1 = 0
            else:
                l1 = 1
        bd["L1_regime"] = l1

        # ── L2: HTF Structure (0-3) ────────────────────────────────────
        struct = tech["htf"]["structure"]
        s_str  = struct["structure"]
        s_bos  = struct["bos"]

        base_l2 = 0
        if is_long:
            if   s_str == "BULLISH (HH + HL)":    base_l2 =  2
            elif "CHoCH BULLISH" in s_str:         base_l2 =  1
            elif s_str == "BEARISH (LH + LL)":     base_l2 = -1
        else:
            if   s_str == "BEARISH (LH + LL)":    base_l2 =  2
            elif "CHoCH BEARISH" in s_str:         base_l2 =  1
            elif s_str == "BULLISH (HH + HL)":     base_l2 = -1

        bos_bonus = 0
        if s_bos:
            if is_long  and "BULLISH" in s_bos:   bos_bonus = 1
            elif not is_long and "BEARISH" in s_bos: bos_bonus = 1

        l2 = max(0, min(3, base_l2 + bos_bonus))
        bd["L2_structure"] = l2

        # ── L3: EMA Alignment (0-2) ───────────────────────────────────
        ema        = tech["htf"]["ema"]
        ema_score  = ema.get("ema_score", 0)   # 0-5

        if is_long:
            if ema_score >= 4:  l3 = 2
            elif ema_score == 3: l3 = 1
            else:                l3 = 0
        else:
            inv = 5 - ema_score  # 0-5, makin besar = makin bearish
            if inv >= 4:  l3 = 2
            elif inv == 3: l3 = 1
            else:          l3 = 0
        bd["L3_ema"] = l3

        # ── L4: Elliott Wave (0-1) ────────────────────────────────────
        elliott = tech["htf"].get("elliott", {})
        e_dir   = elliott.get("direction", "NEUTRAL")
        e_score = elliott.get("score", 0)
        e_pos   = elliott.get("wave_position", "")

        l4 = 0
        if (e_score == 1
                and ((is_long and e_dir == "BULLISH") or (not is_long and e_dir == "BEARISH"))
                and "Post Wave-5" not in e_pos):
            l4 = 1
        bd["L4_elliott"] = l4

        # ── L5: LTF Confirmation (0-1) ────────────────────────────────
        ltf = tech["ltf"]
        if is_long:
            l5 = 1 if ltf.get("bull_confirm") else 0
        else:
            l5 = 1 if ltf.get("bear_confirm") else 0
        bd["L5_ltf"] = l5

        # ── L6: Fundamental (0-1) ─────────────────────────────────────
        l6 = 0
        if fund:
            mcap     = float(fund.get("mcap") or 0) or 1
            fdv      = float(fund.get("fdv") or mcap)
            fdv_r    = fdv / mcap if mcap > 0 else 1
            # vol: spot quoteVolume (USD) — proxy yang cukup untuk MCap ratio
            raw_vol  = float(ticker.get("vol") or ticker.get("quoteVolume") or 0)
            vol_mcap = (raw_vol / mcap * 100) if mcap > 0 else 0
            rs_diff  = ticker["change"] - ticker["btc_change"]

            if is_long:
                conds = [fdv_r < 1.5, vol_mcap > 3, rs_diff > 0]
            else:
                conds = [fdv_r > 2.0, vol_mcap < 1, rs_diff < -2]

            l6 = 1 if sum(conds) >= 2 else 0
        bd["L6_fundamental"] = l6

        # ── L7: On-Chain DeFi (0-1) ──────────────────────────────────
        defi_score, _ = score_defi_confluence(defi_ctx, direction)
        # -99 = token unlock hard blocker; sudah dicek di analyze() dan quick_scan()
        # Di sini normalkan ke 0 saja (trade sudah diblokir sebelum sampai sini)
        l7 = max(0, min(1, defi_score if defi_score != -99 else 0))
        bd["L7_defi"] = l7

        # ── L8: Sentiment (0-1) ───────────────────────────────────────
        l8 = max(0, min(1, pre_sent_score))
        bd["L8_sentiment"] = l8

        # ── L9: Whale Activity (0-1) ──────────────────────────────────
        l9 = max(0, min(1, pre_whale_score))
        bd["L9_whale"] = l9

        # ── L10: GC/DC from tech (0-1) ────────────────────────────────
        gc_dc = tech.get("htf", {}).get("gc_dc", {})
        if is_long and gc_dc.get("bull_bias"):
            l10 = 1
        elif not is_long and gc_dc.get("bear_bias"):
            l10 = 1
        else:
            l10 = 0
        bd["L10_gc_dc"] = l10

        total = l1 + l2 + l3 + l4 + l5 + l6 + l7 + l8 + l9 + l10
        return total, bd

    # ── Main analysis ──────────────────────────────────────────────────

    def format_signal(self, symbol, final_dir, final_score, conv,
                       ticker, tech, active_bd, fund=None) -> str:
        e   = tech["execution"]
        tp  = e["tp"]
        htf = tech["htf"]
        ema = htf["ema"]
        price  = ticker["price"]
        change = ticker["change"]
        rs     = change - ticker["btc_change"]
        mcap   = float(fund.get("mcap") or 0)/1e6 if fund else 0.0
        is_long = "LONG" in final_dir
        dir_str = "LONG" if is_long else "SHORT"
        regime  = tech.get("regime", {}).get("regime_btc", "?")
        bd = active_bd
        filled  = min(final_score, 14)
        bar     = "=" * filled + "-" * (14 - filled)
        ob     = htf.get('ob', {})
        gc_dc  = htf.get('gc_dc', {})
        ot     = e.get('order_type', 'MARKET')
        lines = [
            "=" * 34,
            f"SIGNAL: [{dir_str}] {symbol}",
            "=" * 34,
            f"Price  : ${price:.5f} ({change:+.2f}%)",
            f"MCap   : ${mcap:.0f}M  RS: {rs:+.2f}%",
            f"Regime : {regime}",
            "-" * 34,
            f"ORDER  : {ot}",
            f"ENTRY  : ${e['entry']:.5f}",
            f"SL     : ${e['sl']:.5f} ({e.get('sl_pct',0):.2f}%)",
            f"TP1(2R): ${tp['TP1']:.5f}",
            f"TP2    : ${tp['TP2']:.5f}",
            f"TP3    : ${tp['TP3']:.5f}",
            f"RR     : {e.get('rr','N/A')}",
            "-" * 34,
            f"H4 Str : {htf['structure']['structure']}",
            f"EMA    : {ema.get('trend','?')}",
            f"GC/DC  : {gc_dc.get('cross','N/A')}",
            f"OB     : Bull={'✅' if ob.get('has_bull_setup') else '❌'} Bear={'✅' if ob.get('has_bear_setup') else '❌'}",
            "-" * 34,
            f"Score  : {final_score}/{MAX_SCORE} [{bar}]",
            f"L1:{bd.get('L1_regime',0)} L2:{bd.get('L2_structure',0)} L3:{bd.get('L3_ema',0)} L4:{bd.get('L4_elliott',0)} L5:{bd.get('L5_ltf',0)}",
            f"L6:{bd.get('L6_fundamental',0)} L7:{bd.get('L7_defi',0)} L8:{bd.get('L8_sentiment',0)} L9:{bd.get('L9_whale',0)} L10:{bd.get('L10_gc_dc',0)}",
            "=" * 34,
        ]
        return "\n".join(lines)

    async def analyze(self, symbol: str, precomputed_tech=None,
                precomputed_defi=None, precomputed_sent=None,
                precomputed_ticker=None, precomputed_fund=None) -> str:
        symbol = symbol.upper()
        ticker = precomputed_ticker or await self.get_binance_ticker(symbol)
        if not ticker:
            return f"❌ {symbol}/USDT tidak ditemukan di Binance."
        ticker["symbol"] = symbol

        fund   = precomputed_fund if precomputed_fund is not None else await self.get_fundamental_data(symbol)
        news   = await self.scraper.get_catalysts(symbol)
        tech   = (precomputed_tech
                  if precomputed_tech is not None
                  else await self.tech.full_analysis(
                  symbol,
                  htf=cfg_get('tf_bias'),
                  mtf=cfg_get('tf_structure'),
                  ltf=cfg_get('tf_entry'),
                  regime_tf=cfg_get('tf_regime')
              ))

        if not tech:
            return f"❌ {symbol} — gagal fetch data teknikal."

        # ── On-chain + Sentiment ───────────────────────────────────────
        defi_ctx  = precomputed_defi or await get_full_defi_context(symbol)
        sent_ctx  = precomputed_sent or await get_full_sentiment(symbol)

        # Hard blocker: token unlock
        unlock = defi_ctx.get("unlock", {})
        if unlock.get("skip_trade"):
            return (f"⛔ {symbol} — SKIP\n"
                    f"Token Unlock Risk: {unlock.get('unlock_pct_mcap',0):.1f}% "
                    f"supply unlock dalam 30 hari.")

        # ── Precompute whale ONCE sebelum scoring ─────────────────────
        whale_ctx = await get_whale_context(symbol)
        if isinstance(sent_ctx, dict):
            sent_ctx["_whale"] = whale_ctx

        # ── Pre-compute async scores ONCE ─────────────────────────
        bull_sent, _ = await score_sentiment(symbol, "BULLISH", sent_ctx)
        bear_sent, _ = await score_sentiment(symbol, "BEARISH", sent_ctx)
        bull_whale, _ = await score_whale(symbol, "BULLISH", whale_ctx)
        bear_whale, _ = await score_whale(symbol, "BEARISH", whale_ctx)

        # ── Score per direction ────────────────────────────────────────
        long_score,  long_bd  = self._compute_score(
            "BULLISH", tech, fund, ticker, defi_ctx, bull_sent, bull_whale
        )
        short_score, short_bd = self._compute_score(
            "BEARISH", tech, fund, ticker, defi_ctx, bear_sent, bear_whale
        )

        # ── Determine final direction ──────────────────────────────────
        htf_bias = tech["htf"]["bias"]

        if long_score >= HIGH_THRESH and long_score > short_score:
            final_dir   = "LONG"
            final_score = long_score
            active_bd   = long_bd
        elif short_score >= HIGH_THRESH and short_score > long_score:
            final_dir   = "SHORT"
            final_score = short_score
            active_bd   = short_bd
        elif long_score >= MOD_THRESH or short_score >= MOD_THRESH:
            if long_score >= short_score:
                final_dir   = "LONG (Reduced)"
                final_score = long_score
                active_bd   = long_bd
            else:
                final_dir   = "SHORT (Reduced)"
                final_score = short_score
                active_bd   = short_bd
        else:
            final_dir   = "NO TRADE"
            final_score = max(long_score, short_score)
            active_bd   = long_bd if long_score >= short_score else short_bd

        # Conviction label
        if final_score >= HIGH_THRESH:
            conv = f"✅ HIGH CONVICTION ({final_score}/{MAX_SCORE})"
        elif final_score >= MOD_THRESH:
            conv = f"⚠️ MODERATE ({final_score}/{MAX_SCORE})"
        else:
            conv = f"❌ LOW ({final_score}/{MAX_SCORE})"

        # ── Snapshot metrics ───────────────────────────────────────────
        price      = ticker["price"]
        change     = ticker["change"]
        btc_change = ticker["btc_change"]
        rs_diff    = change - btc_change
        mcap       = float(fund.get("mcap") or 0) if fund else 0.0
        fdv        = float(fund.get("fdv")  or mcap) if fund else 0.0
        fdv_ratio  = fdv / mcap if mcap > 0 else 1
        raw_vol    = float(ticker.get("vol") or ticker.get("quoteVolume") or 0)
        vol_mcap   = raw_vol / mcap * 100 if mcap > 0 else 0.0

        rs_str = (f"💪 Very Strong (+{rs_diff:.1f}%)" if rs_diff > 3
                  else f"✅ Outperform (+{rs_diff:.1f}%)" if rs_diff > 0
                  else f"⚠️ Underperform ({rs_diff:.1f}%)"  if rs_diff > -3
                  else f"🔻 Bleeding ({rs_diff:.1f}%)")

        liq_str = ("🔥 High Activity" if vol_mcap > 15
                   else "✅ Deep"       if vol_mcap > 5
                   else "⚠️ Moderate"   if vol_mcap > 2
                   else "❌ Thin/Risky")

        val_str = ("❌ Overvalued (High Dilution)" if fdv_ratio > 3
                   else "⚠️ Fair-High" if fdv_ratio > 1.5
                   else "✅ Fair"      if fdv_ratio > 1
                   else "✅ Fair (Full Circ)")

        data_src = fund.get("source","?") if fund else "Binance Only"

        # ── Regime & Elliott text ──────────────────────────────────────
        regime    = tech.get("regime", {})
        elliott   = tech["htf"].get("elliott", {})
        ema       = tech["htf"]["ema"]
        wyckoff   = tech["htf"]["wyckoff"]
        h_struct  = tech["htf"]["structure"]
        ltf       = tech["ltf"]

        regime_str  = f"{regime.get('regime_btc','?')} | Coin: {regime.get('regime_coin','?')}"
        elliott_str = f"{elliott.get('wave_position','?')} — {elliott.get('note','')}"
        ema_str     = (f"EMA20:{ema.get('ema20',0):.4f} "
                       f"EMA50:{ema.get('ema50',0):.4f} "
                       f"EMA200:{ema.get('ema200',0):.4f} "
                       f"[{ema.get('trend','?')}]")

        ltf_fvg = ltf["fvg"]
        fvg_str = "Tidak ada"
        if ltf_fvg.get("bullish_fvg"):
            f_ = ltf_fvg["bullish_fvg"]
            fvg_str = f"Bullish FVG {f_['bottom']:.5f}-{f_['top']:.5f}"
        elif ltf_fvg.get("bearish_fvg"):
            f_ = ltf_fvg["bearish_fvg"]
            fvg_str = f"Bearish FVG {f_['bottom']:.5f}-{f_['top']:.5f}"

        sweeps_str = "\n".join(ltf["liquidity"]["sweeps"][:2])
        displ_str  = "\n".join(ltf["displacement"]["displacements"][:2])

        # ── Whale, DeFi & Sentiment notes ──────────────────────────────
        final_direction_str = "BULLISH" if "LONG" in final_dir else "BEARISH"
        _, whale_notes = await score_whale(symbol, final_direction_str, whale_ctx)
        whale_str = "\n".join(whale_notes) if whale_notes else "• Data tidak tersedia"

        _, defi_notes = score_defi_confluence(defi_ctx, final_direction_str)
        defi_str = "\n".join(defi_notes) if defi_notes else "• Data tidak tersedia"

        _, sent_notes = await score_sentiment(symbol, final_direction_str, sent_ctx)
        sent_str = "\n".join(sent_notes) if sent_notes else "• Data tidak tersedia"

        # ── Scoring breakdown ──────────────────────────────────────────
        bd_str = (
            f"L1:{active_bd.get('L1_regime',0)}/2 "
            f"L2:{active_bd.get('L2_structure',0)}/3 "
            f"L3:{active_bd.get('L3_ema',0)}/2 "
            f"L4:{active_bd.get('L4_elliott',0)} "
            f"L5:{active_bd.get('L5_ltf',0)}\n"
            f"L6:{active_bd.get('L6_fundamental',0)} "
            f"L7:{active_bd.get('L7_defi',0)} "
            f"L8:{active_bd.get('L8_sentiment',0)} "
            f"L9🐋:{active_bd.get('L9_whale',0)} "
            f"L10📈:{active_bd.get('L10_gc_dc',0)}"
        )

        # ── Execution plan ─────────────────────────────────────────────
        e        = tech["execution"]
        swing_info = ""
        exec_sec = "\n━━━ EXECUTION: NO TRADE ━━━\nSkor tidak memenuhi threshold."

        if (e["entry_mode"] not in ("NO TRADE","BLOCKED (GC/DC filter)")
                and "NO TRADE" not in final_dir
                and "BLOCKED" not in e["entry_mode"]):
            tp = e["tp"]
            swing_info = (
                f"\n📐 SL Distance: {e.get('sl_pct',0):.2f}% (max 3%)"
                f"\n📏 RR: {e.get('rr','N/A')} (min 1:2)"
            )
            exec_sec = f"""
━━━ EXECUTION PLAN ━━━

Mode    : {e['entry_mode']}
Entry   : ${e['entry']:.5f}
SL      : ${e['sl']:.5f} ({e.get('sl_pct',0):.2f}%)
TP1 (2R): ${tp['TP1']:.5f}
TP2 (2.618R): ${tp['TP2']:.5f}
TP3 (3.618R): ${tp['TP3']:.5f}
TP4 (4.236R): ${tp['TP4_MOON']:.5f}
R:R     : {e['rr']}"""

        catalyst_str = "\n".join(news)

        report = f"""
⚡ {symbol} — INTRADAY SIGNAL ({tech['htf']['timeframe'].upper()}/{tech['mtf']['timeframe'].upper()}/{tech['ltf']['timeframe'].upper()})
━━━ SNAPSHOT ━━━
💰 Harga   : ${price:.5f}
📊 MCap    : ${mcap/1e6:.1f}M | FDV/MCap: {fdv_ratio:.2f}
📈 24h     : {change:+.2f}% | Vol/MCap: {vol_mcap:.1f}%
📉 Valuasi : {val_str}
🔄 RS BTC  : {rs_str}
💧 Likuiditas: {liq_str}
📡 Data    : {data_src}{swing_info}

━━━ MARKET REGIME ━━━
{regime_str}

━━━ TECHNICAL (ICT/SMC) ━━━
📊 HTF ({tech['htf']['timeframe'].upper()}) — Bias
Structure : {h_struct['structure']}
BOS       : {h_struct['bos'] or 'Tidak ada BOS baru'}
EMA       : {ema_str}
EMA50 Bias: Price {ema.get('price_vs_ema50','?')} EMA50
Wyckoff   : {wyckoff['phase']} [{wyckoff['position_in_range']}]
Elliott   : {elliott_str}
HTF Bias  : {'🟢' if htf_bias=='BULLISH' else '🔴' if htf_bias=='BEARISH' else '⚪'} {htf_bias}

🎯 LTF ({tech['ltf']['timeframe'].upper()}) — Entry
Structure : {ltf['structure']['structure']}
Displacement:
{displ_str}
FVG       : {fvg_str}
Sweep     :
{sweeps_str}

━━━ ON-CHAIN (DefiLlama) ━━━
{defi_str}

━━━ WHALE ACTIVITY ━━━
{whale_str}

━━━ SENTIMENT ━━━
{sent_str}

━━━ CATALYST (News 24h) ━━━
{catalyst_str}

━━━ FINAL VERDICT ━━━
Confluence : {conv}
Score      : LONG {long_score}/{MAX_SCORE} | SHORT {short_score}/{MAX_SCORE}
Breakdown  :
{bd_str}
Bias Akhir : {final_dir}
Invalidasi : Close di bawah ${ticker['low']:.5f}
{exec_sec}
"""

        # ── Log ke journal ─────────────────────────────────────────────
        if (final_score >= HIGH_THRESH
                and "Reduced" not in final_dir
                and e["entry_mode"] not in ("NO TRADE","BLOCKED (GC/DC filter)")
                and "BLOCKED" not in e["entry_mode"]
                and e.get("rr_valid")):
            tp = e["tp"]
            try:
                log_signal(
                    symbol=symbol,
                    bias=final_dir,
                    entry=e["entry"], sl=e["sl"],
                    tp1=tp["TP1"], tp2=tp["TP2"], tp3=tp["TP3"],
                    sl_pct=e.get("sl_pct", 0),
                    rr=e.get("rr", "N/A"),
                    confidence=conv,
                    htf_bias=htf_bias,
                    defi_score=active_bd.get("L7_defi", 0),
                    notes=(f"Score:{final_score}/{MAX_SCORE} "
                           f"L1:{active_bd.get('L1_regime',0)} "
                           f"L2:{active_bd.get('L2_structure',0)}")
                )
            except Exception:
                pass

        return report

    async def scan_market_by_volume(self, min_vol: float = None,
                              max_symbols: int = None) -> list:
        """
        Scan semua pasangan USDT Perpetual dari Binance FUTURES (fapi).
        Menggunakan futures volume sebagai filter (menghindari limit CMC).
        Return: list of (symbol, volume_usd) sorted by volume desc.
        """
        if min_vol is None:
            min_vol = 10_000_000  # Default $10M daily volume
        if max_symbols is None:
            max_symbols = cfg_get("max_results")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=8) as r:
                    futures_tickers = await r.json()
        except Exception as e:
            return []

        candidates = []
        for t in futures_tickers:
            sym_raw = t.get("symbol","")
            if not sym_raw.endswith("USDT"):
                continue
            if "_" in sym_raw:
                continue
            vol24h = float(t.get("quoteVolume", 0))
            if vol24h < min_vol:
                continue
            sym = sym_raw[:-4]
            candidates.append((sym, vol24h))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:max_symbols]

    async def quick_scan(self, symbol: str) -> tuple:
        """
        Versi ringkas untuk auto-scan.
        Selalu return 7-tuple: (report|None, direction|"SKIP", score, ticker, tech, fund, breakdown)
        Semua early-exit path return: (None, "SKIP", 0, {}, {}, {}, {})
        """
        _SKIP = (None, "SKIP", 0, {}, {}, {}, {})

        symbol = symbol.upper()
        ticker = await self.get_binance_ticker(symbol)
        if not ticker:
            return _SKIP
        ticker["symbol"] = symbol

        # Fetch technical SEKALI
        tech = await self.tech.full_analysis(
            symbol,
            htf=cfg_get('tf_bias'),
            mtf=cfg_get('tf_structure'),
            ltf=cfg_get('tf_entry'),
            regime_tf=cfg_get('tf_regime')
        )
        if not tech:
            return _SKIP

        htf_bias   = tech["htf"]["bias"]
        confidence = tech["execution"]["confidence"]
        change     = ticker["change"]
        btc_change = ticker["btc_change"]
        rs_diff    = change - btc_change

        # Pre-filter cepat
        if confidence == "LOW":
            return _SKIP

        if htf_bias == "BULLISH" and rs_diff > -1 and change > -2:
            direction = "LONG"
        elif htf_bias == "BEARISH" and rs_diff < 1 and change < 2:
            direction = "SHORT"
        else:
            return _SKIP

        # On-chain blocker
        defi_ctx = await get_full_defi_context(symbol)
        unlock   = defi_ctx.get("unlock", {})
        if unlock.get("skip_trade"):
            return _SKIP

        # Full score
        fund     = await self.get_fundamental_data(symbol)
        sent_ctx  = await get_full_sentiment(symbol)
        whale_ctx = await get_whale_context(symbol)
        dir_str = "BULLISH" if direction == "LONG" else "BEARISH"
        pre_sent, _ = await score_sentiment(symbol, dir_str, sent_ctx)
        pre_whale, _ = await score_whale(symbol, dir_str, whale_ctx)
        score, bd = self._compute_score(
            dir_str, tech, fund, ticker, defi_ctx, pre_sent, pre_whale
        )

        if score < MOD_THRESH:
            return _SKIP

        # Generate full report
        report = await self.analyze(
            symbol,
            precomputed_tech=tech,
            precomputed_defi=defi_ctx,
            precomputed_sent=sent_ctx,
            precomputed_ticker=ticker,
            precomputed_fund=fund,
        )
        return report, direction, score, ticker, tech, fund, bd


if __name__ == "__main__":
    a = CryptoBiasAnalyzer()
    import asyncio
    print(asyncio.run(a.analyze("BTC")))
