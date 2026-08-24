import time
import threading
import config
import database
from hardware import HardwareManager
from monitor import ProxmoxMonitor

def main():
    cfg = config.load_config()
    database.init_db()
    
    hw = HardwareManager()
    pve = ProxmoxMonitor(cfg["pve_ip"], cfg["pve_node"], cfg["pve_user"], cfg["pve_password"])

    current_page = 0
    total_pages = 3
    interval_options = [5, 10, 30, 60]
    
    latest_metrics = {"cpu": "N/A", "ram_used": 0, "ram_total": 0}
    latest_env = {"temp": "N/A", "hum": "N/A"}

    # Background worker for Proxmox API logging
    def pve_logger_thread():
        while True:
            stats = pve.get_stats()
            if stats:
                latest_metrics.update(stats)
                database.log_server_metrics(stats["cpu"], stats["ram_used"], stats["ram_total"])
            time.sleep(cfg["log_interval"])

    # Background worker for DHT11 Environment logging
    def env_logger_thread():
        while True:
            t, h = hw.read_dht()
            if t is not None:
                latest_env["temp"], latest_env["hum"] = t, h
                database.log_env_metrics(t, h)
            time.sleep(cfg["dht_interval"])

    # Start threads
    t1 = threading.Thread(target=pve_logger_thread, daemon=True)
    t2 = threading.Thread(target=env_logger_thread, daemon=True)
    t1.start()
    t2.start()

    hw.display_text("System Ready", "Monitoring T5500")
    time.sleep(1.5)

    try:
        while True:
            touch = hw.read_touch_event()
            
            if touch == 'SHORT':
                current_page = (current_page + 1) % total_pages
            elif touch == 'LONG' and current_page == 2:
                # Cycle log interval on long press
                curr_idx = interval_options.index(cfg["log_interval"]) if cfg["log_interval"] in interval_options else 0
                new_interval = interval_options[(curr_idx + 1) % len(interval_options)]
                cfg["log_interval"] = new_interval
                config.save_config(cfg)
                hw.display_text("Saved Interval:", f"{new_interval} Seconds")
                time.sleep(1.2)

            # Render Screen Pages
            if current_page == 0:
                # Page 1: Server Metrics
                line1 = f"T5500 CPU:{latest_metrics['cpu']}%"
                line2 = f"RAM:{latest_metrics['ram_used']}/{latest_metrics['ram_total']}GB"
                hw.display_text(line1, line2)
                
            elif current_page == 1:
                # Page 2: Ambient Desk Environment
                line1 = f"Desk Temp:{latest_env['temp']}C"
                line2 = f"Humidity :{latest_env['hum']}%"
                hw.display_text(line1, line2)
                
            elif current_page == 2:
                # Page 3: Live Settings Menu
                line1 = f"Interval: {cfg['log_interval']}s"
                line2 = "[Hold to Change]"
                hw.display_text(line1, line2)

            time.sleep(0.15)

    except KeyboardInterrupt:
        hw.cleanup()

if __name__ == "__main__":
    main()