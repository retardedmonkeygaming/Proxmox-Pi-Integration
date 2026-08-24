import time
import threading
from typing import Any, Dict, List

import uvicorn
import config
import database
from hardware import HardwareManager
from monitor import ProxmoxManager
from logging_setup import setup_logging
from app import app as fastapi_app

def fmt_rate(kbps: float) -> str:
    if kbps >= 1024:
        return f"{kbps/1024:5.1f}M"
    return f"{kbps:5.0f}K"

def fmt_uptime(secs: int) -> str:
    if secs < 3600:
        return f"{secs//60}m"
    if secs < 86400:
        return f"{secs//3600}h"
    return f"{secs//86400}d"

def main():
    cfg = config.load_config()

    if not cfg.get("setup_done") or not cfg.get("nodes"):
        print("\n" + "=" * 50)
        print("  PVE Node Monitor – First Boot")
        print("=" * 50)
        print("1) Terminal setup")
        print("2) WebUI setup (recommended)")
        choice = input("Choose [1/2]: ").strip() or "2"

        if choice == "1":
            cfg = config.run_terminal_wizard()
        else:
            print("\nOpen http://<pi-ip>:8000/setup in your browser")
            def run_setup():
                uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, log_level="warning")
            threading.Thread(target=run_setup, daemon=True).start()
            while not config.load_config().get("setup_done"):
                time.sleep(1)
            cfg = config.load_config()

    log = setup_logging(cfg.get("log_level", "INFO"))
    log.info("=== PVE Node Monitor starting ===")

    database.init_db()
    hw = HardwareManager(
        hold_time=cfg["hold_time"],
        multi_tap_window=cfg["multi_tap_window"],
        buzzer_enabled=cfg["buzzer_enabled"],
    )
    pve = ProxmoxManager(cfg["nodes"])

    all_metrics: List[Dict[str, Any]] = []
    current_idx = min(cfg.get("default_node_idx", 0), max(0, len(cfg["nodes"]) - 1))
    humidity = None
    stop = threading.Event()

    def poller():
        nonlocal all_metrics
        while not stop.is_set():
            results = pve.poll_all()
            all_metrics = results
            for s in results:
                try:
                    database.log_server_metrics(
                        s["name"], s["cpu"], s["ram_used"], s["ram_total"],
                        s["disk_pct"], s["net_in"], s["net_out"],
                        s["active_vms"], 1 if s["online"] else 0
                    )
                except Exception as e:
                    log.error("DB: %s", e)
            stop.wait(cfg["log_interval"])

    def env_worker():
        nonlocal humidity
        while not stop.is_set():
            h = hw.read_humidity()
            if h is not None:
                humidity = h
                try:
                    database.log_humidity(h)
                except Exception:
                    pass
            stop.wait(cfg["dht_interval"])

    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=env_worker, daemon=True).start()

    def run_web():
        uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, log_level="warning", access_log=False)
    threading.Thread(target=run_web, daemon=True).start()
    log.info("Web UI → http://0.0.0.0:8000")

    page = 0
    TOTAL = 4
    in_settings = False
    settings_idx = 0
    last_flash = time.time()
    flash_active = False
    last_activity = time.time()

    try:
        while True:
            g = hw.read_gesture()
            now = time.time()
            metrics = all_metrics[current_idx] if all_metrics else {"online": False, "name": "?"}

            if in_settings and now - last_activity > 15:
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
                    settings_idx = (settings_idx + 1) % 5
                elif g == "HOLD":
                    if settings_idx == 0:
                        opts = [5, 10, 30, 60]
                        i = opts.index(cfg["log_interval"]) if cfg["log_interval"] in opts else 0
                        cfg["log_interval"] = opts[(i + 1) % len(opts)]
                    elif settings_idx == 1:
                        cfg["dht_interval"] = 60 if cfg["dht_interval"] == 30 else 30
                    elif settings_idx == 2:
                        cfg["buzzer_enabled"] = not cfg["buzzer_enabled"]
                        hw.buzzer_enabled = cfg["buzzer_enabled"]
                    elif settings_idx == 3:
                        current_idx = (current_idx + 1) % max(1, len(all_metrics))
                        cfg["default_node_idx"] = current_idx
                    elif settings_idx == 4:
                        cfg["quiet_mode"] = not cfg.get("quiet_mode", False)
                    config.save_config(cfg)
                    hw.beep(0.07)
            elif g == "SINGLE":
                page = (page + 1) % TOTAL
            elif g == "HOLD" and not in_settings:
                current_idx = (current_idx + 1) % max(1, len(all_metrics))
                hw.beep(0.05, 2)

            # ----- RENDER -----
            if in_settings:
                labels = [
                    ("Log Interval", f"> {cfg['log_interval']}s"),
                    ("DHT Interval", f"> {cfg['dht_interval']}s"),
                    ("Buzzer", f"> {'ON' if cfg['buzzer_enabled'] else 'OFF'}"),
                    ("Active Node", f"> {metrics.get('name','?')[:10]}"),
                    ("Quiet Mode", f"> {'ON' if cfg.get('quiet_mode') else 'OFF'}"),
                ]
                title, val = labels[settings_idx]
                hw.display(f"SET {title[:12]}", f"{val:<16}")
            else:
                # Smooth node-name flash
                if now - last_flash >= cfg.get("flash_interval", 10):
                    flash_active = True
                    last_flash = now

                if flash_active and (now - last_flash) <= cfg.get("flash_duration", 2.2):
                    name = (metrics.get("name") or "?")[:14]
                    hw.force_display("     NODE:     ", f"[{name.center(14)}]")
                else:
                    flash_active = False
                    if not metrics.get("online"):
                        hw.display(" System Offline", f"{(metrics.get('name') or '')[:16]:^16}")
                    else:
                        if page == 0:
                            hw.display(
                                f"CPU: {metrics['cpu']:5.1f}%    ",
                                f"RAM: {metrics['ram_used']:4.1f}/{metrics['ram_total']:4.1f}G"
                            )
                        elif page == 1:
                            hw.display(
                                f"Disk: {metrics['disk_pct']:5.1f}%   ",
                                f"VMs:  {metrics['active_vms']:3d}      "
                            )
                        elif page == 2:
                            up = fmt_rate(metrics["net_out"])
                            dn = fmt_rate(metrics["net_in"])
                            hw.display(f"Up:  {up}     ", f"Dn:  {dn}     ")
                        else:
                            uptime = fmt_uptime(metrics.get("uptime", 0))
                            hum = f"{humidity:.0f}%" if humidity is not None else "--%"
                            hw.display(f"Up:  {uptime:<6}    ", f"Hum: {hum:<6}    ")

            time.sleep(0.035)
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    finally:
        stop.set()
        hw.cleanup()
        log.info("=== Stopped ===")

if __name__ == "__main__":
    main()