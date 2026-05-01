"""
ANALYZER — Unified Swing Trade Signal Engine
Scoring: 12-point directional confluence system
  L1: Regime(0-2) L2: Structure(0-3) L3: EMA(0-2)
  L4: Elliott(0-1) L5: LTF(0-1) L6: Fund(0-1)
  L7: DeFi(0-1)   L8: Sentiment(0-1)
  HIGH CONVICTION >= 8 | MODERATE 5-7 | NO TRADE < 5
"""
import requests
import ccxt
import xml.etree.ElementTree as ET
from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, CMC_API_KEY
from technical import TechnicalEngine
from defillama import get_full_defi_context, score_defi_confluence
from sentiment import get_full_sentiment, score_sentiment
from journal import log_signal, log_scan_run

MAX_SCORE   = 12
HIGH_THRESH = 8
MOD_THRESH  = 5


class CryptoNewsScraper:
    def get_catalysts(self, symbol):
        catalysts = []
        try:
            url = (f"https://news.google.com/rss/search"
                   f"?q={symbol}+crypto+when:24h&hl=en-US&gl=US&ceid=US:en")
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                for item in root.findall(".//item")[:2]:
                    title = item.find("title").text or ""
                    catalysts.append(f"• {title.split(' - ')[0].strip()}")
        except Exception:
            pass
        return catalysts or ["• Tidak ada katalis signifikan 24h terakhir"]


