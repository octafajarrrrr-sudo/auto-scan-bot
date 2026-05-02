"""
DEFILLAMA ENGINE — Free On-Chain Intelligence Layer
Covers: TVL, DEX Volume, Stablecoin Flows, Derivatives/OI,
        Token Unlocks, CEX Transparency, Hacks Archive.
Semua endpoint gratis — tidak butuh API key.
"""

import requests
import time
from functools import lru_cache
from datetime import datetime, timedelta, timezone

BASE       = "https://api.llama.fi"
COINS      = "https://coins.llama.fi"
STABLES    = "https://stablecoins.llama.fi"

TIMEOUT    = 8
HEADERS    = {"User-Agent": "CryptoBiasBot/2.0"}

# ── simple in-memory cache (TTL 15 menit) ──────────────────────────────────
_cache: dict = {}

def _get(url: str, params: dict = None, ttl: int = 900) -> dict | None:
    key = url + str(params)
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < ttl:
        return _cache[key]["data"]
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            _cache[key] = {"data": data, "ts": now}
            return data
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 1. PRICING — Real-time price dari DEX/CEX via DefiLlama
# ══════════════════════════════════════════════════════════════════════════════
def get_token_price(symbol: str) -> dict:
    """
    Harga real-time via DefiLlama Coins API.
    Mendukung ribuan token termasuk memecoin DEX.
    """
    # Coba via coingecko slug mapping dulu
    slug_map = {
        "BTC": "coingecko:bitcoin", "ETH": "coingecko:ethereum",
        "SOL": "coingecko:solana", "BNB": "coingecko:binancecoin",
        "ONDO": "coingecko:ondo-finance", "PENDLE": "coingecko:pendle",
        "RNDR": "coingecko:render-token", "TIA": "coingecko:celestia",
        "INJ": "coingecko:injective-protocol", "SUI": "coingecko:sui",
        "SEI": "coingecko:sei-network", "PEPE": "coingecko:pepe",
        "WIF": "coingecko:dogwifcoin", "FLOKI": "coingecko:floki",
    }
    coin_id = slug_map.get(symbol.upper(), f"coingecko:{symbol.lower()}")
    data = _get(f"{COINS}/prices/current/{coin_id}")
    if data and "coins" in data and coin_id in data["coins"]:
        c = data["coins"][coin_id]
        return {
            "price": c.get("price", 0),
            "confidence": c.get("confidence", 0),
            "timestamp": c.get("timestamp", 0),
            "source": "DefiLlama",
        }
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# 2. TVL & DEX VOLUME — Modal ekosistem & rotasi likuiditas
# ══════════════════════════════════════════════════════════════════════════════
def get_chain_tvl(chain: str = "Ethereum") -> dict:
    """TVL chain tertentu — deteksi inflow/outflow modal."""
    data = _get(f"{BASE}/v2/historicalChainTvl/{chain}")
    if not data or not isinstance(data, list) or len(data) < 2:
        return {}
    latest  = data[-1]["tvl"]
    prev_7d = data[-7]["tvl"] if len(data) >= 7 else data[0]["tvl"]
    prev_1d = data[-2]["tvl"]
    return {
        "chain": chain,
        "tvl_usd": latest,
        "change_1d_pct": round((latest - prev_1d) / prev_1d * 100, 2) if prev_1d else 0,
        "change_7d_pct": round((latest - prev_7d) / prev_7d * 100, 2) if prev_7d else 0,
    }

def get_dex_volume(chain: str = "all") -> dict:
    """Volume DEX — deteksi lonjakan volume mendadak."""
    params = {"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true"}
    if chain != "all":
        data = _get(f"{BASE}/overview/dexs/{chain}", params)
    else:
        data = _get(f"{BASE}/overview/dexs", params)
    if not data:
        return {}
    vol24h  = data.get("total24h", 0)
    vol7d   = data.get("total7d", 0)
    avg_7d  = vol7d / 7 if vol7d else 0
    spike   = round(vol24h / avg_7d, 2) if avg_7d else 1.0
    return {
        "vol24h": vol24h,
        "vol7d":  vol7d,
        "avg_daily_7d": round(avg_7d),
        "volume_spike": spike,          # >1.5 = lonjakan signifikan
        "spike_signal": spike > 1.5,
    }

