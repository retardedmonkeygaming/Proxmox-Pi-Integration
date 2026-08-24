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
    "type": "server",
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "nodes": [],
    "log_interval": 10,
    "dht_interval": 30,
    "hold_time": 0.5,
    "multi_tap_window": 0.45,
    "buzzer_enabled": True,
    "passive_buzzer_enabled": True,
    "quiet_mode": False,
    "compact_cards": False,
    "flash_hostname": True,
    "flash_interval": 10,
    "flash_duration": 2.2,
    "default_node_idx": 0,
    "log_level": "INFO",
    "setup_done": False,
    "pins_done": False,
    "standalone": False,
    "cpu_alert": 85,
    "disk_alert": 90,
    "ram_alert": 90,
    "theme": "dark",
    "graph_order": ["cpu", "ram", "net"],
    "graph_visible": {"cpu": True, "ram": True, "net": True, "disk": False},
    "auto_refresh": 5,
    # components installed
    "has_lcd": True,
    "has_touch": True,
    "has_active_buzzer": True,
    "has_passive_buzzer": True,
    "has_dht": True,
    # GPIO (BCM)
    "gpio_touch": 27,
    "gpio_active_buzzer": 6,
    "gpio_passive_buzzer": 16,
    "gpio_dht": 4,
    "lcd_mode": "parallel",   # "parallel" | "i2c"
    "lcd_rs": 22,
    "lcd_en": 17,
    "lcd_d4": 25,
    "lcd_d5": 24,
    "lcd_d6": 23,
    "lcd_d7": 18,
    "lcd_i2c_addr": "0x27",
}

def load_config() -> Dict[str, Any]:
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if "pve_ip" in cfg and "nodes" not in cfg:
        cfg["nodes"] = [{
            "name": cfg.get("pve_node", "pve"),
            "ip": cfg.get("pve_ip", ""),
            "node": cfg.get("pve_node", "pve"),
            "user": cfg.get("pve_user", "root@pam"),
            "password": cfg.get("pve_password", ""),
            "type": "server",
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
        n["type"] = "server"
        nodes.append(n)
        if input("Add another? [y/N]: ").strip().lower() != "y":
            break
    cfg["nodes"] = nodes
    cfg["setup_done"] = True
    cfg["pins_done"] = True
    cfg["standalone"] = True
    save_config(cfg)
    print("\n[✓] Saved (standalone).\n")
    return cfg
