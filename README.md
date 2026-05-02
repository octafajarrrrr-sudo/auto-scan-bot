# Crypto Bias Bot
**Swing Trade Signal Engine** — ICT/SMC · Elliott Wave · Market Regime · On-Chain Intelligence

---

## Gambaran Umum

Bot Telegram yang menganalisis aset kripto menggunakan pendekatan multi-layer:
- **Teknikal**: ICT/SMC (BOS/CHoCH/FVG/Sweep), Elliott Wave, EMA 20/50/200, Wyckoff
- **Regime**: BTC Weekly EMA200 sebagai penentu bull/bear market
- **Fundamental**: Market cap, FDV/MCap ratio, RS vs BTC
- **On-Chain**: DefiLlama (TVL, DEX volume, token unlocks, hacks archive)
- **Sentiment**: Binance Futures (funding rate, LSR, taker ratio) + CMC global metrics

Setiap sinyal dinilai dengan **sistem 12 poin direktional** — LONG dan SHORT dihitung terpisah. Hanya sinyal dengan skor ≥ 8 yang dikirim ke Telegram (HIGH CONVICTION).

---

## Cara Setup

### 1. Persyaratan
```
Python 3.10+
VPS Linux (Ubuntu 22.04 disarankan)
```

### 2. Clone & Install
```bash
git clone <repo-url>
cd crypto_bias_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Konfigurasi API Keys
Edit `config.py` dan isi dengan key yang valid:

```python
BINANCE_API_KEY    = "..."   # Untuk OHLCV + rate limit upgrade
BINANCE_SECRET_KEY = "..."   # Pasangan dari API key Binance
CMC_API_KEY        = "..."   # CoinMarketCap Pro API key
TELEGRAM_BOT_TOKEN = "..."   # Dari @BotFather di Telegram
TELEGRAM_CHAT_ID   = "..."   # ID Telegram kamu (dari @userinfobot)
```

**Catatan**: DefiLlama dan Binance Futures (funding, OI, LSR) tidak membutuhkan API key.

### 4. Deploy ke VPS
```bash
chmod +x deploy.sh
sudo bash deploy.sh
```

Script akan:
- Install Python & dependencies
- Membuat systemd service `cryptobot`
- Start bot secara otomatis

### 5. Cek Status
```bash
systemctl status cryptobot
journalctl -u cryptobot -f     # live logs
```

### 6. Jalankan Dashboard (opsional)
```bash
# Buka terminal terpisah di VPS
source venv/bin/activate
python dashboard.py

# Akses dari browser:
# http://IP_VPS_KAMU:8080
```

---

## Perintah Telegram

| Command | Deskripsi |
|---------|-----------|
| `/start` | Tampilkan menu utama |
| `/analyze SOL` | Analisa lengkap 1 koin (full 8-layer report) |
| `/scan` | Scan semua watchlist — kirim TOP 2 sinyal terkuat |
| `/scanall` | Full scan + kirim laporan semua koin |
| `/list` | Lihat watchlist saat ini |
| `/add MAJOR AVAX` | Tambah koin ke kategori tertentu |
| `/remove BRETT` | Hapus koin dari watchlist |
| `/status` | Cek status bot + koneksi API (real-time check) |

---

## Sistem Scoring 12 Poin

Setiap koin dinilai dua kali — sekali untuk LONG, sekali untuk SHORT.

| Layer | Faktor | Max |
|-------|--------|-----|
| L1 | Market Regime (BTC Weekly EMA200) | 2 |
| L2 | HTF Structure — BOS/CHoCH/HH+HL | 3 |
| L3 | EMA Alignment (20/50/200) | 2 |
| L4 | Elliott Wave — impulse position | 1 |
| L5 | LTF Confirmation (4H FVG + displacement) | 1 |
| L6 | Fundamental (FDV ratio, vol, RS vs BTC) | 1 |
| L7 | On-Chain DeFi (DEX vol, OI, hacks) | 1 |
| L8 | Sentiment (funding, LSR, taker, CMC) | 1 |
| **Total** | | **12** |

**Threshold**:
- **≥ 8** → HIGH CONVICTION — dikirim ke Telegram
- **5-7** → MODERATE — hanya tampil di `/analyze`
- **< 5** → NO TRADE — diabaikan

---

## Trade Rules

```
Entry   : Harga pasar saat sinyal HIGH CONVICTION muncul
SL      : max(ATR×2, 5% dari entry) — dipilih yang lebih kecil risikonya
TP1     : +2R  dari entry (minimum target swing)
TP2     : +2.618R (Fibonacci extension)
TP3     : +3.618R (Fibonacci extension)
TP4     : +4.236R (Full extension / moon target)

Timeframe : Swing — HTF=1D, LTF=4H, Regime=1W
Minimum RR: 1:2
Maximum SL: 5% dari entry
```

---

## Struktur File

```
crypto_bias_bot/
├── bot.py           — Telegram interface & auto-scan (8 jam)
├── analyzer.py      — Unified 12-point scoring engine
├── technical.py     — ICT/SMC + Elliott + Regime + EMA multi-period
├── sentiment.py     — CMC global metrics + Binance Futures sentiment
├── defillama.py     — On-chain data (TVL, unlocks, hacks) — gratis
├── journal.py       — SQLite signal tracker & statistik
├── dashboard.py     — Flask monitoring server (port 8080)
├── config.py        — API keys (dengan dokumentasi fungsi tiap key)
├── requirements.txt — Python dependencies
├── deploy.sh        — Script deploy ke VPS
├── templates/
│   └── index.html   — Dashboard dark theme UI
├── BLUEPRINT.md     — Dokumentasi arsitektur & workflow lengkap
└── README.md        — File ini
```

---

## Dashboard

Akses di `http://IP_VPS:8080` setelah menjalankan `python dashboard.py`.

Fitur:
- **Stats bar**: Winrate, avg PnL, running positions, total signals
- **Running Positions**: Posisi aktif dengan entry/SL/TP
- **Scan History**: Riwayat setiap scan run + top 2 koin
- **Trade Journal**: Semua sinyal dengan filter (Running/Win/Loss/Long/Short)
- **Update Modal**: Update status sinyal (WIN_TP1/WIN_TP2/LOSS/CANCELLED) langsung dari dashboard
- Auto-refresh setiap 30 detik

---

## Watchlist Default

```python
MAJOR:   BTC, ETH, SOL, BNB
MIDCAP:  ONDO, PENDLE, RNDR, TIA, INJ, SUI, SEI
LOWCAP:  PEPE, WIF, BRETT, BOME, TURBO, FLOKI
```

---

## Hard Blockers (Auto-Skip)

Sinyal tidak akan dikirim jika:
1. **Token Unlock Risk** — unlock > 5% market cap dalam 30 hari (DefiLlama)
2. **Bear Market** — BTC di bawah EMA200 weekly + sinyal LONG
3. **Low Confidence** — technical score rendah sebelum full scoring
4. **RR Tidak Valid** — TP1 tidak mencapai 2R setelah SL di-cap 5%

---

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| Bot tidak start | Cek `journalctl -u cryptobot -f`, pastikan API keys valid |
| Auto-scan tidak jalan | Pastikan `python-telegram-bot[job-queue]` terinstall |
| Dashboard tidak bisa diakses | Buka port 8080 di firewall VPS: `ufw allow 8080` |
| Semua hasil NO TRADE | Normal jika market ranging — tunggu breakout yang jelas |
| CMC error | Cek quota limit, fallback ke CoinGecko otomatis |

---

## Lisensi

Untuk penggunaan pribadi. Bukan financial advice.
