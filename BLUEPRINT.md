# CRYPTO BIAS BOT — BLUEPRINT & WORKFLOW
## Swing Trade Signal Engine | ICT/SMC + Elliott + Regime + On-Chain

---

## DAFTAR MASALAH YANG DITEMUKAN & DIPERBAIKI

### technical.py
| # | Masalah | Perbaikan |
|---|---------|-----------|
| 1 | `h['ema']['price_vs_ema']` — key tidak ada di versi baru EMA | Ganti ke `price_vs_ema50` / `price_vs_ema200` |
| 2 | `ema_score <= 2` dilabeli bearish — tapi score 2 = 3 bullish dari 5 kondisi, sebenarnya mildly bullish | Threshold diubah: bearish = score 0-1, neutral = 2 |
| 3 | Regime score dihitung tapi tidak dipakai di confluence manapun | Diintegrasikan ke Layer 1 scoring |
| 4 | Elliott wave score dihitung tapi tidak masuk confluence | Diintegrasikan ke Layer 4 scoring |
| 5 | CHoCH: BOS selalu `None` pada CHoCH structures | BOS untuk CHoCH diabaikan secara benar (CHoCH sendiri sudah sinyal) |

### analyzer.py
| # | Masalah | Perbaikan |
|---|---------|-----------|
| 1 | Confluence tidak direktional — faktor seperti `fdv_ratio<1.5` selalu menambah +1 tanpa peduli arah | Scoring dipisah LONG score vs SHORT score |
| 2 | `fund_bias = "Bearish"` diset tapi tidak mengurangi skor confluence | Bearish fundamental mengurangi LONG score |
| 3 | Sentiment module (`sentiment.py`) tidak pernah diimport | Diintegrasikan ke Layer 8 |
| 4 | Tiga sistem confidence paralel (tech confidence, confluence, defi score) tidak terkoneksi | Digabung ke satu sistem 12 poin |
| 5 | TP label masih "TP1 (1R)" padahal TP1 = 2R | Dibenarkan ke "TP1 (2R)" |
| 6 | `max_score` hardcode 6, padahal confluence bisa melebihi itu | Dihitung dinamis dari layer yang tersedia |
| 7 | `quick_scan` kadang return 2-tuple, kadang 3-tuple | Selalu return 3 nilai dengan sentinel |

### defillama.py
| # | Masalah | Perbaikan |
|---|---------|-----------|
| 1 | Stablecoin supply >150B SELALU true (supply aktual ~$200B+) = poin gratis | Ubah ke perubahan supply 7d (naik = bullish signal) |
| 2 | OI elevated dan DEX spike tidak peduli arah | Ditambah parameter `direction` untuk scoring |
| 3 | Hacks score bisa negatif tapi tidak ada batas bawah yang dihandle | Batas: skor tidak bisa di bawah 0 kecuali unlock blocker |

### sentiment.py
| # | Masalah | Perbaikan |
|---|---------|-----------|
| 1 | File dibuat tapi tidak pernah diimport/digunakan | Diintegrasikan ke analyzer Layer 8 |

---

## ARSITEKTUR SISTEM

