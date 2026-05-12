"""
RISK MANAGER — Institutional Grade Position Sizing & Protection
- Dynamic Position Sizing (Risk 1% per trade)
- Sentiment Multiplier
- Circuit Breakers (Daily Loss & Consecutive Losses)
- ATR-based Unidirectional Trailing Stop
"""
import math
import json
import os
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), "risk_state.json")

# ── Global State for Circuit Breaker ──────────────────────────────
class RiskState:
    daily_equity_start = 1000.0  # Modal simulasi awal hari
    current_equity = 1000.0
    consecutive_losses = 0
    stop_trading_until = None
    stop_trading_global = False

state = RiskState()

def _save_state():
    """Persist state ke disk agar survive restart."""
    data = {
        "daily_equity_start": state.daily_equity_start,
        "current_equity": state.current_equity,
        "consecutive_losses": state.consecutive_losses,
        "stop_trading_until": state.stop_trading_until.isoformat() if state.stop_trading_until else None,
        "stop_trading_global": state.stop_trading_global,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def _load_state():
    """Load state dari disk saat startup."""
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        state.daily_equity_start = data.get("daily_equity_start", 1000.0)
        state.current_equity = data.get("current_equity", 1000.0)
        state.consecutive_losses = data.get("consecutive_losses", 0)
        state.stop_trading_global = data.get("stop_trading_global", False)
        until = data.get("stop_trading_until")
        state.stop_trading_until = datetime.fromisoformat(until) if until else None
        logger.info(f"Risk state loaded: equity=${state.current_equity}, losses={state.consecutive_losses}")
    except Exception as e:
        logger.warning(f"Failed to load risk state: {e}")

_load_state()

# ── 1. Position Sizing ──────────────────────────────────────────
def calculate_position_size(balance: float, entry_price: float, sl_price: float, 
                            sent_score: float = 50.0) -> dict:
    """
    Menghitung ukuran posisi berdasarkan risiko 1% dari balance.
    Serta menerapkan multiplier berdasarkan sentiment score.
    """
    if state.stop_trading_global:
        return {"approved": False, "reason": "Global Circuit Breaker AKTIF (Loss Harian > 5%)"}
    
    if state.stop_trading_until and datetime.now(timezone.utc) < state.stop_trading_until:
        return {"approved": False, "reason": "Cooling Period AKTIF (3x Loss Beruntun)"}

    # Hitung Sentimen Factor (0.7 hingga 1.2)
    # 50 = netral = 0.95 factor
    # 100 = bullish = 1.2 factor
    # 0 = bearish = 0.7 factor
    if sent_score > 90 or sent_score < 10:
        return {"approved": False, "reason": f"Extreme Sentiment Score ({sent_score}). Crowded trade dihindari."}

    sent_factor = 0.7 + (sent_score / 200)
    
    # Base risk: 1% dari modal
    base_risk_usd = balance * 0.01
    
    # Hitung SL distance
    sl_distance_pct = abs(entry_price - sl_price) / entry_price
    if sl_distance_pct <= 0:
        return {"approved": False, "reason": "SL distance invalid"}
        
    # Position size dalam USD
    position_usd = base_risk_usd / sl_distance_pct
    
    # Terapkan sentiment factor ke jumlah posisi
    adjusted_position_usd = position_usd * sent_factor
    
    # Hitung quantity koin
    quantity = adjusted_position_usd / entry_price
    
    # Safety Check: Jangan biarkan ukuran posisi melebihi leverage maksimum wajar (misal 20x dari balance)
    if adjusted_position_usd > (balance * 20):
        return {"approved": False, "reason": "Position size menuntut leverage melebihi 20x"}

    return {
        "approved": True,
        "base_risk_usd": round(base_risk_usd, 2),
        "sl_distance_pct": round(sl_distance_pct * 100, 2),
        "position_usd": round(adjusted_position_usd, 2),
        "quantity": quantity,
        "sent_factor": round(sent_factor, 2)
    }

# ── 2. Circuit Breaker ──────────────────────────────────────────
def update_circuit_breaker(trade_pnl_usd: float):
    """
    Dipanggil setiap kali sebuah trade ditutup.
    """
    state.current_equity += trade_pnl_usd
    
    daily_drawdown = (state.current_equity - state.daily_equity_start) / state.daily_equity_start * 100
    
    if trade_pnl_usd < 0:
        state.consecutive_losses += 1
    else:
        state.consecutive_losses = 0
        
    # Aturan 1: Daily Loss > 5% -> Hentikan aktivitas hari ini
    if daily_drawdown <= -5.0:
        logger.warning("🚨 CIRCUIT BREAKER TRIPPED! Daily Loss > 5%. Trading dihentikan.")
        state.stop_trading_global = True
        
    # Aturan 2: 3x Loss beruntun -> Rehat 1 jam
    elif state.consecutive_losses >= 3:
        state.stop_trading_until = datetime.now(timezone.utc) + timedelta(hours=1)
        logger.warning("🚨 3x CONSECUTIVE LOSSES! Cooling down selama 1 jam.")
        state.consecutive_losses = 0
    
    _save_state()  # Persist setiap perubahan

def reset_daily_equity():
    """Dijalankan setiap jam 00:00 UTC melalui scheduler."""
    state.daily_equity_start = state.current_equity
    state.stop_trading_global = False
    _save_state()
    logger.info(f"Daily equity di-reset ke ${state.current_equity}. Global Stop Trading dicabut.")

# ── 3. Trailing Stop ────────────────────────────────────────────
def calculate_trailing_stop(is_long: bool, current_sl: float, highest_since_entry: float, 
                            lowest_since_entry: float, atr: float) -> float:
    """
    Trailing stop berbasis ATR yang hanya bergerak ke arah profit (Unidirectional).
    LONG: SL baru = highest - (1.5 * ATR), jika lebih besar dari SL lama.
    SHORT: SL baru = lowest + (1.5 * ATR), jika lebih kecil dari SL lama.
    """
    if is_long:
        proposed_sl = highest_since_entry - (1.5 * atr)
        return max(current_sl, proposed_sl)  # Hanya maju
    else:
        proposed_sl = lowest_since_entry + (1.5 * atr)
        return min(current_sl, proposed_sl)  # Hanya maju (turun)
