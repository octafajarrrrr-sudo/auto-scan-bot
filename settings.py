"""
SETTINGS — Persistent bot configuration (no script editing needed)
Disimpan di settings.json, diubah via Telegram commands.
"""
import json, os

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

DEFAULTS = {
    "min_mcap_usd":    50_000_000,
    "min_score":       9,
    "scan_interval_h": 4,
    "top_n_signals":   2,
    "tf_bias":         "4h",
    "tf_structure":    "1h",
    "tf_entry":        "15m",
    "tf_regime":       "1d",
    "max_results":     100,
    "mode":            "intraday",
}

# In-memory cache — hindari baca file JSON setiap panggilan get()
_cache: dict | None = None

def load() -> dict:
    global _cache
    if _cache is not None:
        return _cache.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                saved = json.load(f)
            _cache = {**DEFAULTS, **saved}
            return _cache.copy()
        except Exception:
            pass
    _cache = DEFAULTS.copy()
    return _cache.copy()

def save(cfg: dict):
    global _cache
    _cache = {**DEFAULTS, **cfg}
    with open(SETTINGS_FILE, "w") as f:
        json.dump(_cache, f, indent=2)

def get(key: str):
    return load().get(key, DEFAULTS.get(key))

def set_val(key: str, value):
    cfg = load()
    cfg[key] = value
    save(cfg)
    return cfg

def reset():
    global _cache
    _cache = DEFAULTS.copy()
    save(_cache.copy())
    return _cache.copy()

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
