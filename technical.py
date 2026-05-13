"""
TECHNICAL ENGINE — ICT/SMC + Wyckoff + Elliott Wave + Market Regime
Swing Trade: HTF=1D (bias), LTF=4H (entry), Regime=1W (context)
Min RR 1:2 | Max SL 5%
"""
import numpy as np
import pandas as pd


class TechnicalEngine:

    def __init__(self, exchange):
        self.exchange = exchange

    # ──────────────────────────────────────────────────────────────────────
    # DATA FETCHING
    # ──────────────────────────────────────────────────────────────────────
    async def fetch_ohlcv(self, symbol, timeframe="1d", limit=200):
        try:
            raw = await self.exchange.fetch_ohlcv(f"{symbol}/USDT", timeframe, limit=limit)
            df  = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception:
            return None

    # ──────────────────────────────────────────────────────────────────────
    # EMA — Multi-Period (20 / 50 / 200)
    # ──────────────────────────────────────────────────────────────────────
    def calc_ema(self, df, periods=(20, 50, 200)):
        """
        Hitung EMA untuk multiple periods.
        Untuk swing trade, EMA50 dan EMA200 adalah yang paling penting.
        Alignment EMA20 > EMA50 > EMA200 = strong bullish trend.
        """
        closes  = df["close"].values
        current = closes[-1]
        result  = {}

        for p in periods:
            if len(closes) < p:
                result[f"ema{p}"] = closes[-1]
            else:
                ema_val = pd.Series(closes).ewm(span=p, adjust=False).mean().values[-1]
                result[f"ema{p}"] = round(ema_val, 8)

        e20, e50, e200 = result["ema20"], result["ema50"], result["ema200"]

        # Trend alignment scoring
        bullish_count = sum([current > e20, current > e50, current > e200,
                             e20 > e50, e50 > e200])
        bearish_count = sum([current < e20, current < e50, current < e200,
                             e20 < e50, e50 < e200])

        if bullish_count >= 4:
            trend = "STRONG_BULL"
        elif bullish_count >= 3:
            trend = "BULL"
        elif bearish_count >= 4:
            trend = "STRONG_BEAR"
        elif bearish_count >= 3:
            trend = "BEAR"
        else:
            trend = "MIXED"

        result.update({
            "current":  current,
            "trend":    trend,
            "bias":     "Bullish" if bullish_count > bearish_count else "Bearish",
            "price_vs_ema50":  "ABOVE" if current > e50  else "BELOW",
            "price_vs_ema200": "ABOVE" if current > e200 else "BELOW",
            "ema_score": bullish_count,   # 0-5
        })
        return result

    # ──────────────────────────────────────────────────────────────────────
    # MARKET STRUCTURE — BOS / CHoCH (FIXED)
    # ──────────────────────────────────────────────────────────────────────
    def detect_structure(self, df, lookback=5):
        """
        BOS = Break Of Structure: harga CLOSE menembus swing level signifikan.
        CHoCH = Change of Character: perubahan arah dari LH+LL ke HH+HL atau sebaliknya.

        BUG FIX: BOS sebelumnya hanya mengecek current_price > swing_high[-1]
        yang selalu true saat uptrend. Sekarang BOS dikonfirmasi dengan close
        menembus swing level dari trend berlawanan (misal: close > last LH = BOS bull).
        """
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        n      = len(df)

        swing_highs, swing_lows = [], []

        for i in range(lookback, n - lookback):
            if highs[i] == max(highs[i - lookback:i + lookback + 1]):
                swing_highs.append((i, highs[i]))
            if lows[i] == min(lows[i - lookback:i + lookback + 1]):
                swing_lows.append((i, lows[i]))

        structure = "RANGING"
        last_bos  = None
        choch     = False

        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            sh1, sh2 = swing_highs[-2][1], swing_highs[-1][1]
            sl1, sl2 = swing_lows[-2][1],  swing_lows[-1][1]

            if sh2 > sh1 and sl2 > sl1:
                structure = "BULLISH (HH + HL)"
            elif sh2 < sh1 and sl2 < sl1:
                structure = "BEARISH (LH + LL)"
            elif sh2 < sh1 and sl2 > sl1:
                structure = "CHoCH BULLISH (HL terbentuk)"
                choch = True
            elif sh2 > sh1 and sl2 < sl1:
                structure = "CHoCH BEARISH (LH terbentuk)"
                choch = True

            # ── BOS yang benar ──────────────────────────────────────────
            # Hanya dicek pada struktur yang CONFIRMED (bukan CHoCH).
            # CHoCH sendiri sudah merupakan sinyal perubahan struktur.
            # Dalam downtrend (LH+LL): BOS bullish jika close > last LH
            # Dalam uptrend (HH+HL):   BOS bearish jika close < last HL
            current_close = closes[-1]

            if structure == "BEARISH (LH + LL)" and len(swing_highs) >= 1:
                last_lh = swing_highs[-1][1]
                if current_close > last_lh:
                    last_bos = f"✅ BOS BULLISH — Close {current_close:.5f} > LH {last_lh:.5f}"

            elif structure == "BULLISH (HH + HL)" and len(swing_lows) >= 1:
                last_hl = swing_lows[-1][1]
                if current_close < last_hl:
                    last_bos = f"⚠️ BOS BEARISH — Close {current_close:.5f} < HL {last_hl:.5f}"

        return {
            "structure":   structure,
            "bos":         last_bos,
            "choch":       choch,
            "swing_highs": swing_highs[-3:] if swing_highs else [],
            "swing_lows":  swing_lows[-3:]  if swing_lows  else [],
        }

    # ──────────────────────────────────────────────────────────────────────
    # MARKET REGIME — Bull / Bear / Ranging
    # ──────────────────────────────────────────────────────────────────────
    async def detect_market_regime(self, symbol, regime_tf="1d") -> dict:
        """
        Regime detection menggunakan data Weekly BTC + simbol sendiri.
        Weekly EMA200 = garis pemisah bull/bear market paling reliable.

        Klasifikasi:
          BULL_MARKET      — harga > EMA50 > EMA200 weekly
          BEAR_MARKET      — harga < EMA50 < EMA200 weekly
          TRANSITION_BULL  — harga > EMA200 tapi belum di atas EMA50
          TRANSITION_BEAR  — harga < EMA200 tapi belum di bawah EMA50
          RANGING          — mixed signals
        """
        import asyncio
        # Fetch data secara serentak (concurrent)
        try:
            df_btc_w, df_sym_w = await asyncio.gather(
                self.fetch_ohlcv("BTC", regime_tf, 250),
                self.fetch_ohlcv(symbol, regime_tf, 100)
            )
        except Exception:
            df_btc_w, df_sym_w = None, None

        regime_btc  = "UNKNOWN"
        regime_coin = "UNKNOWN"
        regime_score = 1  # neutral default

        if df_btc_w is not None:
            ema_btc = self.calc_ema(df_btc_w)
            trend_btc = ema_btc["trend"]
            price_btc = ema_btc["current"]
            e50_btc   = ema_btc["ema50"]
            e200_btc  = ema_btc["ema200"]

            if price_btc > e50_btc > e200_btc:
                regime_btc = "BULL_MARKET"
                regime_score = 2
            elif price_btc < e50_btc < e200_btc:
                regime_btc = "BEAR_MARKET"
                regime_score = 0
            elif price_btc > e200_btc:
                regime_btc = "TRANSITION_BULL"
                regime_score = 1
            elif price_btc < e200_btc:
                regime_btc = "TRANSITION_BEAR"
                regime_score = 0
            else:
                regime_btc = "RANGING"
                regime_score = 1

        if df_sym_w is not None:
            ema_sym = self.calc_ema(df_sym_w)
            e200_sym = ema_sym["ema200"]
            curr_sym = ema_sym["current"]
            regime_coin = "ABOVE_EMA200" if curr_sym > e200_sym else "BELOW_EMA200"

        # Coin below EMA200 weekly walaupun BTC bull = regime lemah untuk coin itu
        if regime_coin == "BELOW_EMA200" and regime_btc == "BULL_MARKET":
            regime_score = 1  # Turunkan dari 2 ke 1

        return {
            "regime_btc":   regime_btc,
            "regime_coin":  regime_coin,
            "score":        regime_score,    # 0, 1, atau 2
            "label":        regime_btc,
        }

    # ──────────────────────────────────────────────────────────────────────
    # ELLIOTT WAVE — Simplified 5-Wave Detection
    # ──────────────────────────────────────────────────────────────────────
    def detect_elliott_wave(self, df) -> dict:
        """
        Simplified Elliott Wave detection menggunakan zigzag pivot.

        Rules yang dicek:
          - Wave 3 tidak boleh terpendek dari Wave 1 dan Wave 5
          - Wave 4 tidak boleh overlap Wave 1
          - Fibonacci retracement Wave 2 = 38.2%-78.6% dari Wave 1
          - Fibonacci extension Wave 3 = 1.618x Wave 1

        Output: estimated wave position + score 0/1
        """
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        n      = len(df)

        # Step 1: Zigzag pivot detection dengan significance filter
        min_bars = 5  # minimal jarak antar pivot
        pivots   = []  # (index, price, 'H'/'L')

        for i in range(min_bars, n - min_bars):
            window_h = highs[i - min_bars:i + min_bars + 1]
            window_l = lows[i  - min_bars:i + min_bars + 1]

            if highs[i] == max(window_h):
                pivots.append((i, highs[i], 'H'))
            elif lows[i] == min(window_l):
                pivots.append((i, lows[i], 'L'))

        # Step 2: Filter ke alternating sequence (hapus duplikat tipe berturut-turut)
        alternating = []
        for p in pivots:
            if not alternating or alternating[-1][2] != p[2]:
                alternating.append(p)
            elif p[2] == 'H' and p[1] > alternating[-1][1]:
                alternating[-1] = p   # Pertahankan swing high tertinggi
            elif p[2] == 'L' and p[1] < alternating[-1][1]:
                alternating[-1] = p   # Pertahankan swing low terendah

        if len(alternating) < 5:
            return {
                "wave_position": "UNCLEAR",
                "direction":     "NEUTRAL",
                "impulse_valid": False,
                "score":         0,
                "note":          f"Pivot tidak cukup ({len(alternating)} < 5)"
            }

        # Step 3: Ambil 6 titik terakhir dan coba identifikasi 5-wave impulse
        pts = alternating[-6:] if len(alternating) >= 6 else alternating[-5:]
        current_price = closes[-1]

        # Tentukan apakah bullish atau bearish impulse dari tipe pivot pertama
        first_type = pts[0][2]

        wave_pos    = "UNCLEAR"
        direction   = "NEUTRAL"
        impulse_ok  = False
        score       = 0
        note        = ""

        def fib_ok(ratio, low, high):
            """Cek apakah ratio masuk range Fibonacci yang valid."""
            return low <= ratio <= high

        if first_type == 'L' and len(pts) >= 5:
            # Potential BULLISH impulse: L0→H1→L2→H3→L4→H5
            direction = "BULLISH"
            prices    = [p[1] for p in pts[:6]] if len(pts) >= 6 else [p[1] for p in pts]

            if len(prices) >= 6:
                l0, h1, l2, h3, l4, h5 = prices

                w1 = h1 - l0
                w2 = h1 - l2   # retracement
                w3 = h3 - l2
                w4 = h3 - l4   # retracement
                w5 = h5 - l4

                # Elliott Rules validation
                w2_retracement = (w2 / w1) if w1 > 0 else 0
                w4_retracement = (w4 / w3) if w3 > 0 else 0
                w3_vs_w1       = (w3 / w1) if w1 > 0 else 0

                rule1 = w3 >= w1 * 0.9         # Wave 3 tidak terpendek
                rule2 = l4 > l2                 # Wave 4 tidak overlap Wave 1 top (simplified: l4 > l2)
                rule3 = fib_ok(w2_retracement, 0.30, 0.80)  # Wave 2 retrace 30-80% wave 1
                rule4 = w3_vs_w1 >= 1.0         # Wave 3 minimal = Wave 1

                impulse_ok = rule1 and rule2

                if impulse_ok:
                    note = f"✅ Bullish impulse valid (W3={w3_vs_w1:.2f}x W1)"
                    # Tentukan posisi current price dalam wave
                    if current_price >= h5:
                        wave_pos = "Post Wave-5 / ABC Correction Ahead"
                        score    = 0   # Late, potensi reversal
                    elif current_price > l4:
                        wave_pos = "Wave 5 Aktif (final impulse)"
                        score    = 1
                    elif current_price > h3:
                        wave_pos = "Wave 4 Koreksi (buy the dip zone)"
                        score    = 1
                    elif current_price > l2:
                        wave_pos = "Wave 3 Aktif (strongest wave)"
                        score    = 1
                    else:
                        wave_pos = "Wave 2 Koreksi (early entry)"
                        score    = 1
                else:
                    # Rules gagal — mungkin corrective structure
                    wave_pos = "Complex / Corrective"
                    note     = f"⚠️ Rules gagal (R1:{rule1} R2:{rule2} R3:{rule3})"
                    score    = 0

            elif len(prices) == 5:
                l0, h1, l2, h3, l4 = prices
                direction = "BULLISH"
                if current_price > h3:
                    wave_pos = "Wave 5 atau Post-Wave 3"
                    score    = 1
                elif current_price > l2:
                    wave_pos = "Wave 4 (koreksi) — setup entry"
                    score    = 1
                else:
                    wave_pos = "Wave 2 atau awal Wave 3"
                    score    = 1
                note = "Estimasi (5 pivot)"

        elif first_type == 'H' and len(pts) >= 5:
            # Potential BEARISH impulse: H0→L1→H2→L3→H4→L5
            direction = "BEARISH"
            prices    = [p[1] for p in pts[:6]] if len(pts) >= 6 else [p[1] for p in pts]

            if len(prices) >= 6:
                h0, l1, h2, l3, h4, l5 = prices
                w1 = h0 - l1
                w3 = h2 - l3
                impulse_ok = w3 >= w1 * 0.9 and h4 < h2

                if impulse_ok:
                    note = "✅ Bearish impulse valid"
                    if current_price <= l5:
                        wave_pos = "Post Wave-5 / ABC Bounce Ahead"
                        score    = 0
                    elif current_price < h4:
                        wave_pos = "Wave 5 Bearish Aktif"
                        score    = 1
                    elif current_price < l3:
                        wave_pos = "Wave 4 Koreksi Bearish (short setup)"
                        score    = 1
                    else:
                        wave_pos = "Wave 3 Bearish (strongest move)"
                        score    = 1
                else:
                    wave_pos = "Complex / Corrective"
                    note     = "⚠️ Bearish impulse tidak valid"
                    score    = 0
            else:
                # 5 pivot tanpa 6 = tidak bisa validasi impulse secara penuh
                direction = "BEARISH"
                wave_pos  = "UNCLEAR (pivot kurang)"
                score     = 0   # tidak beri score tanpa validasi
                note      = "Butuh min 6 pivot untuk validasi bearish impulse"

        return {
            "wave_position": wave_pos,
            "direction":     direction,
            "impulse_valid": impulse_ok,
            "score":         score,
            "note":          note or f"{len(alternating)} pivot terdeteksi",
        }

    # ──────────────────────────────────────────────────────────────────────
    # LIQUIDITY SWEEP — PDH/PDL, EQH/EQL
    # ──────────────────────────────────────────────────────────────────────
    def detect_liquidity_sweep(self, df) -> dict:
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values

        # Previous periode High/Low — gunakan 50% pertama dari data
        mid = len(highs) // 2
        pdh = max(highs[:mid]) if mid > 0 else max(highs)
        pdl = min(lows[:mid])  if mid > 0 else min(lows)

        sweeps = []
        for i in range(-10, 0):
            idx = len(highs) + i
            if idx < 0:
                continue
            if highs[idx] > pdh and closes[idx] < pdh:
                sweeps.append(f"🔻 Sweep HIGH {highs[idx]:.5f} (PDH: {pdh:.5f}) → Bearish")
            if lows[idx] < pdl and closes[idx] > pdl:
                sweeps.append(f"🔺 Sweep LOW {lows[idx]:.5f} (PDL: {pdl:.5f}) → Bullish")

        # Equal Highs / Equal Lows
        eqh, eql = None, None
        tolerance = (max(highs[-20:]) - min(lows[-20:])) * 0.002

        recent_sh, recent_sl = [], []
        for i in range(3, min(50, len(highs) - 3)):
            idx = len(highs) - i
            if highs[idx] == max(highs[idx - 2:idx + 3]):
                recent_sh.append(highs[idx])
            if lows[idx] == min(lows[idx - 2:idx + 3]):
                recent_sl.append(lows[idx])

        for j in range(len(recent_sh)):
            for k in range(j + 1, len(recent_sh)):
                if abs(recent_sh[j] - recent_sh[k]) < tolerance:
                    eqh = (recent_sh[j] + recent_sh[k]) / 2
                    break

        for j in range(len(recent_sl)):
            for k in range(j + 1, len(recent_sl)):
                if abs(recent_sl[j] - recent_sl[k]) < tolerance:
                    eql = (recent_sl[j] + recent_sl[k]) / 2
                    break

        return {
            "pdh":    pdh,
            "pdl":    pdl,
            "sweeps": sweeps if sweeps else ["Belum ada sweep terdeteksi"],
            "eqh":    eqh,
            "eql":    eql,
        }

    # ──────────────────────────────────────────────────────────────────────
    # FVG — Fair Value Gap
    # ──────────────────────────────────────────────────────────────────────
    def detect_fvg(self, df) -> dict:
        highs = df["high"].values
        lows  = df["low"].values
        fvg_bull, fvg_bear = [], []

        start = max(2, len(highs) - 50)
        for i in range(start, len(highs)):
            if lows[i] > highs[i - 2]:
                fvg_bull.append({"top": lows[i], "bottom": highs[i-2], "idx": i})
            if highs[i] < lows[i - 2]:
                fvg_bear.append({"top": lows[i-2], "bottom": highs[i], "idx": i})

        # Cek apakah FVG terbaru masih fresh (belum diisi harga)
        current = highs[-1]
        latest_bull = fvg_bull[-1] if fvg_bull else None
        latest_bear = fvg_bear[-1] if fvg_bear else None

        bull_fresh = (latest_bull and current <= latest_bull["bottom"]) if latest_bull else False
        bear_fresh = (latest_bear and current >= latest_bear["top"])    if latest_bear else False

        return {
            "bullish_fvg":   latest_bull,
            "bearish_fvg":   latest_bear,
            "bull_fresh":    bull_fresh,
            "bear_fresh":    bear_fresh,
            "total_bullish": len(fvg_bull),
            "total_bearish": len(fvg_bear),
        }

    # ──────────────────────────────────────────────────────────────────────
    # DISPLACEMENT — Institutional Momentum
    # ──────────────────────────────────────────────────────────────────────
    def detect_displacement(self, df) -> dict:
        closes = df["close"].values
        opens  = df["open"].values
        highs  = df["high"].values
        lows   = df["low"].values

        atr_vals = []
        for i in range(1, len(highs)):
            tr = max(highs[i]-lows[i],
                     abs(highs[i]-closes[i-1]),
                     abs(lows[i]-closes[i-1]))
            atr_vals.append(tr)
        atr = np.mean(atr_vals[-14:]) if len(atr_vals) >= 14 else np.mean(atr_vals) if atr_vals else 1

        displacements = []
        for i in range(-5, 0):
            idx  = len(closes) + i
            if idx < 0: continue
            body = abs(closes[idx] - opens[idx])
            if body > atr * 1.5:
                d = "BULLISH" if closes[idx] > opens[idx] else "BEARISH"
                displacements.append(f"{d} Displacement (body: {body:.5f}, ATR: {atr:.5f})")

        return {
            "displacements": displacements if displacements else ["Tidak ada displacement baru"],
            "atr": atr,
        }

    # ──────────────────────────────────────────────────────────────────────
    # WYCKOFF PHASE
    # ──────────────────────────────────────────────────────────────────────
    def detect_wyckoff_phase(self, df) -> dict:
        closes  = df["close"].values
        volumes = df["volume"].values
        highs   = df["high"].values
        lows    = df["low"].values

        price_range  = max(highs[-100:]) - min(lows[-100:])
        recent_range = max(highs[-15:])  - min(lows[-15:])
        range_ratio  = recent_range / price_range if price_range > 0 else 0

        avg_vol_old  = np.mean(volumes[-100:-50]) if len(volumes) >= 100 else np.mean(volumes)
        avg_vol_new  = np.mean(volumes[-20:])
        vol_expanding = avg_vol_new > avg_vol_old * 1.2

        current          = closes[-1]
        range_low        = min(lows[-100:])
        range_high       = max(highs[-100:])
        position_in_range = ((current - range_low) / (range_high - range_low)
                              if range_high != range_low else 0.5)

        if range_ratio < 0.25 and position_in_range < 0.25:
            phase = "ACCUMULATION (Spring Zone)"
        elif range_ratio < 0.25 and position_in_range > 0.75:
            phase = "DISTRIBUTION (UTAD Zone)"
        elif vol_expanding and position_in_range > 0.6:
            phase = "MARKUP (Trending Up)"
        elif vol_expanding and position_in_range < 0.4:
            phase = "MARKDOWN (Trending Down)"
        else:
            phase = "RANGING / CAUSE BUILDING"

        return {
            "phase":            phase,
            "position_in_range": f"{position_in_range*100:.0f}%",
            "vol_expanding":    vol_expanding,
        }

    # ──────────────────────────────────────────────────────────────────────
    # FIBONACCI TP
    # ──────────────────────────────────────────────────────────────────────
    def calc_fibonacci_tp(self, entry, sl, direction="LONG"):
        """
        TP1 = 2R   (minimum swing — wajib RR 1:2)
        TP2 = 2.618R (Fibonacci ekstensi)
        TP3 = 3.618R (Fibonacci ekstensi)
        TP4 = 4.236R (Full extension)
        """
        risk = abs(entry - sl)
        if direction == "LONG":
            return {
                "TP1":      round(entry + risk * 2.0,   6),
                "TP2":      round(entry + risk * 2.618, 6),
                "TP3":      round(entry + risk * 3.618, 6),
                "TP4_MOON": round(entry + risk * 4.236, 6),
            }
        else:
            return {
                "TP1":      round(entry - risk * 2.0,   6),
                "TP2":      round(entry - risk * 2.618, 6),
                "TP3":      round(entry - risk * 3.618, 6),
                "TP4_MOON": round(entry - risk * 4.236, 6),
            }

    # ──────────────────────────────────────────────────────────────────────
    # FULL ANALYSIS
    # ──────────────────────────────────────────────────────────────────────
    def detect_order_blocks(self, df) -> dict:
        """
        Order Block (OB) Detection — Pondasi SMC/ICT.

        Bullish OB: Candle bearish terakhir sebelum impulsive bullish move.
        Bearish OB: Candle bullish terakhir sebelum impulsive bearish move.

        Entry Logic:
          - LONG: beli saat harga kembali ke zona Bullish OB (limit order)
          - SHORT: jual saat harga naik ke zona Bearish OB (limit order)

        OB Zone = [low, high] dari candle OB tersebut.
        """
        opens  = df["open"].values
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        n      = len(df)

        atr_vals = []
        for i in range(1, n):
            atr_vals.append(max(highs[i]-lows[i],
                                abs(highs[i]-closes[i-1]),
                                abs(lows[i]-closes[i-1])))
        atr = float(np.mean(atr_vals[-14:])) if len(atr_vals) >= 14 else float(np.mean(atr_vals)) if atr_vals else 1.0

        bullish_obs = []
        bearish_obs = []

        # Scan 50 candle terakhir
        for i in range(max(2, n - 50), n - 2):
            body_i = abs(closes[i] - opens[i])

            # ── Bullish OB ──────────────────────────────────────────────────
            # Candle i = bearish (close < open)
            # Candle i+1 atau i+2 = bullish impulsive (body > 1.5x ATR)
            if closes[i] < opens[i]:  # candle bearish
                for j in range(i+1, min(i+4, n)):
                    body_j = abs(closes[j] - opens[j])
                    if closes[j] > opens[j] and body_j > atr * 1.2:
                        # Impulsive bullish setelah candle bearish = Bullish OB
                        ob = {
                            "index":     i,
                            "type":      "BULLISH",
                            "ob_high":   round(float(highs[i]), 8),
                            "ob_low":    round(float(lows[i]), 8),
                            "ob_mid":    round(float((highs[i] + lows[i]) / 2), 8),
                            "entry_zone_top":    round(float(highs[i]), 8),
                            "entry_zone_bottom": round(float(lows[i]), 8),
                            "fresh":     closes[-1] > highs[i],  # price above OB = untested
                            "tested":    any(lows[k] <= highs[i] and closes[k] >= lows[i]
                                            for k in range(i+1, n)),
                        }
                        bullish_obs.append(ob)
                        break

            # ── Bearish OB ──────────────────────────────────────────────────
            # Candle i = bullish (close > open)
            # Candle i+1 atau i+2 = bearish impulsive (body > 1.5x ATR)
            elif closes[i] > opens[i]:  # candle bullish
                for j in range(i+1, min(i+4, n)):
                    body_j = abs(closes[j] - opens[j])
                    if closes[j] < opens[j] and body_j > atr * 1.2:
                        ob = {
                            "index":     i,
                            "type":      "BEARISH",
                            "ob_high":   round(float(highs[i]), 8),
                            "ob_low":    round(float(lows[i]), 8),
                            "ob_mid":    round(float((highs[i] + lows[i]) / 2), 8),
                            "entry_zone_top":    round(float(highs[i]), 8),
                            "entry_zone_bottom": round(float(lows[i]), 8),
                            "fresh":     closes[-1] < lows[i],  # price below OB = untested
                            "tested":    any(highs[k] >= lows[i] and closes[k] <= highs[i]
                                            for k in range(i+1, n)),
                        }
                        bearish_obs.append(ob)
                        break

        current = float(closes[-1])

        # Find nearest OB to current price
        nearest_bull = None
        nearest_bear = None

        # Nearest bullish OB below current price (for LONG entry on pullback)
        # Filter: OB di bawah current price, belum di-test, dan masih fresh
        bull_below = [ob for ob in bullish_obs
                      if ob["ob_high"] < current
                      and not ob["tested"]
                      and ob.get("fresh", True)]
        if bull_below:
            nearest_bull = max(bull_below, key=lambda x: x["ob_high"])

        # Nearest bearish OB above current price (for SHORT entry on rally)
        bear_above = [ob for ob in bearish_obs
                      if ob["ob_low"] > current
                      and not ob["tested"]
                      and ob.get("fresh", True)]
        if bear_above:
            nearest_bear = min(bear_above, key=lambda x: x["ob_low"])

        return {
            "bullish_obs":    bullish_obs[-3:] if bullish_obs else [],
            "bearish_obs":    bearish_obs[-3:] if bearish_obs else [],
            "nearest_bull":   nearest_bull,
            "nearest_bear":   nearest_bear,
            "current":        current,
            "has_bull_setup": nearest_bull is not None,
            "has_bear_setup": nearest_bear is not None,
        }


    def detect_golden_death_cross(self, df) -> dict:
        """
        Golden Cross (GC): EMA50 crosses above EMA200 → Strong bullish bias
        Death Cross (DC): EMA50 crosses below EMA200 → Strong bearish bias

        Digunakan sebagai swing trade filter — GC/DC menandai perubahan
        major trend yang signifikan. Ketika GC/DC aktif, score ditambah
        dan RR target dinaikkan (karena trend besar = potensi move besar).

        Lookback: cek 5 candle terakhir untuk fresh cross.
        """
        closes = df["close"].values
        n      = len(closes)

        if n < 205:
            return {"cross": "NONE", "fresh": False, "fresh_gc": False, "fresh_dc": False,
                    "ema50": 0.0, "ema200": 0.0, "spread_pct": 0.0,
                    "bull_bias": False, "bear_bias": False, "score_bonus": 0}

        import pandas as pd
        s = pd.Series(closes)
        ema50_series  = s.ewm(span=50,  adjust=False).mean().values
        ema200_series = s.ewm(span=200, adjust=False).mean().values

        ema50_now  = float(ema50_series[-1])
        ema200_now = float(ema200_series[-1])

        # Cek 5 candle terakhir untuk fresh cross
        fresh_gc = False
        fresh_dc = False
        lookback = min(5, n - 1)

        for i in range(-lookback, 0):
            prev50  = ema50_series[i-1]
            prev200 = ema200_series[i-1]
            curr50  = ema50_series[i]
            curr200 = ema200_series[i]

            if prev50 <= prev200 and curr50 > curr200:
                fresh_gc = True
            if prev50 >= prev200 and curr50 < curr200:
                fresh_dc = True

        # Determine current cross status
        if ema50_now > ema200_now:
            cross = "GOLDEN" if fresh_gc else "ABOVE_EMA200"
            bull_bias = True; bear_bias = False
        else:
            cross = "DEATH" if fresh_dc else "BELOW_EMA200"
            bull_bias = False; bear_bias = True

        # Score bonus: fresh cross = +2, existing cross = +1
        if fresh_gc or fresh_dc:
            score_bonus = 2
        elif cross in ("ABOVE_EMA200", "GOLDEN"):
            score_bonus = 1  # ongoing bull
        elif cross in ("BELOW_EMA200", "DEATH"):
            score_bonus = -1  # ongoing bear (penalti untuk counter-trend)
        else:
            score_bonus = 0

        return {
            "cross":       cross,
            "fresh":       fresh_gc or fresh_dc,
            "fresh_gc":    fresh_gc,
            "fresh_dc":    fresh_dc,
            "ema50":       round(ema50_now, 6),
            "ema200":      round(ema200_now, 6),
            "spread_pct":  round((ema50_now - ema200_now) / ema200_now * 100, 2),
            "bull_bias":   bull_bias,
            "bear_bias":   bear_bias,
            "score_bonus": score_bonus,
        }


    async def full_analysis(self, symbol,
                      htf="4h", mtf="1h", ltf="15m", regime_tf="1d") -> dict | None:
        """
        Intraday MTF Analysis:
          HTF (H4)  — trend bias, structure, Elliott, EMA, Wyckoff
          MTF (H1)  — pullback zone, supply/demand, structure confirmation
          LTF (M15) — entry trigger: displacement, FVG, liquidity sweep
          Regime(1D)— macro context via EMA50/200 weekly BTC
        """
        import asyncio
        try:
            df_htf, df_mtf, df_ltf, regime = await asyncio.gather(
                self.fetch_ohlcv(symbol, htf,  200),
                self.fetch_ohlcv(symbol, mtf,  200),
                self.fetch_ohlcv(symbol, ltf,  300),
                self.detect_market_regime(symbol, regime_tf=regime_tf)
            )
        except Exception:
            return None

        if df_htf is None or df_mtf is None or df_ltf is None:
            return None

        # ── HTF (H4) — Trend Bias ──────────────────────────────────────
        htf_structure = self.detect_structure(df_htf)
        htf_wyckoff   = self.detect_wyckoff_phase(df_htf)
        htf_liquidity = self.detect_liquidity_sweep(df_htf)
        htf_fvg       = self.detect_fvg(df_htf)
        htf_ema       = self.calc_ema(df_htf)
        htf_elliott   = self.detect_elliott_wave(df_htf)
        htf_ob        = self.detect_order_blocks(df_htf)
        htf_gc_dc     = self.detect_golden_death_cross(df_htf)

        # ── MTF (H1) — Structure & Zone ───────────────────────────────
        mtf_structure = self.detect_structure(df_mtf, lookback=3)
        mtf_fvg       = self.detect_fvg(df_mtf)
        mtf_liquidity = self.detect_liquidity_sweep(df_mtf)

        # ── LTF (M15) — Entry Trigger ─────────────────────────────────
        ltf_structure    = self.detect_structure(df_ltf, lookback=3)
        ltf_displacement = self.detect_displacement(df_ltf)
        ltf_fvg          = self.detect_fvg(df_ltf)
        ltf_ob           = self.detect_order_blocks(df_ltf)
        ltf_liquidity    = self.detect_liquidity_sweep(df_ltf)

        # ── Market Regime (1D) ─────────────────────────────────────────
        # Sudah di-fetch bersamaan dengan OHLCV via asyncio.gather di atas


        # ── GC/DC Hard regime filter ──────────────────────────────────
        # Death Cross aktif → block LONG agresif
        # Golden Cross aktif → block SHORT agresif
        gc_dc_block_long  = htf_gc_dc["bear_bias"] and not htf_gc_dc["fresh_gc"]
        gc_dc_block_short = htf_gc_dc["bull_bias"] and not htf_gc_dc["fresh_dc"]

        # ── HTF Bias scoring ───────────────────────────────────────────
        bullish_signals = 0
        bearish_signals = 0

        # Structure (CHoCH dicek DULU)
        s_str_htf = htf_structure["structure"]
        if "CHoCH BULLISH" in s_str_htf:
            bullish_signals += 1
        elif "CHoCH BEARISH" in s_str_htf:
            bearish_signals += 1
        elif s_str_htf == "BULLISH (HH + HL)":
            bullish_signals += 2
        elif s_str_htf == "BEARISH (LH + LL)":
            bearish_signals += 2

        # EMA alignment (0-2)
        ema_score = htf_ema["ema_score"]
        if ema_score >= 4:
            bullish_signals += 2
        elif ema_score >= 3:
            bullish_signals += 1
        elif ema_score <= 1:
            bearish_signals += 2
        elif ema_score == 2:
            bearish_signals += 1

        # Wyckoff (0-1)
        if any(x in htf_wyckoff["phase"] for x in ["ACCUMULATION", "MARKUP"]):
            bullish_signals += 1
        elif any(x in htf_wyckoff["phase"] for x in ["DISTRIBUTION", "MARKDOWN"]):
            bearish_signals += 1

        # Order Block proximity (0-1)
        if htf_ob["has_bull_setup"]:   # price pulling back to bullish OB
            bullish_signals += 1
        if htf_ob["has_bear_setup"]:   # price rallying to bearish OB
            bearish_signals += 1

        # GC/DC bonus (0-1)
        if htf_gc_dc["fresh_gc"]:
            bullish_signals += 1
        elif htf_gc_dc["fresh_dc"]:
            bearish_signals += 1

        # ── MTF + LTF Confirmation ─────────────────────────────────────
        # MTF (H1): pullback ke zone + struktur searah HTF
        mtf_s = mtf_structure["structure"]
        mtf_bull = ("BULLISH" in mtf_s or mtf_fvg["bull_fresh"])
        mtf_bear = ("BEARISH" in mtf_s or mtf_fvg["bear_fresh"])

        # LTF (M15): trigger entry searah
        ltf_bull = (
            any("BULLISH" in d for d in ltf_displacement["displacements"])
            or ltf_fvg["bull_fresh"]
            or "BULLISH" in ltf_structure["structure"]
        )
        ltf_bear = (
            any("BEARISH" in d for d in ltf_displacement["displacements"])
            or ltf_fvg["bear_fresh"]
            or "BEARISH" in ltf_structure["structure"]
        )

        # Konfirmasi penuh: MTF + LTF harus searah
        has_bull_ltf_confirm = mtf_bull and ltf_bull
        has_bear_ltf_confirm = mtf_bear and ltf_bear

        if has_bull_ltf_confirm:
            bullish_signals += 1
        if has_bear_ltf_confirm:
            bearish_signals += 1

        # Final bias
        if bullish_signals > bearish_signals and bullish_signals >= 3:
            htf_bias = "BULLISH"
        elif bearish_signals > bullish_signals and bearish_signals >= 3:
            htf_bias = "BEARISH"
        else:
            htf_bias = "NEUTRAL"

        raw_score = max(bullish_signals, bearish_signals)
        if raw_score >= 5:   confidence = "HIGH"
        elif raw_score >= 3: confidence = "MEDIUM"
        else:                confidence = "LOW"

        # ── Execution Plan ─────────────────────────────────────────────
        current_price = df_ltf["close"].values[-1]
        atr           = ltf_displacement["atr"]

        MAX_SL_PCT = 0.03    # 3% max SL (intraday)
        MIN_RR     = 2.0

        if htf_bias == "BULLISH" and not gc_dc_block_long:
            # Entry: OB zone jika tersedia, else market order
            if ltf_ob["has_bull_setup"] and ltf_ob["nearest_bull"]:
                ob    = ltf_ob["nearest_bull"]
                entry = ob["ob_mid"]            # Limit order di midpoint OB
                order_type = "LIMIT (OB)"
            else:
                entry = current_price           # Market order
                order_type = "MARKET"
            sl_atr = entry - (atr * 1.5)
            sl_cap = entry * (1 - MAX_SL_PCT)
            sl     = max(sl_atr, sl_cap)
            # SL di bawah OB low jika ada OB entry
            if ltf_ob["has_bull_setup"] and ltf_ob["nearest_bull"]:
                sl = min(sl, ltf_ob["nearest_bull"]["ob_low"] - atr * 0.3)
            # Re-enforce SL cap setelah OB override
            sl     = max(sl, entry * (1 - MAX_SL_PCT))
            risk   = entry - sl
            if risk <= 0: risk = entry * MAX_SL_PCT   # safety
            sl_pct = round(risk / entry * 100, 2)
            min_rr_actual = MIN_RR + (0.5 if htf_gc_dc["bull_bias"] else 0)
            tp     = self.calc_fibonacci_tp(entry, sl, "LONG")
            rr_val = (tp["TP1"] - entry) / risk if risk > 0 else 0
            mode   = f"LONG ({order_type})" if rr_val >= min_rr_actual else "NO TRADE"

        elif htf_bias == "BEARISH" and not gc_dc_block_short:
            if ltf_ob["has_bear_setup"] and ltf_ob["nearest_bear"]:
                ob    = ltf_ob["nearest_bear"]
                entry = ob["ob_mid"]
                order_type = "LIMIT (OB)"
            else:
                entry = current_price
                order_type = "MARKET"
            sl_atr = entry + (atr * 1.5)
            sl_cap = entry * (1 + MAX_SL_PCT)
            sl     = min(sl_atr, sl_cap)
            if ltf_ob["has_bear_setup"] and ltf_ob["nearest_bear"]:
                sl = max(sl, ltf_ob["nearest_bear"]["ob_high"] + atr * 0.3)
            # Re-enforce SL cap setelah OB override
            sl     = min(sl, entry * (1 + MAX_SL_PCT))
            risk   = sl - entry
            if risk <= 0: risk = entry * MAX_SL_PCT
            sl_pct = round(risk / entry * 100, 2)
            min_rr_actual = MIN_RR + (0.5 if htf_gc_dc["bear_bias"] else 0)
            tp     = self.calc_fibonacci_tp(entry, sl, "SHORT")
            rr_val = (entry - tp["TP1"]) / risk if risk > 0 else 0
            mode   = f"SHORT ({order_type})" if rr_val >= min_rr_actual else "NO TRADE"

        elif htf_bias in ("BULLISH","BEARISH"):
            # GC/DC blocked counter-trend entry
            entry  = current_price; sl = 0; sl_pct = 0; rr_val = 0
            tp     = {"TP1":0,"TP2":0,"TP3":0,"TP4_MOON":0}
            mode   = "BLOCKED (GC/DC filter)"
            order_type = "NONE"

        else:
            entry      = current_price
            sl         = 0; sl_pct = 0; rr_val = 0
            tp         = {"TP1":0,"TP2":0,"TP3":0,"TP4_MOON":0}
            mode       = "NO TRADE"
            order_type = "NONE"

        return {
            "htf": {
                "timeframe":  htf,
                "structure":  htf_structure,
                "wyckoff":    htf_wyckoff,
                "liquidity":  htf_liquidity,
                "fvg":        htf_fvg,
                "ob":         htf_ob,
                "gc_dc":      htf_gc_dc,
                "ema":        htf_ema,
                "elliott":    htf_elliott,
                "bias":       htf_bias,
                "bull_score": bullish_signals,
                "bear_score": bearish_signals,
            },
            "mtf": {
                "timeframe":  mtf,
                "structure":  mtf_structure,
                "fvg":        mtf_fvg,
                "liquidity":  mtf_liquidity,
                "bull":       mtf_bull,
                "bear":       mtf_bear,
            },
            "ltf": {
                "timeframe":    ltf,
                "structure":    ltf_structure,
                "displacement": ltf_displacement,
                "fvg":          ltf_fvg,
                "ob":           ltf_ob,
                "liquidity":    ltf_liquidity,
                "bull_confirm": has_bull_ltf_confirm,
                "bear_confirm": has_bear_ltf_confirm,
                "ltf_bull":     ltf_bull,
                "ltf_bear":     ltf_bear,
            },
            "regime": regime,
            "execution": {
                "bias":       htf_bias,
                "confidence": confidence,
                "entry_mode": mode,
                "entry":      entry,
                "sl":         sl,
                "sl_pct":     sl_pct,
                "tp":         tp,
                "rr":         f"1:{rr_val:.2f}" if rr_val > 0 else "N/A",
                "order_type": locals().get("order_type","MARKET"),
                "rr_valid":   rr_val >= MIN_RR,
            },
        }
