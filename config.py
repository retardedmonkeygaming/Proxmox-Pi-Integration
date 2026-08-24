import json
import os
from typing import Any, Dict

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
    "log_level": "INFO",
}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        return run_setup_wizard()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
    return cfg

def save_config(cfg: Dict[str, Any]) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)

def run_setup_wizard() -> Dict[str, Any]:
    print("=" * 52)
    print("   PVE Node Monitor – First Boot Setup")
    print("=" * 52)
    cfg = DEFAULT_CONFIG.copy()
    cfg["pve_ip"] = input("Proxmox IP / Hostname: ").strip()
    node = input("Node name [pve]: ").strip()
    if node:
        cfg["pve_node"] = node
    user = input("User [root@pam]: ").strip()
    if user:
        cfg["pve_user"] = user
    cfg["pve_password"] = input("Password: ").strip()
    save_config(cfg)
    print("\n[✓] config.json written\n")
    return cfg