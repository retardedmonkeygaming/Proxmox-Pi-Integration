import json
import os
from typing import Dict, Any

CONFIG_FILE = "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "pve_ip": "",
    "pve_node": "pve",
    "pve_user": "root@pam",
    "pve_password": "",
    "log_interval": 10,
    "dht_interval": 5,
    "hold_time": 0.5,
    "multi_tap_window": 0.45,
    "buzzer_enabled": True,
}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        return run_setup_wizard()
    with open(CONFIG_FILE, "r") as f:
        cfg = json.load(f)
    # Merge any missing keys from defaults
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
    return cfg

def save_config(config_data: Dict[str, Any]) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=4)

def run_setup_wizard() -> Dict[str, Any]:
    print("=" * 50)
    print("      PVE NODE MONITOR - FIRST BOOT SETUP     ")
    print("=" * 50)

    config = DEFAULT_CONFIG.copy()
    config["pve_ip"] = input("Enter Proxmox IP/Hostname: ").strip()

    node = input("Enter Proxmox Node Name [default: pve]: ").strip()
    if node:
        config["pve_node"] = node

    user = input("Enter Proxmox User [default: root@pam]: ").strip()
    if user:
        config["pve_user"] = user

    config["pve_password"] = input("Enter Proxmox Password: ").strip()

    print("\n--- Touch Sensor Threshold Setup ---")
    try:
        hold = float(input("Hold duration (seconds) [default: 0.5]: ").strip() or 0.5)
        config["hold_time"] = hold
    except ValueError:
        pass

    try:
        tap_win = float(input("Multi-tap window (seconds) [default: 0.45]: ").strip() or 0.45)
        config["multi_tap_window"] = tap_win
    except ValueError:
        pass

    save_config(config)
    print("\n[✓] Setup complete! Saved to config.json\n")
    return config