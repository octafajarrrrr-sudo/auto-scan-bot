# Crypto Intraday Bot
**Signal Engine Intraday** — Binance Futures · ICT/SMC · Elliott Wave · On-Chain · Sentiment

---

## Cara Kerja (Workflow)

```
BINANCE FUTURES
(fapi.binance.com)
  │ Scan semua USDT Perpetual
  │ Filter by volume → validasi MCap (CMC/CoinGecko)
  ▼
MULTI-TIMEFRAME ANALYSIS
  H4  → Trend bias (structure, EMA 20/50/200, Elliott, Wyckoff)
  H1  → Zone & pullback (structure, FVG, liquidity)
  M15 → Entry trigger (displacement, FVG, sweep)
  1D  → Regime context (BTC weekly EMA200)
  ▼
UNIFIED SCORING (12 poin direktional)
  L1 Regime   (0-2)  BTC weekly EMA trend
  L2 Structure (0-3) H4 BOS/CHoCH/HH+HL
  L3 EMA       (0-2) Alignment EMA20/50/200
  L4 Elliott   (0-1) Posisi dalam impulse wave
  L5 LTF       (0-1) H1+M15 konfirmasi searah
  L6 Fund      (0-1) FDV ratio, volume, RS vs BTC
  L7 DeFi      (0-1) DefiLlama TVL, unlock, hacks
  L8 Sentiment (0-1) Funding rate, LSR, taker, CMC
  ▼
THRESHOLD
  >= 8  → HIGH CONVICTION → Signal dikirim ke Telegram
   5-7  → MODERATE → hanya via /analyze
  < 5   → NO TRADE → dibuang
  ▼
EXECUTION
  Entry : harga pasar saat sinyal
  SL    : max(ATR×1.5, 3% dari entry)
  TP1   : +2R    | TP2 : +2.618R
  TP3   : +3.618R| TP4 : +4.236R
  ▼
TELEGRAM      JOURNAL (SQLite)      DASHBOARD (Flask)
  Kirim card    Log sinyal            Live monitoring
  Paper trade   Track status          Auto price check
  Auto-scan     Winrate stats         PnL realtime
```

---

## Setup

### 1. Install
```bash
git clone <repo-url> && cd crypto_bias_bot
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Isi API Keys di `config.py`
```python
BINANCE_API_KEY    = "..."  # Market data + rate limit upgrade
BINANCE_SECRET_KEY = "..."  # Pasangan API key
CMC_API_KEY        = "..."  # Fundamental + global metrics + trending
TELEGRAM_BOT_TOKEN = "..."  # Dari @BotFather
TELEGRAM_CHAT_ID   = "..."  # ID kamu (dari @userinfobot)
# DefiLlama & Binance Futures public = tidak perlu key
```

### 3. Deploy VPS
```bash
chmod +x deploy.sh && sudo bash deploy.sh
# Cek: journalctl -u cryptobot -f
```

### 4. Dashboard (terminal terpisah)
```bash
python dashboard.py
# Akses: http://IP_VPS:8080
# Buka port: ufw allow 8080
```

---

## Perintah Telegram

### Analisa
| Command | Fungsi |
|---------|--------|
| `/analyze SOL` | Full 8-layer report 1 koin |
| `/scan` | Scan futures market → TOP N sinyal terkuat |
| `/scanall` | Scan → tampilkan semua yang lolos threshold |

### Paper Trade
| Command | Fungsi |
|---------|--------|
| `/paper` | Lihat semua open paper trades |
| `/paperstats` | Winrate & statistik paper trade |
| `/closepapr 3 98000` | Tutup paper trade #3 di harga 98000 |

### Backtest
| Command | Fungsi |
|---------|--------|
| `/backtest SOL` | Backtest SOL pada data historis (TF sesuai settings) |

### Settings (tidak perlu buka script)
| Command | Fungsi | Default |
|---------|--------|---------|
| `/settings` | Tampilkan semua setting | — |
| `/setmcap 100` | Min MCap $100 juta | $50M |
| `/setscore 8` | Min confluence score | 8/12 |
| `/settf 4h 1h 15m` | Timeframe bias/struktur/entry | 4h/1h/15m |
| `/setmode intraday` | Mode preset (auto-set TF) | intraday |
| `/setmode swing` | Ubah ke swing (1d/4h/1h) | — |
| `/settopn 3` | Jumlah sinyal top yang dikirim | 2 |
| `/setinterval 4` | Interval auto-scan (jam) | 4 |
| `/resetset` | Reset semua ke default | — |

### Info
| Command | Fungsi |
|---------|--------|
| `/status` | Status bot + cek koneksi API nyata |
| `/stats` | Statistik journal + paper trade |
| `/list` | Info mode scan saat ini |
| `/start` | Menu lengkap |

---

## Dashboard

Buka `http://IP_VPS:8080` setelah `python dashboard.py` berjalan.