```
TELEGRAM USER
     │
     ▼
┌─────────────┐
│   bot.py    │  ← Interface: 8 commands, auto-scan 8h, top-2 filter
└──────┬──────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│                        analyzer.py                           │
│   CryptoBiasAnalyzer                                         │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Binance  │  │   CMC    │  │DefiLlama │  │ Sentiment  │  │
│  │ ticker   │  │ quotes   │  │ on-chain │  │   engine   │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘  │
│                                                              │
│  UNIFIED SCORING ENGINE (12 pts max)                         │
│  L1: Regime (0-2)  L2: Structure (0-3)  L3: EMA (0-2)       │
│  L4: Elliott (0-1) L5: LTF Confirm (0-1)                    │
│  L6: Fundamental (0-1) L7: DeFi (0-1) L8: Sentiment (0-1)  │
└──────────────────────────────────────────────────────────────┘
       │                    │
       ▼                    ▼
┌─────────────┐      ┌─────────────────┐
│ technical.py│      │   journal.py    │
│             │      │                 │
│ TechnicalEngine    │ SQLite tracker  │
│ ┌─────────┐ │      │ signals + stats │
│ │Regime 1W│ │      └────────┬────────┘
│ │HTF 1D   │ │               │
│ │LTF 4H   │ │               ▼
│ │Elliott  │ │      ┌─────────────────┐
│ │EMA 20/50│ │      │  dashboard.py   │
│ │/200     │ │      │  Flask + HTML   │
│ │Wyckoff  │ │      │  Dark theme     │
│ │FVG      │ │      └─────────────────┘
│ │Sweep    │ │
│ └─────────┘ │
└─────────────┘

EXTERNAL APIs:
  Binance     → OHLCV (1W/1D/4H), ticker, futures public
  CMC         → Fundamental quotes, global metrics, trending
  CoinGecko   → Fallback fundamental data (gratis)
  DefiLlama   → TVL, DEX vol, unlocks, hacks (gratis)
  Binance Futures (public) → funding rate, OI, LSR, taker ratio
  Google News RSS → catalyst detection (gratis)
```

---

## WORKFLOW SCAN

```
TRIGGER: /scan, /scanall, atau auto-scan job (8 jam)
          │
          ▼
STEP 1: PRE-FILTER (cepat, tanpa berat)
  ├─ fetch_ticker() via Binance
  ├─ Cek RS vs BTC: if abs(rs_diff) < 0.5 → SKIP (terlalu flat)
  └─ Cek volume: if vol < threshold → SKIP (tidak liquid)
          │
          ▼
STEP 2: TECHNICAL ANALYSIS (full_analysis)
  ├─ Fetch 1W BTC + 1W Coin (regime)
  ├─ Fetch 1D Coin (HTF: structure, EMA, Wyckoff, FVG, Elliott, sweep)
  ├─ Fetch 4H Coin (LTF: structure, displacement, FVG, sweep)
  └─ Compute HTF bias direction
          │
          ▼
STEP 3: HARD BLOCKERS
  ├─ Token unlock risk? → SKIP
  ├─ Regime = BEAR_MARKET + direction = LONG? → SKIP
  └─ Technical confidence = LOW? → SKIP
          │
          ▼
STEP 4: UNIFIED SCORING (12 pts max, per direction)
  ├─ L1: Regime (BTC weekly EMA)          0-2 pts
  ├─ L2: HTF Structure (BOS/CHoCH)        0-3 pts
  ├─ L3: EMA Alignment (20/50/200)        0-2 pts
  ├─ L4: Elliott Wave (impulse + position) 0-1 pt
  ├─ L5: LTF Confirmation (4H FVG+disp)  0-1 pt
  ├─ L6: Fundamental (FDV, RS, vol/mcap)  0-1 pt
  ├─ L7: On-Chain DeFi (directional)      0-1 pt
  └─ L8: Sentiment (funding, LSR, taker)  0-1 pt
          │
          ▼
STEP 5: CONVICTION THRESHOLD
  ├─ Score ≥ 8 → HIGH CONVICTION (trade eligible)
  ├─ Score 5-7 → MODERATE (reduced size, skip in auto-scan)
  └─ Score < 5 → NO TRADE → SKIP
          │
          ▼
STEP 6: EXECUTION PLAN (jika HIGH CONVICTION)
  ├─ SL = max(ATR×2, price×5%) → pilih yang lebih kecil risikonya
  ├─ Validasi RR: TP1 = 2R → jika tidak valid → NO TRADE
  ├─ TP1=2R, TP2=2.618R, TP3=3.618R, TP4=4.236R
  └─ Log ke journal.db
          │
          ▼
STEP 7: RANKING & OUTPUT (untuk auto-scan dan /scan)
  ├─ Sort semua hasil by total score (descending)
  ├─ Ambil TOP 2 saja
  └─ Kirim via Telegram
```

---

## SCORING SYSTEM DETAIL

