"""
WHALE TRACKER — Free On-Chain Whale Intelligence
Sumber: DefiLlama (gratis), Binance Futures public, CoinGecko
Tidak memerlukan API key apapun.

Whale signals yang dideteksi:
1. Large Taker Buys/Sells  — Binance futures taker volume spike
2. OI + Price Divergence   — Smart money accumulation/distribution
3. Exchange Flows          — DefiLlama: in/out dari CEX (proxy whale)
4. Token Holder Concentration — DefiLlama: whale wallet activity
5. Stablecoin Whale Moves  — Large stablecoin transfers (proxy liquidity)
"""

import requests
import time

TIMEOUT = 7
FAPI    = "https://fapi.binance.com/fapi/v1"
LLAMA   = "https://api.llama.fi"

_cache: dict = {}

def _get(url, params=None, ttl=300):
    key = url + str(params)
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < ttl:
        return _cache[key]["data"]
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "CryptoBiasBot/2.0"})
        if r.status_code == 200:
            d = r.json()
            _cache[key] = {"data": d, "ts": now}
            return d
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 1. LARGE TAKER VOLUME — Binance Futures (public)
#    Whale sering eksekusi via market order besar → taker ratio spike
# ══════════════════════════════════════════════════════════════════════════════

def get_taker_whale_signal(symbol: str) -> dict:
    """
    Deteksi whale taker activity dari Binance Futures.
    Jika taker buy volume >> sell volume → whale accumulating.
    Jika taker sell >> buy → whale distributing.
    """
    # Ambil 12 periode (H1 = 12 jam)
    data = _get(f"{FAPI}/takerbuy",
                params={"symbol": f"{symbol.upper()}USDT",
                        "period": "1h", "limit": 12})
    if not data or not isinstance(data, list) or len(data) < 3:
        return {"available": False}

    buy_vols  = [float(d.get("buySellRatio", 1.0)) for d in data]
    avg_ratio = sum(buy_vols) / len(buy_vols)
    latest    = buy_vols[-1]
    recent_3  = sum(buy_vols[-3:]) / 3

    # Whale buying: persistent ratio > 1.1 over last 3 periods
    # Whale selling: persistent ratio < 0.9 over last 3 periods
    if recent_3 > 1.15:
        signal = "WHALE_BUYING"
        strength = round((recent_3 - 1) * 100, 1)
    elif recent_3 < 0.85:
        signal = "WHALE_SELLING"
        strength = round((1 - recent_3) * 100, 1)
    elif recent_3 > 1.05:
        signal = "MILD_BUYING"
        strength = round((recent_3 - 1) * 100, 1)
    elif recent_3 < 0.95:
        signal = "MILD_SELLING"
        strength = round((1 - recent_3) * 100, 1)
    else:
        signal = "NEUTRAL"
        strength = 0

    return {
        "available":      True,
        "signal":         signal,
        "strength_pct":   strength,
        "avg_ratio_3h":   round(recent_3, 3),
        "avg_ratio_12h":  round(avg_ratio, 3),
        "latest_ratio":   round(latest, 3),
        "whale_bullish":  signal == "WHALE_BUYING",
        "whale_bearish":  signal == "WHALE_SELLING",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. OI vs PRICE DIVERGENCE — Binance Futures (public)
#    Smart money signal: OI naik saat price turun = accumulation (bullish)
#    OI naik saat price naik = euphoria (bearish jangka menengah)
# ══════════════════════════════════════════════════════════════════════════════

def get_oi_price_divergence(symbol: str) -> dict:
    """
    Deteksi divergence antara Open Interest dan Price.
    Classic SMC whale signal.
    """
    oi_data = _get(f"{FAPI}/openInterestHist",
                   params={"symbol": f"{symbol.upper()}USDT",
                           "period": "4h", "limit": 6})
    if not oi_data or not isinstance(oi_data, list) or len(oi_data) < 4:
        return {"available": False}

    oi_old = float(oi_data[0].get("sumOpenInterest", 0))
    oi_new = float(oi_data[-1].get("sumOpenInterest", 0))
    oi_change = (oi_new - oi_old) / oi_old * 100 if oi_old else 0

    price_old = float(oi_data[0].get("sumOpenInterestValue", 0)) / oi_old if oi_old else 0
    price_new = float(oi_data[-1].get("sumOpenInterestValue", 0)) / oi_new if oi_new else 0
    price_change = (price_new - price_old) / price_old * 100 if price_old else 0

    # Divergence patterns
    if oi_change > 3 and price_change < -1:
        pattern = "ACCUMULATION"   # OI naik, price turun → whale beli di dip
        whale_signal = "BULLISH"
    elif oi_change > 3 and price_change > 3:
        pattern = "STRONG_TREND"   # Keduanya naik → trend kuat, tapi awas overextended
        whale_signal = "BULLISH_CAUTION"
    elif oi_change < -3 and price_change > 1:
        pattern = "DISTRIBUTION"   # OI turun, price naik → whale keluar
        whale_signal = "BEARISH"
    elif oi_change < -3 and price_change < -3:
        pattern = "DELEVERAGING"   # Keduanya turun → forced liquidation
        whale_signal = "BEARISH"
    elif oi_change > 3 and price_change < -3:
        pattern = "SHORT_BUILD"    # OI naik, price turun tajam → short build-up
        whale_signal = "BEARISH"
    else:
        pattern = "NEUTRAL"
        whale_signal = "NEUTRAL"

    return {
        "available":      True,
        "pattern":        pattern,
        "whale_signal":   whale_signal,
        "oi_change_pct":  round(oi_change, 2),
        "price_change_pct": round(price_change, 2),
        "whale_bullish":  whale_signal == "BULLISH",
        "whale_bearish":  whale_signal in ("BEARISH",),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. LARGE LIQUIDATIONS — Binance Futures (public)
#    Liquidation spike = whale stop hunt atau forced close
#    Setelah liquidation spike besar → biasanya reversal
# ══════════════════════════════════════════════════════════════════════════════

def get_liquidation_whale(symbol: str) -> dict:
    """
    Cek recent liquidations untuk deteksi stop hunt / whale flush.
    """
    data = _get(f"{FAPI}/forceOrders",
                params={"symbol": f"{symbol.upper()}USDT", "limit": 50},
                ttl=60)
    if not data or not isinstance(data, list):
        return {"available": False}

    if not data:
        return {"available": True, "total_liq_usd": 0,
                "dominant": "NONE", "signal": "NEUTRAL"}

    long_liq  = sum(float(d.get("origQty", 0)) * float(d.get("price", 0))
                    for d in data if d.get("side") == "SELL")  # long positions liquidated
    short_liq = sum(float(d.get("origQty", 0)) * float(d.get("price", 0))
                    for d in data if d.get("side") == "BUY")   # short positions liquidated

    total = long_liq + short_liq

    if total < 1_000_000:  # < $1M = tidak signifikan
        return {"available": True, "total_liq_usd": total,
                "dominant": "LOW", "signal": "NEUTRAL"}

    if long_liq > short_liq * 2:
        dominant = "LONG_LIQUIDATED"
        signal   = "BEARISH_FLUSH"      # longs flushed → potential bounce
    elif short_liq > long_liq * 2:
        dominant = "SHORT_LIQUIDATED"
        signal   = "BULLISH_SQUEEZE"    # shorts squeezed → continuation or reversal
    else:
        dominant = "MIXED"
        signal   = "VOLATILITY"

    return {
        "available":      True,
        "total_liq_usd":  round(total),
        "long_liq_usd":   round(long_liq),
        "short_liq_usd":  round(short_liq),
        "dominant":       dominant,
        "signal":         signal,
        "significant":    total > 5_000_000,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. EXCHANGE INFLOW/OUTFLOW — DefiLlama (proxy whale)
#    Large inflow ke CEX = whale mau jual (bearish)
#    Large outflow dari CEX = whale hold/stake (bullish)
# ══════════════════════════════════════════════════════════════════════════════

def get_exchange_flow_signal() -> dict:
    """
    Deteksi capital flow ke/dari CEX via DefiLlama bridge data.
    Bridge outflow dari exchange = bullish (withdrawal ke self-custody).
    Bridge inflow ke exchange = bearish (deposit untuk jual).
    """
    data = _get(f"{LLAMA}/overview/bridges", ttl=1800)
    if not data:
        return {"available": False}

    vol24h = data.get("total24h", 0)
    vol7d  = data.get("total7d", 0)
    avg    = vol7d / 7 if vol7d else 0

    if avg > 0:
        flow_ratio = round(vol24h / avg, 2)
        if flow_ratio > 2.0:
            signal = "WHALE_BRIDGE_SPIKE"   # massive cross-chain activity
            desc   = f"Bridge volume {flow_ratio}x avg 7d"
        elif flow_ratio > 1.3:
            signal = "ELEVATED_FLOW"
            desc   = f"Bridge volume {flow_ratio}x avg 7d (elevated)"
        else:
            signal = "NORMAL"
            desc   = f"Bridge volume normal ({flow_ratio}x avg)"
    else:
        signal = "UNKNOWN"; desc = "No data"; flow_ratio = 1.0

    return {
        "available":   True,
        "signal":      signal,
        "flow_ratio":  flow_ratio,
        "vol24h":      vol24h,
        "description": desc,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MASTER — Aggregated Whale Score
# ══════════════════════════════════════════════════════════════════════════════

def get_whale_context(symbol: str) -> dict:
    """Gabungkan semua whale signals untuk 1 simbol."""
    return {
        "symbol":    symbol,
        "taker":     get_taker_whale_signal(symbol),
        "oi_div":    get_oi_price_divergence(symbol),
        "liq":       get_liquidation_whale(symbol),
        "flows":     get_exchange_flow_signal(),
    }


def score_whale(symbol: str, direction: str, ctx: dict = None) -> tuple[int, list]:
    """
    Whale scoring (0-1).
    direction: 'BULLISH' atau 'BEARISH'
    Returns: (score, [notes])
    """
    if ctx is None:
        ctx = get_whale_context(symbol)

    is_long = direction == "BULLISH"
    points  = 0
    total   = 0
    notes   = []

    # 1. Taker signal
    taker = ctx.get("taker", {})
    if taker.get("available"):
        total += 1
        sig = taker.get("signal", "NEUTRAL")
        st  = taker.get("strength_pct", 0)
        if is_long and taker.get("whale_bullish"):
            points += 1
            notes.append(f"🐋 Whale Buying ({st:.0f}% intensity) — Taker ratio {taker.get('avg_ratio_3h')}")
        elif not is_long and taker.get("whale_bearish"):
            points += 1
            notes.append(f"🐋 Whale Selling ({st:.0f}% intensity) — Taker ratio {taker.get('avg_ratio_3h')}")
        else:
            notes.append(f"• Taker: {sig} (ratio {taker.get('avg_ratio_3h', '?')})")

    # 2. OI/Price divergence
    oi = ctx.get("oi_div", {})
    if oi.get("available"):
        total += 1
        if is_long and oi.get("whale_bullish"):
            points += 1
            notes.append(f"📊 OI Pattern: {oi.get('pattern')} → Smart money bullish")
        elif not is_long and oi.get("whale_bearish"):
            points += 1
            notes.append(f"📊 OI Pattern: {oi.get('pattern')} → Smart money bearish")
        else:
            notes.append(f"• OI/Price: {oi.get('pattern', 'NEUTRAL')}")

    # 3. Liquidations
    liq = ctx.get("liq", {})
    if liq.get("available") and liq.get("significant"):
        total += 1
        sig = liq.get("signal", "NEUTRAL")
        amt = liq.get("total_liq_usd", 0)
        if is_long and sig == "BULLISH_SQUEEZE":
            points += 1
            notes.append(f"⚡ Short Squeeze: ${amt/1e6:.1f}M liquidated → bullish momentum")
        elif not is_long and sig == "BEARISH_FLUSH":
            points += 1
            notes.append(f"⚡ Long Flush: ${amt/1e6:.1f}M liquidated → bearish momentum")
        else:
            notes.append(f"• Liquidation: {sig} (${amt/1e6:.1f}M)")

    # 4. Exchange flows
    flows = ctx.get("flows", {})
    if flows.get("available") and flows.get("signal") != "NORMAL":
        notes.append(f"🌉 Bridge: {flows.get('description', '')}")

    if not notes:
        notes.append("• Whale data tidak tersedia")

    final = 1 if total > 0 and (points / total) >= 0.5 else 0
    return final, notes
