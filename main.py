import time
import threading
from typing import Any, Dict

import config
import database
from hardware import HardwareManager
from monitor import ProxmoxMonitor

def _fmt_rate(kbps: float) -> str:
    """Compact rate string that always fits."""
    if kbps >= 1024:
        return f"{kbps/1024:.1f}M"
    return f"{kbps:.0f}K"

def main() -> None:
    cfg = config.load_config()
    database.init_db()

    hw = HardwareManager(
        hold_time=cfg.get("hold_time", 0.5),
        multi_tap_window=cfg.get("multi_tap_window", 0.45),
        buzzer_enabled=cfg.get("buzzer_enabled", True),
    )
    pve = ProxmoxMonitor(cfg["pve_ip"], cfg["pve_node"], cfg["pve_user"], cfg["pve_password"])

    metrics: Dict[str, Any] = {
        "node": cfg["pve_node"],
        "cpu": 0.0,
        "ram_used": 0.0,
        "ram_total": 0.0,
        "disk_pct": 0.0,
        "active_vms": 0,
        "net_in": 0.0,
        "net_out": 0.0,
    }
    env: Dict[str, Any] = {"temp": None, "hum": None}

    stop_event = threading.Event()

    def pve_logger() -> None:
        while not stop_event.is_set():
            s = pve.get_stats()
            if s:
                metrics.update(s)
                database.log_server_metrics(
                    s["cpu"], s["ram_used"], s["ram_total"],
                    s["disk_pct"], s["net_in"], s["net_out"], s["active_vms"]
                )
            stop_event.wait(cfg["log_interval"])

    def env_logger() -> None:
        while not stop_event.is_set():
            t, h = hw.read_dht()
            if t is not None:
                env["temp"], env["hum"] = t, h
                database.log_env_metrics(t, h)
            stop_event.wait(cfg["dht_interval"])

    threading.Thread(target=pve_logger, daemon=True).start()
    threading.Thread(target=env_logger, daemon=True).start()

    in_settings = False
    current_page = 0
    total_pages = 4
    settings_idx = 0
    interval_opts = [5, 10, 30, 60]
    settings_timeout = 0.0

    last_hostname_flash = time.time()
    flash_active = False

    try:
        while True:
            gesture = hw.read_touch_gesture()
            now = time.time()

            # Auto-exit settings after 15 s inactivity
            if in_settings and now - settings_timeout > 15:
                in_settings = False

            # --- GESTURE HANDLING ---
            if gesture == "DOUBLE" and not in_settings:
                in_settings = True
                settings_idx = 0
                settings_timeout = now

            elif gesture == "TRIPLE" and in_settings:
                in_settings = False

            elif in_settings:
                settings_timeout = now
                if gesture == "SINGLE":
                    settings_idx = (settings_idx + 1) % 3
                elif gesture == "HOLD":
                    if settings_idx == 0:
                        curr = interval_opts.index(cfg["log_interval"]) if cfg["log_interval"] in interval_opts else 0
                        cfg["log_interval"] = interval_opts[(curr + 1) % len(interval_opts)]
                    elif settings_idx == 1:
                        cfg["dht_interval"] = 10 if cfg["dht_interval"] == 5 else 5
                    elif settings_idx == 2:
                        cfg["buzzer_enabled"] = not cfg.get("buzzer_enabled", True)
                        hw.buzzer_enabled = cfg["buzzer_enabled"]
                    config.save_config(cfg)
                    hw.beep(0.08)

            elif not in_settings and gesture == "SINGLE":
                current_page = (current_page + 1) % total_pages

            # --- RENDER ---
            if in_settings:
                if settings_idx == 0:
                    hw.display_text("SET: Log Interv", f"> {cfg['log_interval']}s  Hold")
                elif settings_idx == 1:
                    hw.display_text("SET: DHT Interv", f"> {cfg['dht_interval']}s  Hold")
                else:
                    buzz = "ON " if cfg.get("buzzer_enabled", True) else "OFF"
                    hw.display_text("SET: Buzzer", f"> {buzz}   Hold")
            else:
                # Hostname flash every 10 s for 2 s
                if now - last_hostname_flash >= 10:
                    flash_active = True
                    last_hostname_flash = now

                if flash_active and (now - last_hostname_flash <= 2):
                    node = metrics["node"][:14]
                    hw.display_text("NODE HOSTNAME:", f"[{node.center(14)}]")
                else:
                    flash_active = False
                    temp = env["temp"]
                    hum = env["hum"]
                    t_str = f"{temp:.0f}C" if temp is not None else "--C"
                    h_str = f"{hum:.0f}%" if hum is not None else "--%"

                    if current_page == 0:
                        # CPU + RAM
                        hw.display_text(
                            f"CPU:{metrics['cpu']:5.1f}%",
                            f"RAM:{metrics['ram_used']:.0f}/{metrics['ram_total']:.0f}G"
                        )
                    elif current_page == 1:
                        # Disk + Temp
                        hw.display_text(
                            f"Disk:{metrics['disk_pct']:5.1f}%",
                            f"Amb Temp:{t_str:>6}"
                        )
                    elif current_page == 2:
                        # Network (NEW)
                        up = _fmt_rate(metrics["net_out"])
                        dn = _fmt_rate(metrics["net_in"])
                        hw.display_text(
                            f"Net Up:{up:>8}",
                            f"Net Dn:{dn:>8}"
                        )
                    else:
                        # Guests + Humidity
                        hw.display_text(
                            f"Guests:{metrics['active_vms']:3d}",
                            f"Hum:{h_str:>6} {t_str}"
                        )

            time.sleep(0.04)

    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        hw.cleanup()

if __name__ == "__main__":
    main()