### Layer 1 — Market Regime (0-2 pts)
```
Sumber: BTC Weekly EMA50/200 + Coin Weekly EMA200

BULL_MARKET (BTC price > EMA50 > EMA200 weekly):
  + Coin above EMA200 weekly → 2 pts
  + Coin below EMA200 weekly → 1 pt

TRANSITION_BULL (BTC price > EMA200 but < EMA50):
  → 1 pt (cautious)

RANGING:
  → 1 pt

TRANSITION_BEAR (BTC price < EMA200 but > EMA50):
  → 0 pt (no LONG) / 1 pt (SHORT)

BEAR_MARKET (BTC price < EMA50 < EMA200 weekly):
  → 0 pt LONG (BLOCKED) / 2 pt SHORT
```

### Layer 2 — HTF Structure (0-3 pts)
```
Sumber: 1D Market Structure (BOS/CHoCH)

BULLISH HH+HL:      +2 LONG, -2 SHORT (tidak bisa diconflue untuk short)
CHoCH BULLISH:      +1 LONG, 0 SHORT
RANGING:            0 LONG, 0 SHORT
CHoCH BEARISH:      0 LONG, +1 SHORT
BEARISH LH+LL:      -2 LONG, +2 SHORT

BOS Confirmed:      +1 bonus untuk arah yang sesuai
→ Max: 3 pts (2 structure + 1 BOS bonus)
```

### Layer 3 — EMA Alignment (0-2 pts)
```
Sumber: 1D EMA20/EMA50/EMA200

ema_score = sum of [price>ema20, price>ema50, price>ema200, ema20>ema50, ema50>ema200]

Untuk LONG:
  ema_score 5 (perfect bull) → 2 pts
  ema_score 4               → 2 pts
  ema_score 3               → 1 pt
  ema_score 0-2             → 0 pt

Untuk SHORT:
  ema_score 0 (perfect bear) → 2 pts
  ema_score 1                → 2 pts
  ema_score 2                → 1 pt
  ema_score 3-5              → 0 pt
```

### Layer 4 — Elliott Wave (0-1 pt)
```
Sumber: 1D zigzag pivot detection

Kondisi untuk score = 1:
  - Impulse valid (rules 1+2 terpenuhi)
  - direction == arah trade
  - wave_position BUKAN "Post Wave-5" (terlambat)

Kondisi score = 0:
  - Impulse tidak valid / complex/corrective
  - Post Wave-5 (potensi reversal berlawanan)
  - direction berlawanan dengan trade
```

### Layer 5 — LTF Confirmation (0-1 pt)
```
Sumber: 4H structure + FVG + displacement

LONG: +1 jika (4H structure BULLISH) AND (fresh bullish FVG OR bullish displacement)
SHORT: +1 jika (4H structure BEARISH) AND (fresh bearish FVG OR bearish displacement)
```

### Layer 6 — Fundamental (0-1 pt)
```
Sumber: CMC / CoinGecko + Binance ticker

LONG: +1 jika 2 dari 3:
  - FDV/MCap < 1.5 (tidak terdilusi)
  - Vol/MCap > 3% (liquid)
  - RS vs BTC > 0% (outperform)

SHORT: +1 jika 2 dari 3:
  - FDV/MCap > 2 (high dilution pressure)
  - Vol/MCap < 1% (thin, vulnerable)
  - RS vs BTC < -2% (underperform)
```

### Layer 7 — On-Chain DeFi (0-1 pt, dengan hard blocker)
```
Sumber: DefiLlama (gratis)

HARD BLOCKER: Token unlock > 5% mcap dalam 30 hari → skip seluruh koin

LONG: +1 jika DEX vol spike (>1.5x avg 7d) AND stablecoin supply naik 7d
SHORT: +1 jika OI elevated (>1.3x avg) AND tidak ada DEX spike
Hack alert > $50M 30d: -1 dari score ini (min 0)
```

