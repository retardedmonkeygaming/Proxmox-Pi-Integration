import time
import threading
import os
from typing import Any, Dict, List
from datetime import datetime

import uvicorn
import config
import database
from database import log_alert, log_activity, ack_alert
from hardware import HardwareManager
from monitor import ProxmoxManager
from logging_setup import setup_logging
from app import app as fastapi_app, lcd_state

BUZZER_TEST_FLAG = "/tmp/pve_buzzer_test"

def fmt_rate(kbps: float) -> str:
    if kbps >= 1024:
        return f"{kbps/1024:4.1f}M"
    return f"{kbps:4.0f}K"

def fmt_uptime(secs: int) -> str:
    d = secs // 86400
    h = (secs % 86400) // 3600
    m = (secs % 3600) // 60
    if d > 0:
        return f"{d}d {h:02d}h"
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def fire_webhook(cfg: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """POST JSON to webhook_url (Discord/Telegram/generic). Best-effort."""
    url = (cfg.get("webhook_url") or "").strip()
    if not url:
        return
    try:
        import requests as _req
        body = dict(payload)
        body.setdefault("source", "pve-node-monitor")
        # Discord-friendly content field if not present
        if "content" not in body and "text" not in body:
            msg = body.get("message") or body.get("event") or "alert"
            body["content"] = f"**PVE Monitor** · {msg}"
        _req.post(url, json=body, timeout=4)
    except Exception:
        pass


def in_quiet_hours(cfg: Dict[str, Any]) -> bool:
    qh = cfg.get("quiet_hours") or ""
    if not qh or "-" not in qh:
        return False
    try:
        start_s, end_s = qh.split("-", 1)
        now_t = datetime.now().time()
        start = datetime.strptime(start_s.strip(), "%H:%M").time()
        end = datetime.strptime(end_s.strip(), "%H:%M").time()
        if start <= end:
            return start <= now_t <= end
        return now_t >= start or now_t <= end
    except Exception:
        return False


def get_pi_ip() -> str:
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?.?.?.?"


def lcd_center(text: str, width: int = 16) -> str:
    t = (text or "")[:width]
    pad = width - len(t)
    left = pad // 2
    return (" " * left) + t + (" " * (pad - left))


def lcd_page_lines(page: int, metrics: dict) -> tuple:
    """Pretty 16x2 lines for each page index."""
    name = (metrics.get("name") or "?")[:14]
    cpu = float(metrics.get("cpu") or 0)
    ru = float(metrics.get("ram_used") or 0)
    rt = float(metrics.get("ram_total") or 0)
    disk = float(metrics.get("disk_pct") or 0)
    vms = int(metrics.get("active_vms") or 0)
    load1 = float(metrics.get("load1") or 0)
    top = str(metrics.get("top_vm") or "—")[:14]
    free = metrics.get("disk_free_gb")

    if page == 0:
        return (
            lcd_center(f"CPU {cpu:5.1f}%"),
            lcd_center(f"RAM {ru:.1f}/{rt:.0f}G"),
        )
    if page == 1:
        free_s = f"{float(free):.0f}G free" if free is not None else f"{vms} guests"
        return (
            lcd_center(f"DISK {disk:4.1f}%"),
            lcd_center(free_s),
        )
    if page == 2:
        up = fmt_rate(float(metrics.get("net_out") or 0)).strip()
        dn = fmt_rate(float(metrics.get("net_in") or 0)).strip()
        return (
            lcd_center("NET  UP / DN"),
            lcd_center(f"{up} / {dn}"),
        )
    if page == 3:
        return (
            lcd_center("UPTIME"),
            lcd_center(fmt_uptime(int(metrics.get("uptime") or 0))),
        )
    if page == 4:
        return (
            lcd_center("LOAD 1m"),
            lcd_center(f"{load1:.2f}"),
        )
    if page == 5:
        return (
            lcd_center("TOP GUEST"),
            lcd_center(top),
        )
    if page == 6:
        return (
            lcd_center("THIS PI"),
            lcd_center(get_pi_ip()),
        )
    # fallback node info
    return (
        lcd_center(name),
        lcd_center((metrics.get("ip") or "")[:16]),
    )


PAGE_LABELS = {
    0: "CPU / RAM",
    1: "Disk",
    2: "Network",
    3: "Uptime",
    4: "Load",
    5: "Top guest",
    6: "Pi IP",
}

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
            while not config.load_config().get("setup_done") or not config.load_config().get("pins_done"):
                time.sleep(1)
            cfg = config.load_config()

    log = setup_logging(cfg.get("log_level", "INFO"))
    log.info("=== PVE Node Monitor starting ===")

    database.init_db()
    hw = HardwareManager(cfg)
    pve = ProxmoxManager(cfg["nodes"])

    try:
        hw.force_display(lcd_center("PVE MONITOR"), lcd_center("starting..."))
        time.sleep(1.0)
        hw.force_display(lcd_center("PVE MONITOR"), lcd_center("ready"))
        time.sleep(0.5)
    except Exception:
        pass

    all_metrics: List[Dict[str, Any]] = []
    current_idx = min(cfg.get("default_node_idx", 0), max(0, len(cfg["nodes"]) - 1))
    stop = threading.Event()
    alert_cooldown = 0.0
    last_cfg_check = 0.0
    alerting = False
    escalation_level = 0
    prev_online: Dict[str, bool] = {}

    def poller():
        nonlocal all_metrics
        while not stop.is_set():
            cfg_now = config.load_config()
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

    threading.Thread(target=poller, daemon=True).start()

    def run_web():
        uvicorn.run(fastapi_app, host="0.0.0.0", port=8000, log_level="warning", access_log=False)
    threading.Thread(target=run_web, daemon=True).start()
    log.info("Web UI → http://0.0.0.0:8000")

    page = 0
    TOTAL = 7
    in_settings = False
    settings_idx = 0
    last_flash = time.time()
    last_auto_scroll = time.time()
    flash_active = False
    last_activity = time.time()
    confirm_shutdown = False
    confirm_timer = 0.0

    try:
        while True:
            now = time.time()
            if now - last_cfg_check > 3:
                cfg = config.load_config()
                hw.buzzer_enabled = cfg.get("buzzer_enabled", True) and cfg.get("has_active_buzzer", True)
                hw.passive_buzzer_enabled = cfg.get("passive_buzzer_enabled", True) and cfg.get("has_passive_buzzer", True)
                last_cfg_check = now

            if os.path.exists(BUZZER_TEST_FLAG):
                try:
                    os.remove(BUZZER_TEST_FLAG)
                    hw.test_beep()
                except Exception:
                    pass

            g = hw.read_gesture()
            if g:
                last_activity = now
            metrics = all_metrics[current_idx] if all_metrics else {"online": False, "name": "?"}

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

            if g == "DOUBLE" and alerting:
                hw.alert_silenced = True
                alerting = False
                hw.beep(0.05, 1)
                last_activity = now
                try:
                    ack_alert(node_name=metrics.get("name"))
                    log_activity("alert_ack", str(metrics.get("name","")), "hardware")
                except Exception:
                    pass
                try:
                    ack_alert(node_name=metrics.get("name"))
                    log_activity("alert_ack", metrics.get("name", ""), "hardware")
                except Exception:
                    pass
                try:
                    database.ack_alert(node_name=metrics.get("name"))
                except Exception:
                    pass
            elif g == "DOUBLE" and not in_settings:
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
                    settings_idx = (settings_idx + 1) % 7
                    lcd_state["settings_idx"] = settings_idx
                elif g == "HOLD":
                    if settings_idx == 0:
                        opts = [5, 10, 30, 60]
                        i = opts.index(cfg["log_interval"]) if cfg["log_interval"] in opts else 0
                        cfg["log_interval"] = opts[(i + 1) % len(opts)]
                    elif settings_idx == 1:
                        cfg["buzzer_enabled"] = not cfg["buzzer_enabled"]
                        hw.buzzer_enabled = cfg["buzzer_enabled"]
                    elif settings_idx == 2:
                        cfg["passive_buzzer_enabled"] = not cfg.get("passive_buzzer_enabled", True)
                        hw.passive_buzzer_enabled = cfg["passive_buzzer_enabled"]
                    elif settings_idx == 3:
                        current_idx = (current_idx + 1) % max(1, len(all_metrics) or 1)
                        cfg["default_node_idx"] = current_idx
                    elif settings_idx == 4:
                        cfg["flash_hostname"] = not cfg.get("flash_hostname", True)
                    elif settings_idx == 5:
                        cfg["quiet_mode"] = not cfg.get("quiet_mode", False)
                    elif settings_idx == 6:
                        if not confirm_shutdown:
                            confirm_shutdown = True
                            confirm_timer = now
                            hw.beep(0.08, 2)
                        else:
                            name = metrics.get("name")
                            client = pve.get_client(name) if name else None
                            if client:
                                ok = client.power("shutdown")
                                hw.alert_tone()
                                log.info("Shutdown sent to %s: %s", name, ok)
                                try: log_activity("shutdown", str(name), "hardware")
                                except: pass
                            confirm_shutdown = False
                    config.save_config(cfg)
                    hw.beep(0.07)
            elif g == "SINGLE":
                enabled = cfg.get("lcd_pages_enabled") or list(range(TOTAL))
                enabled = [int(x) for x in enabled if int(x) < TOTAL]
                if not enabled:
                    enabled = list(range(TOTAL))
                if page in enabled:
                    idx = enabled.index(page)
                    page = enabled[(idx + 1) % len(enabled)]
                else:
                    page = enabled[0]
                lcd_state["page"] = page
                last_auto_scroll = now
            elif g == "HOLD" and alerting:
                hw.alert_silenced = True
                alerting = False
                escalation_level = 0
                hw.beep(0.15, 3)
                try:
                    ack_alert()
                    log_activity("emergency_mute", "all", "hardware")
                except Exception:
                    pass
                last_activity = now
            elif g == "HOLD" and not in_settings:
                current_idx = (current_idx + 1) % max(1, len(all_metrics) or 1)
                cfg["default_node_idx"] = current_idx
                config.save_config(cfg)
                hw.beep(0.05, 2)

            if confirm_shutdown and now - confirm_timer > 5:
                confirm_shutdown = False

            # ALERTS
            quiet = cfg.get("quiet_mode") or in_quiet_hours(cfg)
            if quiet:
                hw.alert_silenced = True

            # Offline / online transitions (all nodes)
            for s in all_metrics:
                name = s.get("name") or "?"
                online = bool(s.get("online"))
                was = prev_online.get(name)
                if was is not None and was and not online:
                    try:
                        log_alert(name, "offline", f"{name} went offline", 0, 0)
                        log_activity("offline", name, "system")
                    except Exception:
                        pass
                    fire_webhook(cfg, {"event": "offline", "node": name, "message": f"{name} went offline"})
                    if not quiet and not hw.alert_silenced:
                        hw.pattern("warn")
                elif was is not None and (not was) and online:
                    try:
                        log_activity("online", name, "system")
                    except Exception:
                        pass
                    fire_webhook(cfg, {"event": "online", "node": name, "message": f"{name} back online"})
                prev_online[name] = online

            alerting = False
            ram_pct = 0.0
            if metrics.get("online") and not quiet:
                ncfg = next((x for x in cfg.get("nodes", []) if x.get("name") == metrics.get("name")), {})
                cpu_a = ncfg["cpu_alert"] if ncfg.get("cpu_alert") is not None else cfg.get("cpu_alert", 85)
                ram_a = ncfg["ram_alert"] if ncfg.get("ram_alert") is not None else cfg.get("ram_alert", 90)
                disk_a = ncfg["disk_alert"] if ncfg.get("disk_alert") is not None else cfg.get("disk_alert", 90)
                ram_pct = (metrics["ram_used"] / metrics["ram_total"] * 100) if metrics.get("ram_total") else 0
                # free disk GB (rootfs free ≈ total * (1 - pct/100))
                disk_free_gb = None
                if metrics.get("ram_total") is not None:  # metrics has no rootfs total; approx from pct only if we had total
                    pass
                # Use ram_total field pattern – disk free needs rootfs; approximate not available from current stats.
                # disk_pct is available; disk_free_gb_alert uses inverse when we have totals from poller later.
                # For now compute free fraction against a synthetic value only if disk_pct present:
                disk_free_ok = True
                free_thr = cfg.get("disk_free_gb_alert")
                # Without absolute rootfs size in metrics, skip absolute GB check unless we extend monitor.
                # (Phase 4 still stores the setting; GB check activates when monitor exposes free_gb.)

                over = (
                    metrics["cpu"] >= cpu_a
                    or ram_pct >= ram_a
                    or metrics["disk_pct"] >= disk_a
                )
                # Absolute free GB if monitor provided it
                if free_thr is not None and metrics.get("disk_free_gb") is not None:
                    try:
                        if float(metrics["disk_free_gb"]) <= float(free_thr):
                            over = True
                    except Exception:
                        pass

                if over:
                    alerting = True
                    repeat = cfg.get("alert_repeat_sec", 25)
                    if not hw.alert_silenced and now - alert_cooldown > repeat:
                        use_esc = cfg.get("alert_escalation", True)
                        if use_esc:
                            escalation_level = min(escalation_level + 1, 3)
                        else:
                            escalation_level = 2
                        if escalation_level <= 1:
                            hw.beep(0.06, 2)
                            hw.pattern("info")
                        elif escalation_level == 2:
                            hw.pattern("warn")
                        else:
                            hw.alert_tone()
                        alert_cooldown = now
                        try:
                            if free_thr is not None and metrics.get("disk_free_gb") is not None and float(metrics["disk_free_gb"]) <= float(free_thr):
                                atype, val, thr = "disk_free", float(metrics["disk_free_gb"]), float(free_thr)
                                msg = f"DISK FREE {val:.1f}G <= {thr}G"
                            elif metrics["cpu"] >= cpu_a:
                                atype, val, thr = "cpu", metrics["cpu"], cpu_a
                                msg = f"CPU {val:.0f}% >= {thr}%"
                            elif ram_pct >= ram_a:
                                atype, val, thr = "ram", ram_pct, ram_a
                                msg = f"RAM {val:.0f}% >= {thr}%"
                            else:
                                atype, val, thr = "disk", metrics["disk_pct"], disk_a
                                msg = f"DISK {val:.0f}% >= {thr}%"
                            log_alert(metrics.get("name", "?"), atype, msg, val, thr)
                            fire_webhook(cfg, {
                                "event": "threshold",
                                "node": metrics.get("name"),
                                "alert_type": atype,
                                "message": msg,
                                "value": val,
                                "threshold": thr,
                                "escalation": escalation_level,
                            })
                        except Exception:
                            pass
                else:
                    hw.alert_silenced = False
                    escalation_level = 0

            # DISPLAY
            ss = int(cfg.get("lcd_screensaver_sec") or 0)
            if ss > 0 and not in_settings and (now - last_activity) > ss and not alerting:
                hw.display("", "")
                lcd_state["mode"] = "BLANK"
                lcd_state["last_lines"] = ("", "")
                lcd_state["page"] = page
                lcd_state["in_settings"] = False
                lcd_state["alerting"] = False
                time.sleep(0.05)
                continue

            if in_settings:
                if confirm_shutdown and settings_idx == 6:
                    hw.force_display("  SHUTDOWN?   ", " HOLD = send  ")
                else:
                    labels = [
                        ("Log Interval", f"> {cfg['log_interval']}s"),
                        ("Act Buzzer", f"> {'ON' if cfg['buzzer_enabled'] else 'OFF'}"),
                        ("Pas Buzzer", f"> {'ON' if cfg.get('passive_buzzer_enabled') else 'OFF'}"),
                        ("Active Node", f"> {(metrics.get('name') or '?')[:10]}"),
                        ("Name Flash", f"> {'ON' if cfg.get('flash_hostname', True) else 'OFF'}"),
                        ("Quiet Mode", f"> {'ON' if cfg.get('quiet_mode') else 'OFF'}"),
                        ("Shutdown", "> HOLD=send"),
                    ]
                    title, val = labels[settings_idx]
                    hw.display(f"SET {title[:12]}", f"{val:<16}")
                lcd_state["mode"] = "SETTINGS"
            else:
                do_flash = cfg.get("flash_hostname", True)
                flash_int = cfg.get("flash_interval", 10)
                if do_flash and now - last_flash >= flash_int:
                    flash_active = True
                    last_flash = now

                if do_flash and flash_active and (now - last_flash) <= cfg.get("flash_duration", 2.2):
                    name = (metrics.get("name") or "?")[:14]
                    hw.force_display("     NODE:     ", f"[{name.center(14)}]")
                    lcd_state["mode"] = "FLASH"
                else:
                    flash_active = False
                    lcd_state["mode"] = "PAGES"

                    # Auto-scroll through enabled pages (pauses 15s after touch)
                    if (
                        cfg.get("lcd_auto_scroll", True)
                        and not in_settings
                        and not alerting
                        and (now - last_activity) > 15
                    ):
                        interval = float(cfg.get("lcd_auto_scroll_sec") or 5)
                        # Dynamic: slightly faster when many pages enabled
                        enabled = cfg.get("lcd_pages_enabled") or list(range(TOTAL))
                        enabled = [int(x) for x in enabled if 0 <= int(x) < TOTAL]
                        if not enabled:
                            enabled = list(range(TOTAL))
                        dyn = max(2.5, interval * (4.0 / max(4, len(enabled))))
                        if now - last_auto_scroll >= dyn:
                            if page in enabled:
                                page = enabled[(enabled.index(page) + 1) % len(enabled)]
                            else:
                                page = enabled[0]
                            lcd_state["page"] = page
                            last_auto_scroll = now

                    if alerting and not hw.alert_silenced:
                        line2 = f"C{metrics['cpu']:.0f} R{ram_pct:.0f} D{metrics['disk_pct']:.0f}"
                        hw.force_display(lcd_center("! ALERT !"), lcd_center(line2))
                    elif not metrics.get("online"):
                        hw.display(lcd_center("OFFLINE"), lcd_center((metrics.get("name") or "?")[:16]))
                    else:
                        l1, l2 = lcd_page_lines(page, metrics)
                        hw.display(l1, l2)

            lcd_state["last_lines"] = hw.get_display_text()
            lcd_state["page"] = page
            lcd_state["in_settings"] = in_settings
            lcd_state["alerting"] = alerting and not hw.alert_silenced

            time.sleep(0.035)
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    finally:
        stop.set()
        hw.cleanup()
        log.info("=== Stopped ===")

if __name__ == "__main__":
    main()
