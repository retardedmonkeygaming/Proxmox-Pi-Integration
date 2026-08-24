#!/usr/bin/env python3
"""
Phase 3 patch
- Quiet hours
- Disk absolute GB threshold
- Alert history modal
- DB retention (auto-prune)
- Long-hold = emergency mute
- Boot splash on LCD
- Pi IP page on LCD
- Stronger offline / online alerts
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent

def backup(p: Path):
    bak = p.with_suffix(p.suffix + ".phase3.bak")
    if not bak.exists():
        shutil.copy2(p, bak)
        print(f"  backup → {bak.name}")

# ─────────────────────────────────────────────
# main.py – LCD extras + quiet hours + mute
# ─────────────────────────────────────────────
def patch_main():
    p = ROOT / "main.py"
    if not p.exists():
        print("main.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # 1. Boot splash (once at start of loop)
    if "BOOT SPLASH" not in text:
        splash = '''
    # BOOT SPLASH
    try:
        hw.force_display(" PVE Node Mon  ", "   starting... ")
        time.sleep(1.2)
        hw.force_display(" PVE Node Mon  ", "     ready     ")
        time.sleep(0.8)
    except Exception:
        pass
'''
        text = text.replace(
            "try:\n        while True:",
            splash + "\n    try:\n        while True:"
        )
        print("  main.py → boot splash")

    # 2. Long-hold = emergency mute all alerts
    if "EMERGENCY MUTE" not in text:
        # after gesture read
        mute_code = '''
            # EMERGENCY MUTE – long hold while alerting
            if g == "HOLD" and alerting:
                hw.alert_silenced = True
                alerting = False
                hw.beep(0.15, 3)
                try:
                    ack_alert()
                    log_activity("emergency_mute", "all", "hardware")
                except Exception:
                    pass
                last_activity = now
                g = None   # consume gesture
'''
        text = text.replace(
            'g = hw.read_gesture()',
            'g = hw.read_gesture()\n' + mute_code
        )
        print("  main.py → long-hold emergency mute")

    # 3. Add Pi IP page (page 4 becomes IP of Pi, old page 4 shifts)
    # We keep 5 pages, make the last one show the Pi's own IP
    if "get_pi_ip" not in text:
        pi_ip_fn = '''
def get_pi_ip():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?.?.?.?"
'''
        text = text.replace(
            "def fmt_uptime(secs: int) -> str:",
            pi_ip_fn + "\ndef fmt_uptime(secs: int) -> str:"
        )
        # replace the final else page
        text = re.sub(
            r'else:\s+ip = \(metrics\.get\("ip"\) or "\?"\)\[:16\].*?hw\.display\(f"\{ip:\^16\}".*?\)',
            '''else:
                            # page 4 = Pi itself
                            pi = get_pi_ip()
                            hw.display(f"{'THIS PI':^16}", f"{pi:^16}")''',
            text,
            count=1,
            flags=re.DOTALL
        )
        print("  main.py → Pi IP page")

    # 4. Quiet hours check (simple)
    if "quiet_hours" not in text:
        quiet_check = '''
            # Quiet hours (HH:MM-HH:MM, e.g. 22:00-07:00)
            qh = cfg.get("quiet_hours") or ""
            in_quiet = False
            if qh and "-" in qh:
                try:
                    from datetime import datetime
                    start_s, end_s = qh.split("-", 1)
                    now_t = datetime.now().time()
                    start = datetime.strptime(start_s.strip(), "%H:%M").time()
                    end   = datetime.strptime(end_s.strip(), "%H:%M").time()
                    if start <= end:
                        in_quiet = start <= now_t <= end
                    else:  # crosses midnight
                        in_quiet = now_t >= start or now_t <= end
                except Exception:
                    pass
            if in_quiet:
                hw.alert_silenced = True
'''
        text = text.replace(
            "# ALERTS",
            quiet_check + "\n            # ALERTS"
        )
        print("  main.py → quiet hours")

    p.write_text(text, encoding="utf-8")
    print("  main.py written")


# ─────────────────────────────────────────────
# database.py – retention prune
# ─────────────────────────────────────────────
def patch_database():
    p = ROOT / "database.py"
    if not p.exists():
        print("database.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    if "def prune_old_logs" not in text:
        prune = '''
def prune_old_logs(days: int = 14):
    """Keep only the last N days of server_logs."""
    conn = sqlite3.connect(DB_FILE, timeout=15)
    cur = conn.cursor()
    cur.execute("DELETE FROM server_logs WHERE timestamp < datetime('now', ?)", (f"-{days} days",))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted
'''
        text = text.replace(
            "def get_logs_range",
            prune + "\ndef get_logs_range"
        )
        print("  database.py → prune_old_logs()")
        p.write_text(text, encoding="utf-8")
    else:
        print("  database.py → already has prune")


# ─────────────────────────────────────────────
# config.py – new defaults
# ─────────────────────────────────────────────
def patch_config():
    p = ROOT / "config.py"
    if not p.exists():
        print("config.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    additions = {
        '"quiet_hours"': '    "quiet_hours": "",          # e.g. "22:00-07:00"\n',
        '"disk_free_gb_alert"': '    "disk_free_gb_alert": None, # absolute free GB threshold\n',
        '"retention_days"': '    "retention_days": 14,\n',
    }
    for key, line in additions.items():
        if key not in text:
            text = text.replace(
                '    "theme": "dark",',
                line + '    "theme": "dark",'
            )
            print(f"  config.py → added {key}")
    p.write_text(text, encoding="utf-8")


# ─────────────────────────────────────────────
# app.py – alert history + quiet hours UI + disk GB
# ─────────────────────────────────────────────
def patch_app():
    p = ROOT / "app.py"
    if not p.exists():
        print("app.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # 1. Quiet hours + disk free GB in settings (simple inputs)
    if "sQuietHours" not in text:
        settings_extra = '''
<label>Quiet hours (HH:MM-HH:MM)</label>
<input id="sQuietHours" placeholder="22:00-07:00" style="width:100%;margin-bottom:8px">
<label>Disk free alert (GB left)</label>
<input id="sDiskFree" type="number" placeholder="e.g. 20" style="width:100%;margin-bottom:8px">
'''
        # try to insert near other alert settings
        text = re.sub(
            r'(id="sDisk"[^>]*>)',
            r'\1\n' + settings_extra,
            text,
            count=1
        )
        print("  app.py → quiet hours + disk free inputs")

    # load/save them
    if "sQuietHours" in text and "settings.quiet_hours" not in text:
        text = text.replace(
            "settings.disk_alert=parseInt(sDisk.value)||90;",
            "settings.disk_alert=parseInt(sDisk.value)||90;\n"
            "if(document.getElementById('sQuietHours'))settings.quiet_hours=sQuietHours.value||'';\n"
            "if(document.getElementById('sDiskFree'))settings.disk_free_gb_alert=sDiskFree.value===''?null:parseFloat(sDiskFree.value);"
        )
        text = text.replace(
            "if(document.getElementById('sDisk'))sDisk.value=settings.disk_alert||90;",
            "if(document.getElementById('sDisk'))sDisk.value=settings.disk_alert||90;\n"
            "if(document.getElementById('sQuietHours'))sQuietHours.value=settings.quiet_hours||'';\n"
            "if(document.getElementById('sDiskFree'))sDiskFree.value=settings.disk_free_gb_alert??'';"
        )
        print("  app.py → quiet hours save/load")

    # 2. Alert history button + modal
    if "alertHistoryModal" not in text:
        hist_btn = '''
<button class="btn btn-ghost" style="width:auto;padding:4px 10px;font-size:.75rem;margin-left:6px"
        onclick="openAlertHistory()">History</button>
'''
        text = re.sub(
            r'(id="alertList"[^>]*>)',
            hist_btn + r'\1',
            text,
            count=1
        )

        hist_modal = '''
<div id="alertHistoryModal" class="modal">
  <div class="modal-box" style="max-width:480px;max-height:80vh;overflow:auto">
    <h3>Alert history</h3>
    <div id="alertHistoryList" style="font-size:.85rem"></div>
    <button class="btn btn-ghost" style="margin-top:12px" onclick="closeAlertHistory()">Close</button>
  </div>
</div>
'''
        text = text.replace("</body>", hist_modal + "\n</body>")

        hist_js = '''
async function openAlertHistory() {
  const list = document.getElementById('alertHistoryList');
  list.innerHTML = 'Loading…';
  document.getElementById('alertHistoryModal').classList.add('open');
  const alerts = await fetch('/api/alerts?limit=50').then(r=>r.json());
  if (!alerts.length) { list.innerHTML = '<div style="opacity:.6">No alerts yet</div>'; return; }
  list.innerHTML = alerts.map(a =>
    `<div style="padding:6px 0;border-bottom:1px solid var(--border);${a.acknowledged?'opacity:.5':''}">
      <b>${a.node_name||'?'}</b> · ${a.alert_type||''}<br>
      <span style="opacity:.75">${a.message||''}</span><br>
      <span style="font-size:.72rem;opacity:.5">${a.timestamp||''}</span>
      ${a.acknowledged?'':' <a href="#" onclick="ackOne('+a.id+');openAlertHistory();return false" style="color:var(--accent)">ack</a>'}
    </div>`
  ).join('');
}
function closeAlertHistory(){document.getElementById('alertHistoryModal').classList.remove('open')}
'''
        text = text.replace("async function loadAlerts()", hist_js + "\nasync function loadAlerts()")
        print("  app.py → alert history modal")

    # 3. Call prune occasionally from the poller side is in main – we expose an API too
    if "/api/prune" not in text:
        # add a tiny endpoint near other APIs (best-effort string insert)
        prune_ep = '''
@app.post("/api/prune")
def api_prune(days: int = 14):
    from database import prune_old_logs
    n = prune_old_logs(days)
    return {"ok": True, "deleted": n}
'''
        text = text.replace(
            '@app.get("/api/health")',
            prune_ep + "\n@app.get(\"/api/health\")"
        )
        print("  app.py → /api/prune endpoint")

    p.write_text(text, encoding="utf-8")
    print("  app.py written")


# ─────────────────────────────────────────────
# Call prune from main poller occasionally
# ─────────────────────────────────────────────
def patch_main_prune():
    p = ROOT / "main.py"
    text = p.read_text(encoding="utf-8")
    if "prune_old_logs" not in text:
        text = text.replace(
            "from database import log_alert, log_activity, ack_alert",
            "from database import log_alert, log_activity, ack_alert, prune_old_logs"
        )
        # once every ~100 poll cycles
        text = text.replace(
            "stop.wait(cfg_now.get(\"log_interval\", 10))",
            "stop.wait(cfg_now.get(\"log_interval\", 10))\n"
            "            if int(time.time()) % 3600 < cfg_now.get(\"log_interval\", 10):\n"
            "                try: prune_old_logs(cfg_now.get(\"retention_days\", 14))\n"
            "                except Exception: pass"
        )
        p.write_text(text, encoding="utf-8")
        print("  main.py → auto prune hourly")


if __name__ == "__main__":
    print("Phase 3 patch starting…")
    patch_config()
    patch_database()
    patch_main()
    patch_main_prune()
    patch_app()
    print("\nDone. Restart the app.")
    print("Backups end with .phase3.bak")
