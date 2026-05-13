"""
SENTIMENT ENGINE
CMC API Key  → global metrics (BTC dominance, total market cap trend), trending coins
Binance API  → Futures public endpoints: funding rate, open interest, long/short ratio, taker ratio
               (semua public — tidak butuh API key)
"""

import aiohttp
import asyncio
import time
from config import CMC_API_KEY

TIMEOUT = aiohttp.ClientTimeout(total=6)
HEADERS_CMC = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}

_cache: dict = {}
_CACHE_MAX_ENTRIES = 500   # evict oldest when exceeded

def purge_cache(max_age_s: int = 7200):
    """Hapus entri cache yang sudah kadaluarsa. Dipanggil dari daily_reset_loop."""
    now = time.time()
    expired = [k for k, v in _cache.items() if now - v["ts"] > max_age_s]
    for k in expired:
        del _cache[k]

async def _get(url: str, headers: dict = None, params: dict = None, ttl: int = 900) -> dict | None:
    key = url + str(params)
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < ttl:
        return _cache[key]["data"]
    # Auto-evict if cache grows too large
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        purge_cache()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=TIMEOUT) as r:
                if r.status == 200:
                    data = await r.json()
                    _cache[key] = {"data": data, "ts": now}
                    return data
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 1. CMC — Global Market Metrics & Trending
#    API Key: CMC_API_KEY
# ══════════════════════════════════════════════════════════════════════════════

async def get_cmc_global_metrics() -> dict:
    """
    CMC /global-metrics/quotes/latest
    → Total market cap, BTC dominance, ETH dominance, DeFi dominance
    Digunakan untuk: regime classification + macro sentiment
    """
    data = await _get(
        "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest",
        headers=HEADERS_CMC,
        ttl=1800  # cache 30 menit
    )
    if not data or "data" not in data:
        return {}

    d = data["data"]
    q = d.get("quote", {}).get("USD", {})

    return {
        "total_market_cap":        q.get("total_market_cap", 0),
        "total_volume_24h":        q.get("total_volume_24h", 0),
        "total_market_cap_change_24h": q.get("total_market_cap_yesterday_percentage_change", 0),
        "btc_dominance":           d.get("btc_dominance", 0),
        "btc_dominance_change_24h": d.get("btc_dominance_24h_percentage_change", 0),
        "eth_dominance":           d.get("eth_dominance", 0),
        "defi_volume_24h":         d.get("defi_volume_24h", 0),
        "defi_market_cap":         d.get("defi_market_cap", 0),
        "active_cryptocurrencies": d.get("active_cryptocurrencies", 0),
    }


async def get_cmc_trending(symbol: str) -> bool:
    """
    CMC /cryptocurrency/trending/latest
    Cek apakah simbol masuk top trending — sentimen positif jika ya.
    API Key: CMC_API_KEY
    """
    data = await _get(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/trending/latest",
        headers=HEADERS_CMC,
        ttl=3600
    )
    if not data or "data" not in data:
        return False
    trending_syms = {c.get("symbol", "").upper() for c in data["data"]}
    return symbol.upper() in trending_syms


