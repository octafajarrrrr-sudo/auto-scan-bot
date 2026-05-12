# ═══════════════════════════════════════════════════════════════════════════
# CONFIG — API Keys & Credentials
# Masing-masing key memiliki fungsi spesifik — jangan tukar posisi.
# Kunci API sekarang dimuat dengan aman dari file .env
# ═══════════════════════════════════════════════════════════════════════════

import os
from dotenv import load_dotenv

# Muat file .env
load_dotenv()

# ── BINANCE ─────────────────────────────────────────────────────────────────
# Fungsi:
#   1. fetch_ohlcv()   → OHLCV 1D/4H/1W untuk ICT/SMC + Elliott + Regime
#   2. fetch_ticker()  → harga real-time, volume, RS vs BTC
#   3. Futures public  → Funding Rate, OI, Taker Ratio (tidak butuh key)
#   Note: Public market data tidak butuh key; key digunakan untuk rate limit
#         upgrade dan jika bot dikembangkan untuk order execution.
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")

# ── COINMARKETCAP ────────────────────────────────────────────────────────────
# Fungsi:
#   1. /cryptocurrency/quotes/latest     → MCap, FDV, Vol24h (fundamental)
#   2. /global-metrics/quotes/latest     → Total MarketCap, BTC dominance (regime)
#   3. /cryptocurrency/trending/latest   → Trending coins (sentiment)
#   Fallback: CoinGecko free API
CMC_API_KEY = os.getenv("CMC_API_KEY", "")

# ── TELEGRAM ─────────────────────────────────────────────────────────────────
# Fungsi:
#   TELEGRAM_BOT_TOKEN → autentikasi bot ke Telegram API
#   TELEGRAM_CHAT_ID   → whitelist akses, hanya pemilik yang bisa pakai
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── DEFILLAMA ────────────────────────────────────────────────────────────────
# Fungsi: TVL, DEX volume, stablecoin flows, token unlocks, hacks, bridges
# Gratis — tidak butuh API key (dikelola di defillama.py)