def get_protocol_tvl(protocol_slug: str) -> dict:
    """TVL protokol spesifik — deteksi capital rotation."""
    SLUG_MAP = {
        "SOL": "marinade-finance", "ETH": "lido", "BNB": "pancakeswap",
        "ONDO": "ondo-finance", "PENDLE": "pendle", "INJ": "injective",
    }
    slug = SLUG_MAP.get(protocol_slug.upper(), protocol_slug.lower())
    data = _get(f"{BASE}/protocol/{slug}")
    if not data:
        return {}
    tvl_now = data.get("currentChainTvls", {})
    total   = sum(tvl_now.values()) if tvl_now else 0
    return {
        "protocol": slug,
        "tvl_usd": total,
        "chains": list(tvl_now.keys()),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. STABLECOIN FLOWS & BRIDGES — Smart money tracking
# ══════════════════════════════════════════════════════════════════════════════
def get_stablecoin_overview() -> dict:
    """Total stablecoin supply & dominance — proxy smart money positioning."""
    data = _get(f"{STABLES}/stablecoins?includePrices=true")
    if not data or "peggedAssets" not in data:
        return {}
    assets = data["peggedAssets"]
    total_circ = sum(
        a.get("circulating", {}).get("peggedUSD", 0)
        for a in assets
    )
    usdt = next((a for a in assets if a.get("symbol") == "USDT"), {})
    usdc = next((a for a in assets if a.get("symbol") == "USDC"), {})
    usdt_circ = usdt.get("circulating", {}).get("peggedUSD", 0)
    usdc_circ = usdc.get("circulating", {}).get("peggedUSD", 0)
    return {
        "total_stablecoin_supply": total_circ,
        "usdt_supply": usdt_circ,
        "usdc_supply": usdc_circ,
        "usdt_dominance_pct": round(usdt_circ / total_circ * 100, 1) if total_circ else 0,
    }

def get_bridge_volume() -> dict:
    """Volume bridge antar chain — deteksi rotasi modal cross-chain."""
    data = _get(f"{BASE}/overview/bridges?includeChains=true")
    if not data:
        return {}
    vol24h = data.get("total24h", 0)
    vol7d  = data.get("total7d", 0)
    avg    = vol7d / 7 if vol7d else 0
    chains = data.get("chains", [])
    # Top 3 destination chains
    top_chains = sorted(chains, key=lambda x: x.get("volume24h", 0), reverse=True)[:3]
    return {
        "bridge_vol24h": vol24h,
        "bridge_avg_7d": round(avg),
        "bridge_spike": round(vol24h / avg, 2) if avg else 1.0,
        "top_destination_chains": [c.get("name", "?") for c in top_chains],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. DERIVATIVES — Open Interest & Liquidations
# ══════════════════════════════════════════════════════════════════════════════
def get_derivatives_overview() -> dict:
    """Open Interest agregat — deteksi penumpukan posisi leveraged."""
    data = _get(f"{BASE}/overview/derivatives")
    if not data:
        return {}
    oi_24h   = data.get("total24h", 0)
    oi_7d    = data.get("total7d", 0)
    avg_oi   = oi_7d / 7 if oi_7d else 0
    oi_spike = round(oi_24h / avg_oi, 2) if avg_oi else 1.0
    return {
        "oi_24h": oi_24h,
        "oi_avg_7d": round(avg_oi),
        "oi_spike": oi_spike,
        "oi_elevated": oi_spike > 1.3,   # OI naik 30% dari rata-rata → potensi sweep
    }

def get_liquidations_summary() -> dict:
    """
    Ringkasan liquidasi via DefiLlama.
    Deteksi cluster liquidasi yang bisa memicu sweep.
    """
    data = _get(f"{BASE}/liquidations/eth", ttl=300)  # cache 5 menit
    if not data:
        return {"available": False}
    positions = data.get("positions", [])
    if not positions:
        return {"available": False}
    total_usd   = sum(p.get("liquidatableAmount", 0) for p in positions)
    liq_count   = len(positions)
    return {
        "available": True,
        "total_liquidatable_usd": total_usd,
        "position_count": liq_count,
        "risk_level": "HIGH" if total_usd > 1e9 else "MEDIUM" if total_usd > 1e8 else "LOW",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. TOKEN UNLOCKS — Supply inflation filter
# ══════════════════════════════════════════════════════════════════════════════
def get_token_unlocks(symbol: str) -> dict:
    """
    Cek jadwal unlock token dalam 30 hari ke depan.
    Jika ada unlock besar → filter keluar dari watchlist.
    """
    UNLOCK_MAP = {
        "ONDO": "ondo-finance", "PENDLE": "pendle", "TIA": "celestia",
        "INJ": "injective", "SUI": "sui", "SEI": "sei",
        "ARB": "arbitrum", "OP": "optimism", "APT": "aptos",
    }
    slug = UNLOCK_MAP.get(symbol.upper())
    if not slug:
        return {"symbol": symbol, "available": False, "unlock_risk": "UNKNOWN"}

    data = _get(f"{BASE}/emission/{slug}", ttl=3600)  # cache 1 jam
    if not data:
        return {"symbol": symbol, "available": False, "unlock_risk": "UNKNOWN"}

    now_ts    = time.time()
    cutoff_ts = now_ts + (30 * 86400)  # 30 hari ke depan
    events    = data.get("events", [])

    upcoming = [
        e for e in events
        if now_ts < e.get("timestamp", 0) < cutoff_ts
    ]
    total_unlock_usd = sum(e.get("unlockUSD", 0) for e in upcoming)
    mcap = data.get("mcap", 1)
    unlock_pct = round(total_unlock_usd / mcap * 100, 1) if mcap else 0

    if unlock_pct > 10:
        risk = "CRITICAL"   # >10% supply unlock dalam 30 hari
    elif unlock_pct > 5:
        risk = "HIGH"
    elif unlock_pct > 1:
        risk = "MODERATE"
    else:
        risk = "LOW"

    return {
        "symbol": symbol,
        "available": True,
        "upcoming_events": len(upcoming),
        "unlock_usd_30d": total_unlock_usd,
        "unlock_pct_mcap": unlock_pct,
        "unlock_risk": risk,
        "skip_trade": risk in ("CRITICAL", "HIGH"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 6. CEX TRANSPARENCY & HACKS ARCHIVE — Macro safety filter
# ══════════════════════════════════════════════════════════════════════════════
def get_recent_hacks(days: int = 30) -> list:
    """
    Daftar hack/exploit terbaru dari DefiLlama Hacks Archive.
    Digunakan sebagai macro sentiment filter.
    """
    data = _get(f"{BASE}/hacks", ttl=3600)
    if not data:
        return []
    cutoff = time.time() - (days * 86400)
    recent = [
        {
            "name":   h.get("name", "?"),
            "date":   datetime.fromtimestamp(h.get("date", 0), tz=timezone.utc).strftime("%Y-%m-%d"),
            "amount": h.get("fundsLost", 0),
            "chain":  h.get("chains", ["?"])[0] if h.get("chains") else "?",
        }
        for h in data
        if h.get("date", 0) > cutoff
    ]
    return sorted(recent, key=lambda x: x["amount"], reverse=True)[:5]

def get_cex_transparency() -> dict:
    """
    CEX Proof-of-Reserves status via DefiLlama.
    Jika exchange besar tidak transparan → macro risk flag.
    """
    data = _get(f"{BASE}/cexs", ttl=3600)
    if not data:
        return {"available": False}
    transparent = [c for c in data if c.get("lastAuditDate")]
    opaque      = [c for c in data if not c.get("lastAuditDate")]
    return {
        "available": True,
        "total_cex": len(data),
        "with_proof_of_reserves": len(transparent),
        "without_proof":          len(opaque),
        "transparency_pct":       round(len(transparent) / len(data) * 100) if data else 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MASTER — Full DefiLlama context untuk 1 simbol
# ══════════════════════════════════════════════════════════════════════════════
def get_full_defi_context(symbol: str) -> dict:
    """
    Gabungan semua data DefiLlama untuk 1 koin.
    Digunakan oleh analyzer untuk menambah bobot confluence.
    """
    result = {
        "symbol":       symbol,
        "unlock":       get_token_unlocks(symbol),
        "dex_volume":   get_dex_volume(),
        "derivatives":  get_derivatives_overview(),
        "stablecoins":  get_stablecoin_overview(),
        "hacks_30d":    get_recent_hacks(30),
    }
    return result


def score_defi_confluence(ctx: dict, direction: str = "BULLISH") -> tuple[int, list]:
    """
    Hitung skor DeFi confluence (0-1) dengan kesadaran arah (BULLISH/BEARISH).
    Return: (score 0 atau 1, [notes])

    Perubahan dari versi sebelumnya:
    - Stablecoin supply threshold dihapus (selalu >150B = tidak diskriminatif)
    - OI dan DEX spike sekarang direktional
    - Output max 1 (bukan 5) — konsisten dengan layer lain
    """
    is_long = direction == "BULLISH"
    notes   = []
    points  = 0
    total   = 0

    # Hard blocker: token unlock (dikecek di analyzer, tapi tetap handle di sini)
    unlock = ctx.get("unlock", {})
    if unlock.get("skip_trade"):
        notes.append(f"⛔ Token Unlock RISK ({unlock.get('unlock_pct_mcap', 0):.1f}% dalam 30 hari)")
        return -99, notes

    # 1. DEX Volume spike — direktional
    dex = ctx.get("dex_volume", {})
    spike = dex.get("volume_spike", 1.0)
    total += 1
    if dex.get("spike_signal"):
        if is_long:
            points += 1
            notes.append(f"🔥 DEX Vol Spike {spike:.2f}x — demand aktif (bullish)")
        else:
            # Spike saat bear bisa distribusi ATAU panic buy — ambigu, tidak beri poin
            notes.append(f"⚠️ DEX Vol Spike {spike:.2f}x (ambigu untuk short)")
    else:
        if not is_long:
            # Volume sepi saat bear = tidak ada pembelian = bearish valid
            points += 1
            notes.append(f"✅ DEX Vol rendah ({spike:.2f}x) — tidak ada demand (bearish)")
        else:
            notes.append(f"• DEX Vol normal ({spike:.2f}x avg 7d)")

    # 2. Open Interest — direktional
    deriv = ctx.get("derivatives", {})
    oi_spike = deriv.get("oi_spike", 1.0)
    oi_elevated = deriv.get("oi_elevated", False)
    total += 1
    if oi_elevated:
        if is_long:
            # OI naik + harga naik = strong trend, tapi bisa overheated
            # Kita pertimbangkan positif jika tidak berlebihan (< 2x)
            if oi_spike < 2.0:
                points += 1
                notes.append(f"⚡ OI {oi_spike:.2f}x — leveraged long interest (healthy)")
            else:
                notes.append(f"⚠️ OI {oi_spike:.2f}x — overheated, squeeze risk")
        else:
            # OI tinggi dengan harga turun = short squeeze potential menghambat short
            notes.append(f"⚠️ OI {oi_spike:.2f}x — perlu hati-hati (squeeze risk untuk short)")
    else:
        if not is_long:
            points += 1
            notes.append(f"✅ OI normal — tidak ada proteksi short squeeze")
        else:
            notes.append(f"• OI {oi_spike:.2f}x (normal)")

    # 3. Recent hacks — macro filter (arah-agnostik, mengurangi confidence)
    hacks = ctx.get("hacks_30d", [])
    total += 1
    if hacks:
        total_lost = sum(h["amount"] for h in hacks)
        if total_lost > 50e6:
            notes.append(f"⚠️ Hack Alert 30d: ${total_lost/1e6:.0f}M — macro risk")
            # Tidak menambah atau mengurangi points, hanya sebagai informasi
        else:
            points += 1
            notes.append(f"✅ Hack activity rendah (${total_lost/1e6:.1f}M 30d)")
    else:
        points += 1
        notes.append("✅ No major hacks 30d")

    # Score: 1 jika >= 50% sub-layer positif, 0 jika tidak
    final_score = 1 if total > 0 and (points / total) >= 0.5 else 0
    return final_score, notes