### Layer 8 — Sentiment (0-1 pt)
```
Sumber: Binance Futures public + CMC

Scoring ratio: jika ≥ 50% poin sub-layer terpenuhi → 1 pt

Sub-layers per arah:
  Funding rate: sehat untuk arah tersebut?
  LSR (Long/Short ratio): tidak extreme berlawanan?
  Taker ratio: aggressor sesuai arah?
  CMC F&G proxy (7d/30d change): momentum sesuai?
  Trending bonus: CMC trending? +0.5
  Global mcap direction: bullish/bearish?
```

---

## API KEY MAPPING

| Key | File | Endpoint | Fungsi |
|-----|------|----------|--------|
| `BINANCE_API_KEY` | technical.py, analyzer.py | `/api/v3/klines`, `/api/v3/ticker` | OHLCV + price data |
| `BINANCE_API_KEY` | (implisit) | Futures public endpoints | Rate limit upgrade |
| `CMC_API_KEY` | analyzer.py | `/cryptocurrency/quotes/latest` | MCap, FDV, Vol24h |
| `CMC_API_KEY` | sentiment.py | `/global-metrics/quotes/latest` | BTC dominance, total mcap |
| `CMC_API_KEY` | sentiment.py | `/cryptocurrency/trending/latest` | Trending coins |
| `TELEGRAM_BOT_TOKEN` | bot.py | Telegram API | Bot auth |
| `TELEGRAM_CHAT_ID` | bot.py | - | Access whitelist |
| *(no key)* | defillama.py | api.llama.fi | TVL, DEX vol, unlocks, hacks |
| *(no key)* | sentiment.py | fapi.binance.com | Funding, OI, LSR, taker |

---

## FILE STRUCTURE

```
crypto_bias_bot/
│
├── bot.py            → Telegram interface (commands + auto-scan)
├── analyzer.py       → Unified scoring engine (Layer 1-8)
├── technical.py      → ICT/SMC + Elliott + Regime + EMA
├── sentiment.py      → CMC global + Binance futures sentiment
├── defillama.py      → On-chain intelligence (DefiLlama free API)
├── journal.py        → SQLite signal tracker + stats
├── dashboard.py      → Flask monitoring server
├── config.py         → API keys (dokumentasi fungsi tiap key)
├── requirements.txt  → Python dependencies
├── deploy.sh         → VPS deployment script
├── templates/
│   └── index.html    → Dashboard dark theme UI
├── journal.db        → SQLite database (auto-generated)
├── watchlist.json    → Persistent watchlist (auto-generated)
├── BLUEPRINT.md      → Dokumen ini
└── README.md         → Panduan setup & penggunaan
```

---

## TRADE MANAGEMENT RULES

```
ENTRY:    Harga pasar saat sinyal HIGH CONVICTION muncul
SL:       max(ATR×2, entry×5%) → pilih yang paling kecil risikonya
TP1:      Entry + Risk×2.0    (2R — minimum target swing)
TP2:      Entry + Risk×2.618  (Fibonacci 2.618 extension)
TP3:      Entry + Risk×3.618  (Fibonacci 3.618 extension)
TP4:      Entry + Risk×4.236  (Full extension — moon target)

INVALIDATION: Close di bawah SL (bukan wick)
PARTIAL EXIT: Disarankan ambil TP1 (50% posisi), sisanya ke TP2+
```

---

## CONVICTION THRESHOLDS

```
Score  | Level           | Action
-------|-----------------|---------------------------
≥ 8    | HIGH CONVICTION | Trade + log + kirim Telegram
5 - 7  | MODERATE        | Hanya tampil di /analyze, skip auto-scan
< 5    | LOW / NO TRADE  | Skip sepenuhnya
```

---

## SISTEM WATCHLIST

```
MAJOR:   BTC, ETH, SOL, BNB
MIDCAP:  ONDO, PENDLE, RNDR, TIA, INJ, SUI, SEI
LOWCAP:  PEPE, WIF, BRETT, BOME, TURBO, FLOKI

Tambah:  /add MAJOR AVAX
Hapus:   /remove BRETT
Lihat:   /list
```