class CryptoBiasAnalyzer:

    def __init__(self):
        self.binance = ccxt.binance({
            "apiKey":  BINANCE_API_KEY,
            "secret":  BINANCE_SECRET_KEY,
        })
        self.tech    = TechnicalEngine(self.binance)
        self.scraper = CryptoNewsScraper()
        self.cg_url  = "https://api.coingecko.com/api/v3"
        self.cmc_url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        self.cmc_hdr = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}

    # ── Data providers ────────────────────────────────────────────────────

    def get_fundamental_data(self, symbol):
        """CMC primary → CoinGecko fallback."""
        try:
            r = requests.get(self.cmc_url, headers=self.cmc_hdr,
                             params={"symbol": symbol, "convert": "USD"}, timeout=6)
            data = r.json()
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
        try:
            search = requests.get(f"{self.cg_url}/search?query={symbol}", timeout=5).json()
            if search.get("coins"):
                cid  = search["coins"][0]["id"]
                coin = requests.get(
                    f"{self.cg_url}/coins/{cid}"
                    "?localization=false&tickers=false&market_data=true"
                    "&community_data=false&developer_data=false&sparkline=false",
                    timeout=5
                ).json()
                md = coin.get("market_data", {})
                return {
                    "mcap":     md.get("market_cap", {}).get("usd", 0),
                    "fdv":      md.get("fully_diluted_valuation", {}).get("usd", 0),
                    "vol24h":   md.get("total_volume", {}).get("usd", 0),
                    "change24h":md.get("price_change_percentage_24h", 0),
                    "change7d": md.get("price_change_percentage_7d", 0),
                    "source":   "CoinGecko",
                }
        except Exception:
            pass
        return None

    def get_binance_ticker(self, symbol):
        try:
            t   = self.binance.fetch_ticker(f"{symbol}/USDT")
            btc = self.binance.fetch_ticker("BTC/USDT")
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
                       ticker: dict, defi_ctx: dict, sent_ctx: dict) -> tuple[int, dict]:
        """
        Hitung skor 12-poin untuk satu arah (BULLISH atau BEARISH).
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
            if "BULLISH (HH + HL)" in s_str:   base_l2 = 2
            elif "CHoCH BULLISH"   in s_str:   base_l2 = 1
            elif "BEARISH"         in s_str:   base_l2 = -1  # aktif penalti
        else:
            if "BEARISH (LH + LL)" in s_str:   base_l2 = 2
            elif "CHoCH BEARISH"   in s_str:   base_l2 = 1
            elif "BULLISH"         in s_str:   base_l2 = -1

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
            mcap     = fund.get("mcap", 0) or 1
            fdv      = fund.get("fdv",  mcap)
            fdv_r    = fdv / mcap if mcap > 0 else 1
            vol_mcap = (ticker["vol"] / mcap * 100) if mcap > 0 else 0
            rs_diff  = ticker["change"] - ticker["btc_change"]

            if is_long:
                conds = [fdv_r < 1.5, vol_mcap > 3, rs_diff > 0]
            else:
                conds = [fdv_r > 2.0, vol_mcap < 1, rs_diff < -2]

            l6 = 1 if sum(conds) >= 2 else 0
        bd["L6_fundamental"] = l6

        # ── L7: On-Chain DeFi (0-1, -99 blocker sudah di luar) ────────
        defi_score, _ = score_defi_confluence(defi_ctx, direction)
        l7 = max(0, min(1, defi_score))
        bd["L7_defi"] = l7

        # ── L8: Sentiment (0-1) ───────────────────────────────────────
        sent_score, _ = score_sentiment(
            ticker.get("symbol", ""), direction, sent_ctx
        )
        l8 = max(0, min(1, sent_score))
        bd["L8_sentiment"] = l8

        total = l1 + l2 + l3 + l4 + l5 + l6 + l7 + l8
        return total, bd

    # ── Main analysis ──────────────────────────────────────────────────

    def analyze(self, symbol: str, precomputed_tech=None,
                precomputed_defi=None, precomputed_sent=None) -> str:
        symbol = symbol.upper()
        ticker = self.get_binance_ticker(symbol)
        if not ticker:
            return f"❌ {symbol}/USDT tidak ditemukan di Binance."
        ticker["symbol"] = symbol

        fund   = self.get_fundamental_data(symbol)
        news   = self.scraper.get_catalysts(symbol)
        tech   = (precomputed_tech
                  if precomputed_tech is not None
                  else self.tech.full_analysis(symbol))

        if not tech:
            return f"❌ {symbol} — gagal fetch data teknikal."

        # ── On-chain + Sentiment ───────────────────────────────────────
        defi_ctx  = precomputed_defi or get_full_defi_context(symbol)
        sent_ctx  = precomputed_sent or get_full_sentiment(symbol)

        # Hard blocker: token unlock
        unlock = defi_ctx.get("unlock", {})
        if unlock.get("skip_trade"):
            return (f"⛔ {symbol} — SKIP\n"
                    f"Token Unlock Risk: {unlock.get('unlock_pct_mcap',0):.1f}% "
                    f"supply unlock dalam 30 hari.")

        # ── Score per direction ────────────────────────────────────────
        long_score,  long_bd  = self._compute_score(
            "BULLISH", tech, fund, ticker, defi_ctx, sent_ctx
        )
        short_score, short_bd = self._compute_score(
            "BEARISH", tech, fund, ticker, defi_ctx, sent_ctx
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
        mcap       = fund["mcap"]  if fund else 0
        fdv        = fund["fdv"]   if fund else 0
        fdv_ratio  = fdv / mcap    if mcap > 0 else 1
        vol_mcap   = ticker["vol"] / mcap * 100 if mcap > 0 else 0

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

        data_src = fund["source"] if fund else "Binance Only"

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

        # ── DeFi & Sentiment notes ─────────────────────────────────────
        _, defi_notes = score_defi_confluence(
            defi_ctx,
            "BULLISH" if "LONG" in final_dir else "BEARISH"
        )
        defi_str = "\n".join(defi_notes) if defi_notes else "• Data tidak tersedia"

        _, sent_notes = score_sentiment(
            symbol,
            "BULLISH" if "LONG" in final_dir else "BEARISH",
            sent_ctx
        )
        sent_str = "\n".join(sent_notes) if sent_notes else "• Data tidak tersedia"

        # ── Scoring breakdown ──────────────────────────────────────────
        bd_str = (
            f"L1 Regime:{active_bd.get('L1_regime',0)}/2  "
            f"L2 Struct:{active_bd.get('L2_structure',0)}/3  "
            f"L3 EMA:{active_bd.get('L3_ema',0)}/2\n"
            f"L4 Elliott:{active_bd.get('L4_elliott',0)}/1  "
            f"L5 LTF:{active_bd.get('L5_ltf',0)}/1  "
            f"L6 Fund:{active_bd.get('L6_fundamental',0)}/1  "
            f"L7 DeFi:{active_bd.get('L7_defi',0)}/1  "
            f"L8 Sent:{active_bd.get('L8_sentiment',0)}/1"
        )

        # ── Execution plan ─────────────────────────────────────────────
        e        = tech["execution"]
        swing_info = ""
        exec_sec = "\n━━━ EXECUTION: NO TRADE ━━━\nSkor tidak memenuhi threshold."

        if e["entry_mode"] != "NO TRADE" and "NO TRADE" not in final_dir:
            tp = e["tp"]
            swing_info = (
                f"\n📐 SL Distance: {e.get('sl_pct',0):.2f}% (max 5%)"
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
🚀 {symbol} — SWING SIGNAL REPORT
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
📊 HTF (1D)
Structure : {h_struct['structure']}
BOS       : {h_struct['bos'] or 'Tidak ada BOS baru'}
EMA       : {ema_str}
EMA50 Bias: Price {ema.get('price_vs_ema50','?')} EMA50
Wyckoff   : {wyckoff['phase']} [{wyckoff['position_in_range']}]
Elliott   : {elliott_str}
HTF Bias  : {'🟢' if htf_bias=='BULLISH' else '🔴' if htf_bias=='BEARISH' else '⚪'} {htf_bias}

🎯 LTF (4H)
Structure : {ltf['structure']['structure']}
Displacement:
{displ_str}
FVG       : {fvg_str}
Sweep     :
{sweeps_str}

━━━ ON-CHAIN (DefiLlama) ━━━
{defi_str}

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
        if ("NO TRADE" not in final_dir
                and e["entry_mode"] != "NO TRADE"
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

    def quick_scan(self, symbol: str) -> tuple:
        """
        Versi ringkas untuk auto-scan.
        Selalu return 3 nilai: (report|None, bias|"SKIP", score)
        """
        symbol = symbol.upper()
        ticker = self.get_binance_ticker(symbol)
        if not ticker:
            return None, "SKIP", 0
        ticker["symbol"] = symbol

        # Fetch technical SEKALI
        tech = self.tech.full_analysis(symbol)
        if not tech:
            return None, "SKIP", 0

        htf_bias   = tech["htf"]["bias"]
        confidence = tech["execution"]["confidence"]
        change     = ticker["change"]
        btc_change = ticker["btc_change"]
        rs_diff    = change - btc_change

        # Pre-filter cepat
        if confidence == "LOW":
            return None, "SKIP", 0

        if htf_bias == "BULLISH" and rs_diff > -1 and change > -2:
            direction = "LONG"
        elif htf_bias == "BEARISH" and rs_diff < 1 and change < 2:
            direction = "SHORT"
        else:
            return None, "SKIP", 0

        # On-chain blocker
        defi_ctx = get_full_defi_context(symbol)
        unlock   = defi_ctx.get("unlock", {})
        if unlock.get("skip_trade"):
            return None, "SKIP", 0

        # Full score
        fund     = self.get_fundamental_data(symbol)
        sent_ctx = get_full_sentiment(symbol)
        score, _ = self._compute_score(
            "BULLISH" if direction == "LONG" else "BEARISH",
            tech, fund, ticker, defi_ctx, sent_ctx
        )

        if score < MOD_THRESH:
            return None, "SKIP", 0

        # Generate full report (pass precomputed untuk tidak double-fetch)
        report = self.analyze(
            symbol,
            precomputed_tech=tech,
            precomputed_defi=defi_ctx,
            precomputed_sent=sent_ctx,
        )
        return report, direction, score


if __name__ == "__main__":
    a = CryptoBiasAnalyzer()
    print(a.analyze("BTC"))
