"""
ANALYZER — Combined Fundamental Bias + ICT/SMC Technical Engine
Implements the full CRYPTO FUNDAMENTAL BIAS (FLOW VALIDATION BASED) directive
with HL GOLD CORE Veracity Engine technical overlay.
"""
import requests
import ccxt
import xml.etree.ElementTree as ET
from config import *
from technical import TechnicalEngine


class CryptoNewsScraper:
    """RSS-based news scraper for catalyst detection."""

    def get_catalysts(self, symbol):
        catalysts = []
        try:
            url = f"https://news.google.com/rss/search?q={symbol}+crypto+when:24h&hl=en-US&gl=US&ceid=US:en"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                for item in root.findall(".//item")[:2]:
                    title = item.find("title").text
                    catalysts.append(f"• {title.split(' - ')[0].strip()}")
        except Exception:
            pass
        return catalysts if catalysts else ["• Tidak ada katalis signifikan 24h terakhir"]


class CryptoBiasAnalyzer:
    """Main analyzer combining fundamental + technical analysis."""

    def __init__(self):
        self.binance = ccxt.binance({
            "apiKey": BINANCE_API_KEY,
            "secret": BINANCE_SECRET_KEY,
        })
        self.tech = TechnicalEngine(self.binance)
        self.scraper = CryptoNewsScraper()
        self.cg_url = "https://api.coingecko.com/api/v3"
        self.cmc_url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        self.cmc_headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}

    # ---- Data Providers ---- #
    def get_fundamental_data(self, symbol):
        """Try CMC first, fallback to CoinGecko."""
        try:
            r = requests.get(self.cmc_url, headers=self.cmc_headers,
                             params={"symbol": symbol, "convert": "USD"}, timeout=5)
            data = r.json()
            if "data" in data and symbol in data["data"]:
                q = data["data"][symbol]["quote"]["USD"]
                return {
                    "mcap": q.get("market_cap", 0),
                    "fdv": q.get("fully_diluted_market_cap", q.get("market_cap", 0)),
                    "vol24h": q.get("volume_24h", 0),
                    "change24h": q.get("percent_change_24h", 0),
                    "source": "CMC"
                }
        except Exception:
            pass

        # Fallback CoinGecko
        try:
            search = requests.get(f"{self.cg_url}/search?query={symbol}", timeout=5).json()
            if search.get("coins"):
                cid = search["coins"][0]["id"]
                coin = requests.get(
                    f"{self.cg_url}/coins/{cid}?localization=false&tickers=false"
                    "&market_data=true&community_data=false&developer_data=false&sparkline=false",
                    timeout=5
                ).json()
                md = coin.get("market_data", {})
                return {
                    "mcap": md.get("market_cap", {}).get("usd", 0),
                    "fdv": md.get("fully_diluted_valuation", {}).get("usd", 0),
                    "vol24h": md.get("total_volume", {}).get("usd", 0),
                    "change24h": md.get("price_change_percentage_24h", 0),
                    "source": "CoinGecko"
                }
        except Exception:
            pass
        return None

    def get_binance_ticker(self, symbol):
        try:
            t = self.binance.fetch_ticker(f"{symbol}/USDT")
            btc = self.binance.fetch_ticker("BTC/USDT")
            return {
                "price": t["last"],
                "vol": t["quoteVolume"],
                "change": t["percentage"],
                "btc_change": btc["percentage"],
                "high": t["high"],
                "low": t["low"],
            }
        except Exception:
            return None

    # ---- Main Analysis ---- #
    def analyze(self, symbol, precomputed_tech=None):
        symbol = symbol.upper()
        ticker = self.get_binance_ticker(symbol)
        if not ticker:
            return f"❌ {symbol}/USDT tidak ditemukan di Binance."

        fund = self.get_fundamental_data(symbol)
        news = self.scraper.get_catalysts(symbol)
        tech = precomputed_tech if precomputed_tech is not None else self.tech.full_analysis(symbol)

        # --- Fundamental Metrics --- #
        price = ticker["price"]
        change = ticker["change"]
        btc_change = ticker["btc_change"]
        mcap = fund["mcap"] if fund else 0
        fdv = fund["fdv"] if fund else 0
        fdv_ratio = fdv / mcap if mcap > 0 else 1
        vol_mcap = (ticker["vol"] / mcap * 100) if mcap > 0 else 0

        # RS Score (Enhanced)
        rs_diff = change - btc_change
        if rs_diff > 3:
            rs = "💪 Very Strong (Outperform BTC +" + f"{rs_diff:.1f}%)"
        elif rs_diff > 0:
            rs = "✅ Strong (Slight Outperform)"
        elif rs_diff > -3:
            rs = "⚠️ Weak (Slight Underperform)"
        else:
            rs = "🔻 Very Weak (Bleeding vs BTC " + f"{rs_diff:.1f}%)"

        # Liquidity
        if vol_mcap > 15:
            liq = "🔥 High Activity"
        elif vol_mcap > 5:
            liq = "✅ Deep"
        elif vol_mcap > 2:
            liq = "⚠️ Moderate"
        else:
            liq = "❌ Thin/Risky"

        # Valuation
        if fdv_ratio > 3:
            val = "❌ Overvalued (High Dilution)"
        elif fdv_ratio > 1.5:
            val = "⚠️ Fair-High"
        elif fdv_ratio > 1:
            val = "✅ Fair"
        elif fdv_ratio == 1:
            val = "✅ Fair (Full Circulating)"
        else:
            val = "⚠️ Data Error (FDV < MCap)"

        # --- Technical Section --- #
        tech_section = ""
        exec_section = ""
        if tech:
            h = tech["htf"]
            l = tech["ltf"]
            e = tech["execution"]

            # Sweeps formatting
            sweeps_str = "\n".join(l["liquidity"]["sweeps"][:2])
            displ_str = "\n".join(l["displacement"]["displacements"][:2])

            # FVG
            ltf_fvg = l["fvg"]
            fvg_str = "Tidak ada"
            if ltf_fvg["bullish_fvg"]:
                f = ltf_fvg["bullish_fvg"]
                fvg_str = f"Bullish FVG: {f['bottom']:.5f} - {f['top']:.5f}"
            elif ltf_fvg["bearish_fvg"]:
                f = ltf_fvg["bearish_fvg"]
                fvg_str = f"Bearish FVG: {f['bottom']:.5f} - {f['top']:.5f}"

            tech_section = f"""
━━━ TECHNICAL (ICT/SMC) ━━━

📊 HTF ({h['timeframe']}) CONTEXT
Structure: {h['structure']['structure']}
BOS: {h['structure']['bos'] or 'Tidak ada BOS baru'}
Wyckoff: {h['wyckoff']['phase']}
Position in Range: {h['wyckoff']['position_in_range']}
EMA20 Bias: {h['ema']['bias']} (Price {h['ema']['price_vs_ema']})
HTF Bias: {'🟢' if h['bias']=='BULLISH' else '🔴' if h['bias']=='BEARISH' else '⚪'} {h['bias']}

🎯 LTF ({l['timeframe']}) EXECUTION
Structure: {l['structure']['structure']}
Displacement:
{displ_str}
FVG: {fvg_str}
Liquidity Sweep:
{sweeps_str}"""

            if e["entry_mode"] != "NO TRADE":
                tp = e["tp"]
                exec_section = f"""
━━━ EXECUTION PLAN ━━━

Mode: {e['entry_mode']}
Confidence: {e['confidence']}
Entry: ${e['entry']:.5f}
SL: ${e['sl']:.5f}
TP1 (1R): ${tp['TP1']:.5f}
TP2 (1.618): ${tp['TP2']:.5f}
TP3 (2.618): ${tp['TP3']:.5f}
TP4 (4.236): ${tp['TP4_MOON']:.5f}
R:R = {e['rr']}"""
            else:
                exec_section = "\n━━━ EXECUTION: NO TRADE ━━━\nAlasan: Tidak ada alignment HTF + LTF."

        # --- Confluence & Decision --- #
        confluence = 0
        fund_bias = "Neutral"
        if fdv_ratio < 1.5 and vol_mcap > 3:
            fund_bias = "Bullish"
            confluence += 1
        elif fdv_ratio > 2 and vol_mcap < 2:
            fund_bias = "Bearish"

        flow_positive = change > 0 and vol_mcap > 3
        if flow_positive:
            confluence += 1

        if rs_diff > 0:
            confluence += 1

        tech_aligned = False
        if tech:
            if tech["htf"]["bias"] == "BULLISH" and change > 0:
                tech_aligned = True
                confluence += 1
            elif tech["htf"]["bias"] == "BEARISH" and change < 0:
                tech_aligned = True
                confluence += 1

        # Final Bias — max_score dinamis sesuai data tersedia
        max_score = 3 + (1 if fund else 0)
        if confluence >= 3:
            final_bias = "LONG" if change > 0 else "SHORT"
            conv_label = f"✅ HIGH CONVICTION ({confluence}/{max_score})"
        elif confluence == 2:
            final_bias = "LONG (Reduced)" if change > 0 else "SHORT (Reduced)"
            conv_label = f"⚠️ MODERATE ({confluence}/{max_score})"
        else:
            final_bias = "NO TRADE"
            conv_label = f"❌ LOW ({confluence}/{max_score})"

        # --- Build Report --- #
        catalyst_str = "\n".join(news)
        data_src = fund["source"] if fund else "Binance Only"

        report = f"""
🚀 {symbol} — BIAS REPORT
━━━ SNAPSHOT ━━━

💰 Harga: ${price:.5f}
📊 MCap: ${mcap/1e6:.1f}M | FDV/MCap: {fdv_ratio:.2f}
📈 24h: {change:+.2f}% | Vol/MCap: {vol_mcap:.1f}%
📉 Valuasi: {val}
🔄 RS vs BTC: {rs}
💧 Liquidity: {liq}
📡 Data: {data_src}

━━━ FUNDAMENTAL ━━━

Bias: {fund_bias}
Dilution Risk: {"Tinggi" if fdv_ratio > 2 else "Rendah"}
Supply Pressure: {"Berat" if fdv_ratio > 3 else "Ringan"}
{tech_section}

━━━ CATALYST (News Scraper) ━━━
{catalyst_str}

━━━ FINAL VERDICT ━━━

Confluence: {conv_label}
Alignment: {"✅ SEARAH" if tech_aligned else "❌ DIVERGEN"}
Bias Akhir: {final_bias}
Invalidation: Break ${ticker['low']:.5f}
{exec_section}
"""
        return report

    def quick_scan(self, symbol):
        """Versi ringkas untuk auto-scan (hanya output jika ada sinyal).
        FIXED: full_analysis sekarang hanya dipanggil 1x, hasilnya di-pass ke analyze().
        """
        symbol = symbol.upper()
        ticker = self.get_binance_ticker(symbol)
        if not ticker:
            return None, "SKIP"

        change = ticker["change"]
        btc_change = ticker["btc_change"]
        rs_diff = change - btc_change

        # Panggil full_analysis SEKALI saja
        tech = self.tech.full_analysis(symbol)

        htf_bias = tech["htf"]["bias"] if tech else "NEUTRAL"
        confidence = tech["execution"]["confidence"] if tech else "LOW"

        # Quick decision — filter sebelum generate full report
        if htf_bias == "BULLISH" and rs_diff > 0 and change > 0:
            bias = "LONG"
        elif htf_bias == "BEARISH" and rs_diff < 0 and change < 0:
            bias = "SHORT"
        else:
            return None, "SKIP"

        if confidence == "LOW":
            return None, "SKIP"

        # Pass tech yang sudah ada ke analyze() supaya tidak dipanggil ulang
        return self.analyze(symbol, precomputed_tech=tech), bias


if __name__ == "__main__":
    a = CryptoBiasAnalyzer()
    print(a.analyze("BTC"))
