import json
import os

CONFIG_FILE = 'config.json'
DEFAULT_CONFIG = {
    "api_token": "",
    "app_id": "62845",
    "symbol": "R_50",
    "strategy": "1",
    "trade_pc": 1.0,
    "fetch_days": 730
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                # Ensure all default keys exist
                for key, value in DEFAULT_CONFIG.items():
                    if key not in config:
                        config[key] = value
                return config
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)
