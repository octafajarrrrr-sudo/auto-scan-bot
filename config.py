# ═══════════════════════════════════════════════════════════════════════════
# CONFIG — API Keys & Credentials
# Masing-masing key memiliki fungsi spesifik — jangan tukar posisi.
# ═══════════════════════════════════════════════════════════════════════════

# ── BINANCE ─────────────────────────────────────────────────────────────────
# Fungsi:
#   1. fetch_ohlcv()   → OHLCV 1D/4H/1W untuk ICT/SMC + Elliott + Regime
#   2. fetch_ticker()  → harga real-time, volume, RS vs BTC
#   3. Futures public  → Funding Rate, OI, Taker Ratio (tidak butuh key)
#   Note: Public market data tidak butuh key; key digunakan untuk rate limit
#         upgrade dan jika bot dikembangkan untuk order execution.
BINANCE_API_KEY    = "i7f3xTKimodq6JutGEwAmHRenEzUly9Dlikk3B7571kb8Bdxf7RmdeIlQU9O8uzE"
BINANCE_SECRET_KEY = "SlIOLtaPMYYOe0FAfono8K3wkuCaxbmRDKAcAWJDnMLHKkAGYcQMUDDTAy4KlazI"

# ── COINMARKETCAP ────────────────────────────────────────────────────────────
# Fungsi:
#   1. /cryptocurrency/quotes/latest     → MCap, FDV, Vol24h (fundamental)
#   2. /global-metrics/quotes/latest     → Total MarketCap, BTC dominance (regime)
#   3. /cryptocurrency/trending/latest   → Trending coins (sentiment)
#   Fallback: CoinGecko free API
CMC_API_KEY = "f3d1653444ba431e888fb7e9bde3b8b0"

# ── TELEGRAM ─────────────────────────────────────────────────────────────────
# Fungsi:
#   TELEGRAM_BOT_TOKEN → autentikasi bot ke Telegram API
#   TELEGRAM_CHAT_ID   → whitelist akses, hanya pemilik yang bisa pakai
TELEGRAM_BOT_TOKEN = "8737866685:AAFYasxC_pV6cK73YPZl-Ix_iyPd33OyH-Y"
TELEGRAM_CHAT_ID   = "6540284368"

# ── DEFILLAMA ────────────────────────────────────────────────────────────────
# Fungsi: TVL, DEX volume, stablecoin flows, token unlocks, hacks, bridges
# Gratis — tidak butuh API key (dikelola di defillama.py)
