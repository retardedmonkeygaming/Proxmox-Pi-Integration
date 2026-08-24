import time
import threading
from typing import Any, Dict, List

import uvicorn
import config
import database
from hardware import HardwareManager
from monitor import ProxmoxManager
from logging_setup import setup_logging
from app import app as fastapi_app, lcd_state

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
            print("\nOpen http://<pi-ip>:8000/setup")
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
        buzzer_enabled=cfg.get("buzzer_enabled", True),
        passive_buzzer_enabled=cfg.get("passive_buzzer_enabled", True),
    )
    pve = ProxmoxManager(cfg["nodes"])

    all_metrics: List[Dict[str, Any]] = []
    current_idx = min(cfg.get("default_node_idx", 0), max(0, len(cfg["nodes"]) - 1))
    humidity = None
    stop = threading.Event()
    alert_cooldown = 0.0
    last_cfg_check = 0.0

    def poller():
        nonlocal all_metrics
        while not stop.is_set():
            cfg_now = config.load_config()
            # hot-reload nodes if count changed
            if len(cfg_now.get("nodes", [])) != len(pve.clients):
                pve.reload(cfg_now["nodes"])
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
            stop.wait(cfg_now.get("log_interval", 10))

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
            stop.wait(config.load_config().get("dht_interval", 30))

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
    confirm_shutdown = False
    confirm_timer = 0.0

    try:
        while True:
            now = time.time()
            # reload live settings every 3s
            if now - last_cfg_check > 3:
                cfg = config.load_config()
                hw.buzzer_enabled = cfg.get("buzzer_enabled", True)
                hw.passive_buzzer_enabled = cfg.get("passive_buzzer_enabled", True)
                last_cfg_check = now

            g = hw.read_gesture()
            metrics = all_metrics[current_idx] if all_metrics else {"online": False, "name": "?"}

            # web-driven LCD actions
            if lcd_state.get("force_flash"):
                flash_active = True
                last_flash = now
                lcd_state["force_flash"] = False
            if lcd_state.get("in_settings") and not in_settings:
                in_settings = True
                settings_idx = lcd_state.get("settings_idx", 0)
            if not lcd_state.get("in_settings") and in_settings and lcd_state["mode"] == "PAGES":
                in_settings = False
            page = lcd_state.get("page", page)

            if in_settings and now - last_activity > 15:
                in_settings = False
                lcd_state["in_settings"] = False
                lcd_state["mode"] = "PAGES"

            # gesture handling
            if g == "DOUBLE" and not in_settings:
                in_settings = True
                settings_idx = 0
                last_activity = now
                lcd_state["in_settings"] = True
                lcd_state["mode"] = "SETTINGS"
            elif g == "TRIPLE" and in_settings:
                in_settings = False
                lcd_state["in_settings"] = False
                lcd_state["mode"] = "PAGES"
            elif in_settings:
                last_activity = now
                if g == "SINGLE":
                    settings_idx = (settings_idx + 1) % 6
                    lcd_state["settings_idx"] = settings_idx
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
                        cfg["passive_buzzer_enabled"] = not cfg.get("passive_buzzer_enabled", True)
                        hw.passive_buzzer_enabled = cfg["passive_buzzer_enabled"]
                    elif settings_idx == 4:
                        current_idx = (current_idx + 1) % max(1, len(all_metrics) or 1)
                        cfg["default_node_idx"] = current_idx
                    elif settings_idx == 5:
                        # SHUTDOWN CONFIRM
                        if not confirm_shutdown:
                            confirm_shutdown = True
                            confirm_timer = now
                            hw.beep(0.08, 2)
                        else:
                            # confirmed – send shutdown
                            name = metrics.get("name")
                            client = pve.get_client(name) if name else None
                            if client:
                                ok = client.power("shutdown")
                                hw.alert_tone(0.3, 3)
                                log.info("Shutdown sent to %s: %s", name, ok)
                            confirm_shutdown = False
                    config.save_config(cfg)
                    hw.beep(0.07)
            elif g == "SINGLE":
                page = (page + 1) % TOTAL
                lcd_state["page"] = page
            elif g == "HOLD" and not in_settings:
                current_idx = (current_idx + 1) % max(1, len(all_metrics) or 1)
                cfg["default_node_idx"] = current_idx
                config.save_config(cfg)
                hw.beep(0.05, 2)

            # clear confirm if timed out
            if confirm_shutdown and now - confirm_timer > 5:
                confirm_shutdown = False

            # ---- ALERTS ----
            alerting = False
            if metrics.get("online") and not cfg.get("quiet_mode"):
                cpu_a = cfg.get("cpu_alert", 85)
                ram_a = cfg.get("ram_alert", 90)
                disk_a = cfg.get("disk_alert", 90)
                ram_pct = (metrics["ram_used"] / metrics["ram_total"] * 100) if metrics.get("ram_total") else 0
                if metrics["cpu"] >= cpu_a or ram_pct >= ram_a or metrics["disk_pct"] >= disk_a:
                    alerting = True
                    if now - alert_cooldown > 30:
                        hw.alert_tone(0.2, 2)
                        alert_cooldown = now

            # ---- DISPLAY ----
            if in_settings:
                if confirm_shutdown and settings_idx == 5:
                    hw.force_display("SHUTDOWN?", "HOLD to confirm")
                else:
                    labels = [
                        ("Log Interval", f"> {cfg['log_interval']}s"),
                        ("DHT Interval", f"> {cfg['dht_interval']}s"),
                        ("Act Buzzer", f"> {'ON' if cfg['buzzer_enabled'] else 'OFF'}"),
                        ("Pas Buzzer", f"> {'ON' if cfg.get('passive_buzzer_enabled') else 'OFF'}"),
                        ("Active Node", f"> {(metrics.get('name') or '?')[:10]}"),
                        ("Shutdown", "> HOLD=send"),
                    ]
                    title, val = labels[settings_idx]
                    hw.display(f"SET {title[:12]}", f"{val:<16}")
                lcd_state["mode"] = "SETTINGS"
            else:
                flash_int = cfg.get("hostname_flash", cfg.get("flash_interval", 10))
                if now - last_flash >= flash_int:
                    flash_active = True
                    last_flash = now

                if flash_active and (now - last_flash) <= cfg.get("flash_duration", 2.2):
                    name = (metrics.get("name") or "?")[:14]
                    hw.force_display("     NODE:     ", f"[{name.center(14)}]")
                    lcd_state["mode"] = "FLASH"
                else:
                    flash_active = False
                    lcd_state["mode"] = "PAGES"
                    if alerting:
                        hw.force_display("!! ALERT !!", f"CPU{metrics['cpu']:.0f} R{ram_pct:.0f} D{metrics['disk_pct']:.0f}")
                    elif not metrics.get("online"):
                        hw.display(" System Offline", f"{(metrics.get('name') or '')[:16]:^16}")
                    else:
                        if page == 0:
                            hw.display(f"CPU: {metrics['cpu']:5.1f}%    ", f"RAM: {metrics['ram_used']:4.1f}/{metrics['ram_total']:4.1f}G")
                        elif page == 1:
                            hw.display(f"Disk: {metrics['disk_pct']:5.1f}%   ", f"VMs:  {metrics['active_vms']:3d}      ")
                        elif page == 2:
                            up = fmt_rate(metrics["net_out"])
                            dn = fmt_rate(metrics["net_in"])
                            hw.display(f"Up:  {up}     ", f"Dn:  {dn}     ")
                        else:
                            uptime = fmt_uptime(metrics.get("uptime", 0))
                            hum = f"{humidity:.0f}%" if humidity is not None else "--%"
                            hw.display(f"Up:  {uptime:<6}    ", f"Hum: {hum:<6}    ")

            # push lines to web preview
            lcd_state["last_lines"] = hw.get_display_text()
            lcd_state["page"] = page
            lcd_state["in_settings"] = in_settings

            time.sleep(0.035)
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    finally:
        stop.set()
        hw.cleanup()
        log.info("=== Stopped ===")

if __name__ == "__main__":
    main()
