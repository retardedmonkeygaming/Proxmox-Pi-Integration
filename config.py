import json
import os
from typing import Any, Dict, List

CONFIG_FILE = "config.json"

DEFAULT_NODE = {
    "name": "Precision",
    "ip": "",
    "node": "pve",
    "user": "root@pam",
    "password": "",
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "nodes": [],
    "log_interval": 10,
    "dht_interval": 30,
    "hold_time": 0.5,
    "multi_tap_window": 0.45,
    "buzzer_enabled": True,
    "flash_interval": 10,
    "flash_duration": 2.2,
    "default_node_idx": 0,
    "log_level": "INFO",
    "quiet_mode": False,
    "setup_done": False,
}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Migrate very old single-node format
    if "pve_ip" in cfg and "nodes" not in cfg:
        cfg["nodes"] = [{
            "name": cfg.get("pve_node", "pve"),
            "ip": cfg.get("pve_ip", ""),
            "node": cfg.get("pve_node", "pve"),
            "user": cfg.get("pve_user", "root@pam"),
            "password": cfg.get("pve_password", ""),
        }]
        for k in ("pve_ip", "pve_node", "pve_user", "pve_password"):
            cfg.pop(k, None)

    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
    return cfg

def save_config(cfg: Dict[str, Any]) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)

def run_terminal_wizard() -> Dict[str, Any]:
    print("=" * 56)
    print("   PVE Node Monitor – Terminal Setup")
    print("=" * 56)
    cfg = DEFAULT_CONFIG.copy()
    nodes: List[Dict] = []

    while True:
        print(f"\n--- Node / Server #{len(nodes)+1} ---")
        n = DEFAULT_NODE.copy()
        n["name"] = input("Friendly name [Precision]: ").strip() or "Precision"
        n["ip"] = input("IP / Hostname: ").strip()
        n["node"] = input(f"Proxmox node name [{n['name']}]: ").strip() or n["name"]
        n["user"] = input("User [root@pam]: ").strip() or "root@pam"
        n["password"] = input("Password: ").strip()
        nodes.append(n)
        if input("Add another? [y/N]: ").strip().lower() != "y":
            break

    cfg["nodes"] = nodes
    cfg["setup_done"] = True
    save_config(cfg)
    print("\n[✓] Saved.\n")
    return cfg