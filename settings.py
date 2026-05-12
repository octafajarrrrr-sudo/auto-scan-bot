"""
SETTINGS — Persistent bot configuration (no script editing needed)
Disimpan di settings.json, diubah via Telegram commands.
"""
import json, os

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

DEFAULTS = {
    "min_mcap_usd":    50_000_000,   # $50M minimum market cap untuk scan
    "min_score":       9,            # minimum confluence score — matches HIGH_THRESH in analyzer.py
    "scan_interval_h": 4,            # auto-scan setiap N jam
    "top_n_signals":   2,            # kirim top N sinyal terkuat
    "tf_bias":         "4h",         # HTF — trend bias
    "tf_structure":    "1h",         # MTF — structure & zone
    "tf_entry":        "15m",        # LTF — entry trigger
    "tf_regime":       "1d",         # Regime context
    "max_results":     100,          # max koin dari Binance per scan
    "mode":            "intraday",   # intraday | swing
}

def load() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                saved = json.load(f)
            # Merge dengan defaults agar key baru selalu ada
            return {**DEFAULTS, **saved}
        except Exception:
            pass
    return DEFAULTS.copy()

def save(cfg: dict):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get(key: str):
    return load().get(key, DEFAULTS.get(key))

def set_val(key: str, value):
    cfg = load()
    cfg[key] = value
    save(cfg)
    return cfg

def reset():
    save(DEFAULTS.copy())
    return DEFAULTS.copy()

def summary() -> str:
    cfg = load()
    return (
        f"⚙️ *Bot Settings*\n\n"
        f"Mode         : `{cfg['mode']}`\n"
        f"Min MCap     : `${cfg['min_mcap_usd']/1e6:.0f}M`\n"
        f"Min Score    : `{cfg['min_score']}/{14}`\n"
        f"Top N Sinyal : `{cfg['top_n_signals']}`\n"
        f"Auto-Scan    : `setiap {cfg['scan_interval_h']} jam`\n"
        f"Timeframes   :\n"
        f"  Regime  → `{cfg['tf_regime']}`\n"
        f"  Bias    → `{cfg['tf_bias']}`\n"
        f"  Struktur→ `{cfg['tf_structure']}`\n"
        f"  Entry   → `{cfg['tf_entry']}`\n"
        f"Max Koin/Scan: `{cfg['max_results']}`"
    )
