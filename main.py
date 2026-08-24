import time
import threading
import config
import database
from hardware import HardwareManager
from monitor import ProxmoxMonitor

def main():
    cfg = config.load_config()
    database.init_db()
    
    hw = HardwareManager(hold_time=cfg.get("hold_time", 0.5), multi_tap_window=cfg.get("multi_tap_window", 0.45))
    pve = ProxmoxMonitor(cfg["pve_ip"], cfg["pve_node"], cfg["pve_user"], cfg["pve_password"])

    metrics = {"node": cfg["pve_node"], "cpu": 0, "ram_used": 0, "ram_total": 0, "disk_pct": 0, "active_vms": 0}
    env = {"temp": "N/A", "hum": "N/A"}

    # Background threads
    def pve_logger():
        while True:
            s = pve.get_stats()
            if s:
                metrics.update(s)
                database.log_server_metrics(s["cpu"], s["ram_used"], s["ram_total"])
            time.sleep(cfg["log_interval"])

    def env_logger():
        while True:
            t, h = hw.read_dht()
            if t is not None:
                env["temp"], env["hum"] = t, h
                database.log_env_metrics(t, h)
            time.sleep(cfg["dht_interval"])

    threading.Thread(target=pve_logger, daemon=True).start()
    threading.Thread(target=env_logger, daemon=True).start()

    in_settings = False
    current_page = 0
    total_pages = 3
    
    settings_idx = 0
    interval_opts = [5, 10, 30, 60]

    last_hostname_flash = time.time()
    flash_active = False

    try:
        while True:
            gesture = hw.read_touch_gesture()

            # --- MODE TRANSITIONS & GESTURE RULES ---
            if gesture == 'DOUBLE' and not in_settings:
                in_settings = True
                settings_idx = 0
                hw.beep(0.08)

            elif gesture == 'TRIPLE' and in_settings:
                in_settings = False
                hw.beep(0.08)

            elif in_settings:
                if gesture == 'SINGLE':
                    settings_idx = (settings_idx + 1) % 2  # 2 configurable settings
                elif gesture == 'HOLD':
                    if settings_idx == 0:
                        curr = interval_opts.index(cfg["log_interval"]) if cfg["log_interval"] in interval_opts else 0
                        cfg["log_interval"] = interval_opts[(curr + 1) % len(interval_opts)]
                    elif settings_idx == 1:
                        cfg["dht_interval"] = 10 if cfg["dht_interval"] == 5 else 5
                    config.save_config(cfg)

            elif not in_settings and gesture == 'SINGLE':
                current_page = (current_page + 1) % total_pages

            # --- SCREEN RENDERING ---
            if in_settings:
                if settings_idx == 0:
                    hw.display_text(f"SET: Log Interval", f"> {cfg['log_interval']}s (HoldChg)")
                elif settings_idx == 1:
                    hw.display_text(f"SET: DHT Read", f"> {cfg['dht_interval']}s (HoldChg)")

            else:
                # Handle Hostname Flash every 10 seconds on Page 1
                if time.time() - last_hostname_flash >= 10:
                    flash_active = True
                    last_hostname_flash = time.time()

                if flash_active and (time.time() - last_hostname_flash <= 2):
                    # Show node hostname briefly for 2 seconds
                    hw.display_text("NODE HOSTNAME:", f"[{metrics['node'].center(14)}]")
                else:
                    flash_active = False
                    if current_page == 0:
                        # Page 1: CPU & RAM
                        hw.display_text(f"CPU:{metrics['cpu']}%", f"RAM:{metrics['ram_used']}/{metrics['ram_total']}G")
                    elif current_page == 1:
                        # Page 2: Disk + Ambient Temp
                        hw.display_text(f"Disk:{metrics['disk_pct']}% Used", f"Amb Temp:{env['temp']}C")
                    elif current_page == 2:
                        # Page 3: Active VMs / Containers
                        hw.display_text("Proxmox Guests:", f"Active VMs: {metrics['active_vms']}")

            time.sleep(0.05)

    except KeyboardInterrupt:
        hw.cleanup()

if __name__ == "__main__":
    main()