**Fitur:**
- **Stats bar** — winrate, avg PnL, running, total signals (auto-refresh 15 detik)
- **Running Positions** — harga live + PnL% realtime dari Binance Futures
- **Auto price check** — background thread cek SL/TP setiap 60 detik, update status otomatis
- **Scan History** — riwayat scan + top symbols per run
- **Trade Journal** — filter All/Running/Win/Loss/Long/Short, update status via modal
- **Live PnL** — kolom PnL% hijau/merah update tiap 15 detik

---

## API Keys — Fungsi Detail

| Key | Dipakai Di | Fungsi |
|-----|-----------|--------|
| `BINANCE_API_KEY` | `technical.py`, `analyzer.py` | Fetch OHLCV (H4/H1/M15/1D), ticker price |
| `BINANCE_API_KEY` | `settings.py` rate limit | Upgrade rate limit untuk scan massal |
| `CMC_API_KEY` | `analyzer.py` | MCap, FDV, Vol24h per koin |
| `CMC_API_KEY` | `sentiment.py` | Global metrics (BTC dominance, total mcap) |
| `CMC_API_KEY` | `sentiment.py` | Trending coins (sentiment boost) |
| `TELEGRAM_BOT_TOKEN` | `bot.py` | Auth bot ke Telegram API |
| `TELEGRAM_CHAT_ID` | `bot.py` | Whitelist — hanya pemilik bisa pakai |
| *(no key)* | `defillama.py` | TVL, DEX vol, token unlocks, hacks, bridges |
| *(no key)* | `sentiment.py` | Binance Futures public: funding, OI, LSR, taker |
| *(no key)* | `dashboard.py` | Binance Futures public: harga live untuk price checker |

---

## File Structure
```
crypto_bias_bot/
├── bot.py          — Telegram interface, 19 commands, auto-scan job
├── analyzer.py     — 12-point scoring engine, scan futures market
├── technical.py    — MTF engine: H4/H1/M15, Elliott, Regime, EMA
├── sentiment.py    — CMC global metrics + Binance Futures sentiment
├── defillama.py    — On-chain: TVL, unlocks, DEX vol, hacks (gratis)
├── journal.py      — SQLite: signal tracker, stats, winrate
├── backtest.py     — Backtest + paper trade engine
├── dashboard.py    — Flask server: monitoring + auto price checker
├── settings.py     — Persistent config (edit via Telegram, no script)
├── config.py       — API keys
├── requirements.txt
├── deploy.sh
├── templates/
│   └── index.html  — Dark dashboard UI
├── BLUEPRINT.md    — Arsitektur & scoring detail
└── README.md       — File ini
```

---

## Trade Rules
```
Mode    : Intraday (default H4/H1/M15)
Min RR  : 1:2
Max SL  : 3% dari entry (ATR×1.5)
Output  : TOP N sinyal terkuat per scan (default N=2)
Filter  : Binance Futures USDT Perpetual, MCap ≥ $50M
Hard block: token unlock >5% mcap/30 hari
```

---

## Troubleshooting
| Masalah | Solusi |
|---------|--------|
| Bot tidak start | `journalctl -u cryptobot -f` — cek API keys |
| Auto-scan tidak jalan | Pastikan `python-telegram-bot[job-queue]` terinstall |
| Dashboard tidak bisa diakses | `ufw allow 8080` |
| Scan tidak menemukan koin | Coba `/setmcap 20` (turunkan threshold) |
| Semua NO TRADE | Market ranging — normal. Coba `/setscore 6` sementara |
| CMC error | Quota habis — fallback CoinGecko otomatis aktif |
