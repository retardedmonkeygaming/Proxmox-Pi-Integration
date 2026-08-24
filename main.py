import time
import threading
import uvicorn
from typing import Any, Dict

import config
import database
from hardware import HardwareManager
from monitor import ProxmoxMonitor
from logging_setup import setup_logging

# Web UI
try:
    from app import app as fastapi_app
except ImportError:
    fastapi_app = Noneg

def fmt_rate(kbps: float) -> str:
    if kbps >= 1024:
        return f"{kbps/1024:5.1f}M"
    return f"{kbps:5.0f}K"

# ... all previous imports and functions stay the same ...

def main() -> None:
    cfg = config.load_config()
    log = setup_logging(cfg.get("log_level", "INFO"))
    log.info("=== PVE Node Monitor starting ===")

    database.init_db()

    hw = HardwareManager(
        hold_time=cfg["hold_time"],
        multi_tap_window=cfg["multi_tap_window"],
        buzzer_enabled=cfg["buzzer_enabled"],
    )
    pve = ProxmoxMonitor(
        cfg["pve_ip"], cfg["pve_node"], cfg["pve_user"], cfg["pve_password"]
    )

    metrics = {
        "cpu": 0.0, "ram_used": 0.0, "ram_total": 0.0,
        "disk_pct": 0.0, "active_vms": 0, "net_in": 0.0, "net_out": 0.0,
    }
    env = {"temp": None, "hum": None}
    stop = threading.Event()

    def pve_worker():
        while not stop.is_set():
            s = pve.get_stats()
            if s:
                metrics.update(s)
                try:
                    database.log_server_metrics(
                        s["cpu"], s["ram_used"], s["ram_total"],
                        s["disk_pct"], s["net_in"], s["net_out"], s["active_vms"]
                    )
                except Exception as e:
                    log.error("DB server log failed: %s", e)
            else:
                log.warning("No metrics from Proxmox")
            stop.wait(cfg["log_interval"])

    def env_worker():
        while not stop.is_set():
            t, h = hw.read_dht()
            if t is not None:
                env["temp"], env["hum"] = t, h
                try:
                    database.log_env_metrics(t, h)
                except Exception as e:
                    log.error("DB env log failed: %s", e)
            stop.wait(cfg["dht_interval"])

    threading.Thread(target=pve_worker, daemon=True, name="pve").start()
    threading.Thread(target=env_worker, daemon=True, name="env").start()

    # === Web UI in the same process ===
    def run_web():
        uvicorn.run(
            fastapi_app,
            host="0.0.0.0",
            port=8000,
            log_level="warning",
            access_log=False,
        )

    threading.Thread(target=run_web, daemon=True, name="web").start()
    log.info("Web UI → http://0.0.0.0:8000  (or http://<pi-ip>:8000)")

    # LCD / gesture loop
    page = 0
    TOTAL = 4
    in_settings = False
    settings_idx = 0
    interval_opts = [5, 10, 30, 60]
    last_activity = time.time()

    try:
        while True:
            g = hw.read_gesture()
            now = time.time()

            if in_settings and now - last_activity > 12:
                in_settings = False

            if g == "DOUBLE" and not in_settings:
                in_settings = True
                settings_idx = 0
                last_activity = now
            elif g == "TRIPLE" and in_settings:
                in_settings = False
            elif in_settings:
                last_activity = now
                if g == "SINGLE":
                    settings_idx = (settings_idx + 1) % 3
                elif g == "HOLD":
                    if settings_idx == 0:
                        i = interval_opts.index(cfg["log_interval"]) if cfg["log_interval"] in interval_opts else 0
                        cfg["log_interval"] = interval_opts[(i + 1) % len(interval_opts)]
                    elif settings_idx == 1:
                        cfg["dht_interval"] = 10 if cfg["dht_interval"] == 5 else 5
                    else:
                        cfg["buzzer_enabled"] = not cfg["buzzer_enabled"]
                        hw.buzzer_enabled = cfg["buzzer_enabled"]
                    config.save_config(cfg)
                    hw.beep(0.07)
            elif g == "SINGLE":
                page = (page + 1) % TOTAL

            # ----- consistent 16-char render -----
            if in_settings:
                if settings_idx == 0:
                    hw.display("SET Log Interval", f"> {cfg['log_interval']:2d}s   Hold")
                elif settings_idx == 1:
                    hw.display("SET DHT Interval", f"> {cfg['dht_interval']:2d}s   Hold")
                else:
                    state = "ON " if cfg["buzzer_enabled"] else "OFF"
                    hw.display("SET Buzzer      ", f"> {state}    Hold")
            else:
                t = env["temp"]
                h = env["hum"]
                t_s = f"{t:4.1f}C" if t is not None else " --.-C"
                h_s = f"{h:4.1f}%" if h is not None else " --.-%"

                if page == 0:
                    hw.display(
                        f"CPU {metrics['cpu']:5.1f}%     ",
                        f"RAM {metrics['ram_used']:4.1f}/{metrics['ram_total']:4.1f}G"
                    )
                elif page == 1:
                    hw.display(
                        f"Disk {metrics['disk_pct']:5.1f}%    ",
                        f"Temp {t_s}      "
                    )
                elif page == 2:
                    up = fmt_rate(metrics["net_out"])
                    dn = fmt_rate(metrics["net_in"])
                    hw.display(f"Net Up {up}   ", f"Net Dn {dn}   ")
                else:
                    hw.display(
                        f"Guests {metrics['active_vms']:3d}      ",
                        f"Hum  {h_s}     "
                    )

            time.sleep(0.035)

    except KeyboardInterrupt:
        log.info("Shutdown requested")
    finally:
        stop.set()
        hw.cleanup()
        log.info("=== PVE Node Monitor stopped ===")


if __name__ == "__main__":
    main()