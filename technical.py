"""
TECHNICAL ENGINE — ICT/SMC + Wyckoff + FVG + Liquidity Sweep
Implements: Market Structure, FVG, Displacement, Liquidity Levels, Fibonacci TP
Based on: HL GOLD CORE Veracity Engine & OCR Precision Framework
"""
import numpy as np
import pandas as pd


class TechnicalEngine:
    """Multi-timeframe ICT/SMC technical analysis engine."""

    def __init__(self, exchange):
        self.exchange = exchange

    # ------------------------------------------------------------------ #
    #  DATA FETCHING
    # ------------------------------------------------------------------ #
    def fetch_ohlcv(self, symbol, timeframe="1h", limit=200):
        """Fetch OHLCV data dari Binance dan return DataFrame."""
        try:
            raw = self.exchange.fetch_ohlcv(f"{symbol}/USDT", timeframe, limit=limit)
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  MARKET STRUCTURE — BOS / CHoCH
    # ------------------------------------------------------------------ #
    def detect_structure(self, df):
        """Deteksi Market Structure: BOS (Break of Structure) dan CHoCH."""
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        n = len(df)

        swing_highs = []
        swing_lows = []

        # Identifikasi Swing High / Low (lookback 3)
        for i in range(3, n - 3):
            if highs[i] == max(highs[i - 3:i + 4]):
                swing_highs.append((i, highs[i]))
            if lows[i] == min(lows[i - 3:i + 4]):
                swing_lows.append((i, lows[i]))

        # Tentukan trend terakhir berdasarkan HH/HL atau LH/LL
        structure = "RANGING"
        last_bos = None

        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            sh1, sh2 = swing_highs[-2][1], swing_highs[-1][1]
            sl1, sl2 = swing_lows[-2][1], swing_lows[-1][1]

            if sh2 > sh1 and sl2 > sl1:
                structure = "BULLISH (HH + HL)"
            elif sh2 < sh1 and sl2 < sl1:
                structure = "BEARISH (LH + LL)"
            elif sh2 < sh1 and sl2 > sl1:
                structure = "CHoCH BULLISH (Potential Reversal Up)"
            elif sh2 > sh1 and sl2 < sl1:
                structure = "CHoCH BEARISH (Potential Reversal Down)"

            # BOS detection: apakah harga terakhir break swing high/low terakhir?
            current_price = closes[-1]
            if len(swing_highs) >= 1 and current_price > swing_highs[-1][1]:
                last_bos = f"BOS BULLISH (Break above {swing_highs[-1][1]:.5f})"
            elif len(swing_lows) >= 1 and current_price < swing_lows[-1][1]:
                last_bos = f"BOS BEARISH (Break below {swing_lows[-1][1]:.5f})"

        return {
            "structure": structure,
            "bos": last_bos,
            "swing_highs": swing_highs[-3:] if swing_highs else [],
            "swing_lows": swing_lows[-3:] if swing_lows else [],
        }

    # ------------------------------------------------------------------ #
    #  LIQUIDITY SWEEP — PDH/PDL, EQH/EQL
    # ------------------------------------------------------------------ #
    def detect_liquidity_sweep(self, df):
        """Deteksi apakah terjadi Liquidity Sweep (wick menembus level lalu kembali)."""
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        opens = df["open"].values

        # Previous Day High / Low (menggunakan 24 candle terakhir pada H1)
        pdh = max(highs[-48:-24]) if len(highs) >= 48 else max(highs[:len(highs)//2])
        pdl = min(lows[-48:-24]) if len(lows) >= 48 else min(lows[:len(lows)//2])

        sweeps = []

        # Cek 10 candle terakhir untuk sweep
        for i in range(-10, 0):
            idx = len(highs) + i
            if idx < 0:
                continue
            # Sweep High: wick tembus tapi close kembali di bawah
            if highs[idx] > pdh and closes[idx] < pdh:
                sweeps.append(f"🔻 SWEEP HIGH at {highs[idx]:.5f} (PDH: {pdh:.5f}) → Bearish Signal")
            # Sweep Low: wick tembus tapi close kembali di atas
            if lows[idx] < pdl and closes[idx] > pdl:
                sweeps.append(f"🔺 SWEEP LOW at {lows[idx]:.5f} (PDL: {pdl:.5f}) → Bullish Signal")

        # Equal Highs / Equal Lows (liquidity pool)
        eqh = None
        eql = None
        tolerance = (max(highs[-20:]) - min(lows[-20:])) * 0.002  # 0.2% tolerance

        recent_sh = []
        for i in range(3, min(50, len(highs) - 3)):
            idx = len(highs) - i
            if highs[idx] == max(highs[idx - 2:idx + 3]):
                recent_sh.append(highs[idx])

        for j in range(len(recent_sh)):
            for k in range(j + 1, len(recent_sh)):
                if abs(recent_sh[j] - recent_sh[k]) < tolerance:
                    eqh = (recent_sh[j] + recent_sh[k]) / 2
                    break

        # Equal Lows detection (mirror dari EQH)
        recent_sl = []
        for i in range(3, min(50, len(lows) - 3)):
            idx = len(lows) - i
            if lows[idx] == min(lows[idx - 2:idx + 3]):
                recent_sl.append(lows[idx])

        for j in range(len(recent_sl)):
            for k in range(j + 1, len(recent_sl)):
                if abs(recent_sl[j] - recent_sl[k]) < tolerance:
                    eql = (recent_sl[j] + recent_sl[k]) / 2
                    break

        return {
            "pdh": pdh,
            "pdl": pdl,
            "sweeps": sweeps if sweeps else ["Belum ada sweep terdeteksi"],
            "eqh": eqh,
            "eql": eql,
        }

    # ------------------------------------------------------------------ #
    #  FVG (FAIR VALUE GAP) — Imbalance Detection
    # ------------------------------------------------------------------ #
    def detect_fvg(self, df):
        """Deteksi Fair Value Gap (FVG) / Imbalance pada 50 candle terakhir."""
        highs = df["high"].values
        lows = df["low"].values
        fvg_bullish = []
        fvg_bearish = []

        start = max(2, len(highs) - 50)
        for i in range(start, len(highs)):
            # Bullish FVG: low[i] > high[i-2] (gap antara candle i dan i-2)
            if lows[i] > highs[i - 2]:
                fvg_bullish.append({
                    "top": lows[i],
                    "bottom": highs[i - 2],
                    "idx": i
                })
            # Bearish FVG: high[i] < low[i-2]
            if highs[i] < lows[i - 2]:
                fvg_bearish.append({
                    "top": lows[i - 2],
                    "bottom": highs[i],
                    "idx": i
                })

        # Ambil FVG terbaru (terdekat dengan harga sekarang)
        latest_bull_fvg = fvg_bullish[-1] if fvg_bullish else None
        latest_bear_fvg = fvg_bearish[-1] if fvg_bearish else None

        return {
            "bullish_fvg": latest_bull_fvg,
            "bearish_fvg": latest_bear_fvg,
            "total_bullish": len(fvg_bullish),
            "total_bearish": len(fvg_bearish),
        }

    # ------------------------------------------------------------------ #
    #  DISPLACEMENT — Momentum Candle Detection
    # ------------------------------------------------------------------ #
    def detect_displacement(self, df):
        """Deteksi candle displacement (candle besar yang menandakan momentum institusional)."""
        closes = df["close"].values
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values

        # Hitung Average True Range sebagai baseline
        atr_values = []
        for i in range(1, len(highs)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            atr_values.append(tr)
        atr = np.mean(atr_values[-14:]) if len(atr_values) >= 14 else np.mean(atr_values)

        # Displacement = body candle > 1.5x ATR
        displacements = []
        for i in range(-5, 0):
            idx = len(closes) + i
            if idx < 0:
                continue
            body = abs(closes[idx] - opens[idx])
            if body > atr * 1.5:
                direction = "BULLISH" if closes[idx] > opens[idx] else "BEARISH"
                displacements.append(f"{direction} Displacement (Body: {body:.5f}, ATR: {atr:.5f})")

        return {
            "displacements": displacements if displacements else ["Tidak ada displacement baru"],
            "atr": atr,
        }

    # ------------------------------------------------------------------ #
    #  WYCKOFF PHASE — Accumulation / Distribution
    # ------------------------------------------------------------------ #
    def detect_wyckoff_phase(self, df):
        """Estimasi fase Wyckoff berdasarkan price range dan volume."""
        closes = df["close"].values
        volumes = df["volume"].values
        highs = df["high"].values
        lows = df["low"].values

        # Range 50 candle terakhir
        price_range = max(highs[-50:]) - min(lows[-50:])
        recent_range = max(highs[-10:]) - min(lows[-10:])
        range_ratio = recent_range / price_range if price_range > 0 else 0

        # Volume trend
        avg_vol_old = np.mean(volumes[-50:-25]) if len(volumes) >= 50 else np.mean(volumes)
        avg_vol_new = np.mean(volumes[-25:])
        vol_expanding = avg_vol_new > avg_vol_old * 1.2

        # Phase detection
        current = closes[-1]
        range_low = min(lows[-50:])
        range_high = max(highs[-50:])
        position_in_range = (current - range_low) / (range_high - range_low) if range_high != range_low else 0.5

        phase = "RANGING"
        if range_ratio < 0.3 and position_in_range < 0.3:
            phase = "ACCUMULATION (Spring Zone)"
        elif range_ratio < 0.3 and position_in_range > 0.7:
            phase = "DISTRIBUTION (UTAD Zone)"
        elif vol_expanding and position_in_range > 0.5:
            phase = "MARKUP (Trending Up)"
        elif vol_expanding and position_in_range < 0.5:
            phase = "MARKDOWN (Trending Down)"

        return {
            "phase": phase,
            "position_in_range": f"{position_in_range * 100:.0f}%",
            "vol_expanding": vol_expanding,
        }

    # ------------------------------------------------------------------ #
    #  FIBONACCI — TP Levels
    # ------------------------------------------------------------------ #
    def calc_fibonacci_tp(self, entry, sl, direction="LONG"):
        """Hitung TP berdasarkan Fibonacci Extension (1.618, 2.618, 4.236)."""
        risk = abs(entry - sl)
        if direction == "LONG":
            return {
                "TP1": round(entry + risk * 1.0, 6),       # 1R
                "TP2": round(entry + risk * 1.618, 6),     # 1.618 Fibo
                "TP3": round(entry + risk * 2.618, 6),     # 2.618 Fibo
                "TP4_MOON": round(entry + risk * 4.236, 6) # 4.236 Fibo (Full Extension)
            }
        else:
            return {
                "TP1": round(entry - risk * 1.0, 6),
                "TP2": round(entry - risk * 1.618, 6),
                "TP3": round(entry - risk * 2.618, 6),
                "TP4_MOON": round(entry - risk * 4.236, 6)
            }

    # ------------------------------------------------------------------ #
    #  EMA — Trend Filter
    # ------------------------------------------------------------------ #
    def calc_ema(self, df, period=20):
        """Hitung EMA sebagai trend bias tambahan."""
        closes = df["close"].values
        ema = pd.Series(closes).ewm(span=period, adjust=False).mean().values
        current = closes[-1]
        ema_val = ema[-1]
        return {
            "ema_value": ema_val,
            "price_vs_ema": "ABOVE" if current > ema_val else "BELOW",
            "bias": "Bullish" if current > ema_val else "Bearish"
        }

    # ------------------------------------------------------------------ #
    #  FULL ANALYSIS — Combine All
    # ------------------------------------------------------------------ #
    def full_analysis(self, symbol, htf="4h", ltf="15m"):
        """
        Jalankan analisis lengkap multi-timeframe.
        HTF (H4/D1) untuk bias, LTF (M15) untuk entry timing.
        """
        # Fetch data
        df_htf = self.fetch_ohlcv(symbol, htf, 200)
        df_ltf = self.fetch_ohlcv(symbol, ltf, 200)

        if df_htf is None or df_ltf is None:
            return None

        # HTF Analysis (Bias)
        htf_structure = self.detect_structure(df_htf)
        htf_wyckoff = self.detect_wyckoff_phase(df_htf)
        htf_liquidity = self.detect_liquidity_sweep(df_htf)
        htf_fvg = self.detect_fvg(df_htf)
        htf_ema = self.calc_ema(df_htf, 20)

        # LTF Analysis (Entry Timing)
        ltf_structure = self.detect_structure(df_ltf)
        ltf_displacement = self.detect_displacement(df_ltf)
        ltf_fvg = self.detect_fvg(df_ltf)
        ltf_liquidity = self.detect_liquidity_sweep(df_ltf)

        # Determine HTF Bias
        htf_bias = "NEUTRAL"
        bullish_signals = 0
        bearish_signals = 0

        if "BULLISH" in htf_structure["structure"]:
            bullish_signals += 1
        elif "BEARISH" in htf_structure["structure"]:
            bearish_signals += 1

        if htf_ema["bias"] == "Bullish":
            bullish_signals += 1
        else:
            bearish_signals += 1

        if "ACCUMULATION" in htf_wyckoff["phase"] or "MARKUP" in htf_wyckoff["phase"]:
            bullish_signals += 1
        elif "DISTRIBUTION" in htf_wyckoff["phase"] or "MARKDOWN" in htf_wyckoff["phase"]:
            bearish_signals += 1

        if bullish_signals >= 2:
            htf_bias = "BULLISH"
        elif bearish_signals >= 2:
            htf_bias = "BEARISH"

        # Determine Confidence
        confidence = "LOW"
        score = max(bullish_signals, bearish_signals)
        if score >= 3:
            confidence = "HIGH"
        elif score >= 2:
            confidence = "MEDIUM"

        # Entry & TP Calculation
        current_price = df_ltf["close"].values[-1]
        atr = ltf_displacement["atr"]

        if htf_bias == "BULLISH":
            entry = current_price
            sl = current_price - (atr * 2)
            tp_levels = self.calc_fibonacci_tp(entry, sl, "LONG")
            entry_mode = "LONG"
        elif htf_bias == "BEARISH":
            entry = current_price
            sl = current_price + (atr * 2)
            tp_levels = self.calc_fibonacci_tp(entry, sl, "SHORT")
            entry_mode = "SHORT"
        else:
            entry = current_price
            sl = 0
            tp_levels = {"TP1": 0, "TP2": 0, "TP3": 0, "TP4_MOON": 0}
            entry_mode = "NO TRADE"

        return {
            "htf": {
                "timeframe": htf,
                "structure": htf_structure,
                "wyckoff": htf_wyckoff,
                "liquidity": htf_liquidity,
                "fvg": htf_fvg,
                "ema": htf_ema,
                "bias": htf_bias,
            },
            "ltf": {
                "timeframe": ltf,
                "structure": ltf_structure,
                "displacement": ltf_displacement,
                "fvg": ltf_fvg,
                "liquidity": ltf_liquidity,
            },
            "execution": {
                "bias": htf_bias,
                "confidence": confidence,
                "entry_mode": entry_mode,
                "entry": entry,
                "sl": sl,
                "tp": tp_levels,
                "rr": f"1:{(abs(tp_levels['TP2'] - entry) / abs(entry - sl)):.1f}" if sl != 0 and entry != sl else "N/A",
            },
        }