async def get_cmc_fear_greed_proxy(symbol: str) -> dict:
    """
    CMC tidak punya Fear & Greed resmi, tapi kita bisa proxy dari:
    - 7d + 30d price change
    - Volume vs market cap ratio
    - RS vs BTC dominance arah
    """
    data = await _get(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest",
        headers=HEADERS_CMC,
        params={"symbol": symbol, "convert": "USD"},
        ttl=900
    )
    if not data or "data" not in data or symbol not in data["data"]:
        return {}

    q = data["data"][symbol]["quote"]["USD"]
    change_7d  = q.get("percent_change_7d", 0)
    change_30d = q.get("percent_change_30d", 0)
    change_90d = q.get("percent_change_90d", 0)

    # Proxy score: -3 to +3
    fg_score = 0
    if change_7d > 10:  fg_score += 1
    elif change_7d < -10: fg_score -= 1
    if change_30d > 20:  fg_score += 1
    elif change_30d < -20: fg_score -= 1
    if change_90d > 0:   fg_score += 1
    elif change_90d < 0: fg_score -= 1

    if fg_score >= 2:    label = "GREED"
    elif fg_score == 1:  label = "MILD GREED"
    elif fg_score == 0:  label = "NEUTRAL"
    elif fg_score == -1: label = "MILD FEAR"
    else:                label = "FEAR"

    return {
        "fg_score": fg_score,
        "fg_label": label,
        "change_7d": change_7d,
        "change_30d": change_30d,
        "change_90d": change_90d,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. BINANCE — Futures Sentiment (Public Endpoints, No API Key)
# ══════════════════════════════════════════════════════════════════════════════

FAPI = "https://fapi.binance.com/fapi/v1"

async def get_funding_rate(symbol: str) -> dict:
    """
    Binance Futures Funding Rate (public, no auth).
    Positive = longs membayar shorts (bully tapi bisa overheated)
    Negative = shorts membayar longs (bearish atau fear)
    """
    data = await _get(
        f"{FAPI}/fundingRate",
        params={"symbol": f"{symbol.upper()}USDT", "limit": 3},
        ttl=600  # cache 10 menit
    )
    if not data or not isinstance(data, list) or not data:
        return {"available": False}

    latest = float(data[-1].get("fundingRate", 0))
    avg    = sum(float(d.get("fundingRate", 0)) for d in data) / len(data)

    if latest > 0.001:      sentiment = "OVERHEATED_LONG"   # >0.1% = jauhi long
    elif latest > 0.0001:   sentiment = "MILD_LONG"
    elif latest > -0.0001:  sentiment = "NEUTRAL"
    elif latest > -0.001:   sentiment = "MILD_SHORT"
    else:                   sentiment = "OVERSOLD_SHORT"    # < -0.1% = squeeze risk

    return {
        "available":      True,
        "funding_rate":   round(latest * 100, 4),   # dalam persen
        "avg_3session":   round(avg * 100, 4),
        "sentiment":      sentiment,
    }


async def get_open_interest(symbol: str) -> dict:
    """
    Binance Futures Open Interest (public).
    OI naik + harga naik = strong trend
    OI naik + harga turun = short squeeze potential
    OI turun = position unwinding
    """
    data = await _get(
        f"{FAPI}/openInterest",
        params={"symbol": f"{symbol.upper()}USDT"},
        ttl=300
    )
    if not data:
        return {"available": False}

    return {
        "available":      True,
        "open_interest":  float(data.get("openInterest", 0)),
        "timestamp":      data.get("time", 0),
    }


async def get_long_short_ratio(symbol: str) -> dict:
    """
    Binance Global Long/Short Account Ratio (public).
    >1 = lebih banyak long accounts
    <1 = lebih banyak short accounts
    Contrarian signal: extreme ratio sering mendahului reversal
    """
    data = await _get(
        f"{FAPI}/globalLongShortAccountRatio",
        params={"symbol": f"{symbol.upper()}USDT", "period": "4h", "limit": 3},
        ttl=600
    )
    if not data or not isinstance(data, list) or not data:
        return {"available": False}

    latest  = float(data[-1].get("longShortRatio", 1.0))
    ls_long = float(data[-1].get("longAccount", 0.5))
    ls_short = float(data[-1].get("shortAccount", 0.5))

    if latest > 1.5:       label = "HEAVILY_LONG (contrarian risk)"
    elif latest > 1.1:     label = "LONG_BIAS"
    elif latest > 0.9:     label = "BALANCED"
    elif latest > 0.6:     label = "SHORT_BIAS"
    else:                  label = "HEAVILY_SHORT (squeeze risk)"

    return {
        "available":   True,
        "lsr":         round(latest, 3),
        "long_pct":    round(ls_long * 100, 1),
        "short_pct":   round(ls_short * 100, 1),
        "label":       label,
    }


async def get_taker_ratio(symbol: str) -> dict:
    """
    Binance Taker Buy/Sell Volume Ratio (public).
    >1 = taker aggressor lebih banyak beli → bullish pressure
    <1 = taker aggressor lebih banyak jual → bearish pressure
    """
    data = await _get(
        "https://fapi.binance.com/futures/data/takerlongshortRatio",
        params={"symbol": f"{symbol.upper()}USDT", "period": "4h", "limit": 3},
        ttl=600
    )
    if not data or not isinstance(data, list) or not data:
        return {"available": False}

    ratios = [float(d.get("buySellRatio", 1.0)) for d in data]
    avg    = sum(ratios) / len(ratios) if ratios else 1.0

    return {
        "available":     True,
        "taker_ratio":   round(ratios[-1], 3),
        "avg_3period":   round(avg, 3),
        "bullish_pressure": avg > 1.02,
        "bearish_pressure": avg < 0.98,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MASTER — Combined Sentiment Score
# ══════════════════════════════════════════════════════════════════════════════

async def get_full_sentiment(symbol: str) -> dict:
    """Kumpulkan semua data sentiment untuk 1 simbol."""
    gm, fg, tr, fr, lsr, tr_ratio = await asyncio.gather(
        get_cmc_global_metrics(),
        get_cmc_fear_greed_proxy(symbol),
        get_cmc_trending(symbol),
        get_funding_rate(symbol),
        get_long_short_ratio(symbol),
        get_taker_ratio(symbol)
    )
    return {
        "symbol":        symbol,
        "global_metrics": gm,
        "fear_greed":    fg,
        "trending":      tr,
        "funding":       fr,
        "lsr":           lsr,
        "taker":         tr_ratio,
    }


async def score_sentiment(symbol: str, direction: str, ctx: dict = None) -> tuple[int, list]:
    """
    Hitung sentiment score (0 atau 1) untuk arah tertentu.
    direction: 'BULLISH' atau 'BEARISH'

    Returns: (score 0-1, [notes])
    """
    if ctx is None:
        ctx = await get_full_sentiment(symbol)

    score  = 0
    points = 0
    total  = 0
    notes  = []

    is_long = direction == "BULLISH"

    # 1. Funding Rate
    funding = ctx.get("funding", {})
    if funding.get("available"):
        total += 1
        fr = funding["funding_rate"]
        sent = funding["sentiment"]
        if is_long:
            if sent == "OVERHEATED_LONG":
                notes.append(f"⚠️ Funding {fr:+.4f}% (overheated — long risk)")
            elif sent in ("NEUTRAL", "MILD_LONG"):
                points += 1
                notes.append(f"✅ Funding {fr:+.4f}% (sehat untuk long)")
            elif sent in ("MILD_SHORT", "OVERSOLD_SHORT"):
                points += 1
                notes.append(f"✅ Funding negatif {fr:+.4f}% (squeeze potential)")
        else:  # SHORT
            if sent == "OVERSOLD_SHORT":
                notes.append(f"⚠️ Funding {fr:+.4f}% (oversold — short risk)")
            elif sent in ("MILD_LONG", "OVERHEATED_LONG"):
                points += 1
                notes.append(f"✅ Funding positif {fr:+.4f}% (short opportunity)")
            else:
                notes.append(f"• Funding {fr:+.4f}% (neutral)")

    # 2. Long/Short Ratio
    lsr_data = ctx.get("lsr", {})
    if lsr_data.get("available"):
        total += 1
        lsr = lsr_data["lsr"]
        if is_long and 0.8 <= lsr <= 1.3:  # Tidak terlalu extreme
            points += 1
            notes.append(f"✅ LSR {lsr} (balanced — kondisi sehat)")
        elif not is_long and lsr > 1.5:     # Terlalu banyak long = short opportunity
            points += 1
            notes.append(f"✅ LSR {lsr} (heavily long — contrarian short signal)")
        elif is_long and lsr < 0.7:         # Extreme short = potential squeeze
            points += 1
            notes.append(f"✅ LSR {lsr} (heavily short — potential long squeeze up)")
        else:
            notes.append(f"• LSR {lsr} ({lsr_data['label']})")

    # 3. Taker Ratio
    taker = ctx.get("taker", {})
    if taker.get("available"):
        total += 1
        tr = taker["taker_ratio"]
        if is_long and taker.get("bullish_pressure"):
            points += 1
            notes.append(f"✅ Taker ratio {tr} (buy pressure aktif)")
        elif not is_long and taker.get("bearish_pressure"):
            points += 1
            notes.append(f"✅ Taker ratio {tr} (sell pressure aktif)")
        else:
            notes.append(f"• Taker ratio {tr} (netral)")

    # 4. CMC Fear/Greed proxy
    fg = ctx.get("fear_greed", {})
    if fg:
        total += 1
        fg_score = fg.get("fg_score", 0)
        if is_long and fg_score >= 1:
            points += 1
            notes.append(f"✅ Sentimen {fg['fg_label']} (7d: {fg['change_7d']:+.1f}%)")
        elif not is_long and fg_score <= -1:
            points += 1
            notes.append(f"✅ Sentimen {fg['fg_label']} (7d: {fg['change_7d']:+.1f}%)")
        else:
            notes.append(f"• Sentimen {fg.get('fg_label','N/A')} (7d: {fg.get('change_7d',0):+.1f}%)")

    # 5. Trending bonus (informational only, tidak masuk scoring ratio)
    if ctx.get("trending"):
        notes.append(f"🔥 {symbol} trending di CMC (+bonus)")
        total += 1
        points += 1  # full point, masuk total agar konsisten

    # 6. Global market cap direction
    gm = ctx.get("global_metrics", {})
    if gm:
        mcap_chg = gm.get("total_market_cap_change_24h", 0)
        btc_dom   = gm.get("btc_dominance", 50)
        btc_dom_chg = gm.get("btc_dominance_change_24h", 0)
        total += 1

        # Market up + BTC dom menurun = alt season (bullish untuk alts)
        if is_long and mcap_chg > 1 and btc_dom_chg < 0:
            points += 1
            notes.append(f"✅ Mcap {mcap_chg:+.1f}% · BTC dom {btc_dom:.1f}% (alt season)")
        elif not is_long and mcap_chg < -1:
            points += 1
            notes.append(f"✅ Mcap {mcap_chg:+.1f}% (risk-off environment)")
        else:
            notes.append(f"• Mcap {mcap_chg:+.1f}% · BTC dom {btc_dom:.1f}%")

    # Final score: 0 atau 1 (rounded dari rasio)
    if total == 0:
        return 0, notes or ["• Data sentiment tidak tersedia"]

    ratio = points / total
    final_score = 1 if ratio >= 0.45 else 0   # >= 45% dari total poin = score 1

    if not notes:
        notes.append("• Data sentiment terbatas")

    return final_score, notes

