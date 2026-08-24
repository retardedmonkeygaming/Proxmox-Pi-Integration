import json
import os

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "pve_ip": "",
    "pve_node": "pve",
    "pve_user": "root@pam",
    "pve_password": "",
    "log_interval": 10,  # seconds
    "dht_interval": 5,   # seconds
    "temp_unit": "C"
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
    
    config["pve_ip"] = input("Enter Proxmox IP/Hostname (e.g., 192.168.1.100): ").strip()
    node = input("Enter Proxmox Node Name [default: pve]: ").strip()
    if node:
        config["pve_node"] = node
        
    user = input("Enter Proxmox User [default: root@pam]: ").strip()
    if user:
        config["pve_user"] = user
        
    config["pve_password"] = input("Enter Proxmox Password: ").strip()
    
    while True:
        try:
            interval = int(input("Enter Server Log Interval in seconds [min: 5, default: 10]: ").strip() or 10)
            if interval >= 5:
                config["log_interval"] = interval
                break
            print("Interval must be at least 5 seconds.")
        except ValueError:
            print("Please enter a valid integer.")

    save_config(config)
    print("\n[✓] Configuration saved successfully to config.json!\n")
    return config