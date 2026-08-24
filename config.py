import json
import os

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "pve_ip": "",
    "pve_node": "pve",
    "pve_user": "root@pam",
    "pve_password": "",
    "log_interval": 10,
    "dht_interval": 5,
    # Touch Gestures Configuration
    "hold_time": 0.5,        # Hold threshold (seconds)
    "multi_tap_window": 0.45 # Time window to detect double/triple taps
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return run_setup_wizard()
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(config_data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_data, f, indent=4)

def run_setup_wizard():
    print("=" * 50)
    print("      T5500 SERVER MONITOR - FIRST BOOT SETUP     ")
    print("=" * 50)
    
    config = DEFAULT_CONFIG.copy()
    
    config["pve_ip"] = input("Enter Proxmox IP/Hostname: ").strip()
    node = input("Enter Proxmox Node Name [default: pve]: ").strip()
    if node: config["pve_node"] = node
        
    user = input("Enter Proxmox User [default: root@pam]: ").strip()
    if user: config["pve_user"] = user
        
    config["pve_password"] = input("Enter Proxmox Password: ").strip()
    
    # Custom Touch Settings Setup
    print("\n--- Touch Sensor Gesture Customization ---")
    try:
        hold = float(input("Hold duration for setting toggle in seconds [default: 0.5]: ").strip() or 0.5)
        config["hold_time"] = hold
    except ValueError:
        pass

    try:
        tap_win = float(input("Multi-tap detection window in seconds [default: 0.45]: ").strip() or 0.45)
        config["multi_tap_window"] = tap_win
    except ValueError:
        pass

    save_config(config)
    print("\n[✓] Setup completed!\n")
    return config