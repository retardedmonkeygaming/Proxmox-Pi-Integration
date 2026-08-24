import csv
import io
import sqlite3
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

import config
from monitor import ProxmoxManager, NodeClient

app = FastAPI(title="PVE Node Monitor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB = "monitor.db"

lcd_state = {
    "page": 0,
    "in_settings": False,
    "settings_idx": 0,
    "force_flash": False,
    "last_lines": ("", ""),
    "mode": "PAGES",
    "humidity": None,
    "alerting": False,
}

def get_db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

@app.get("/api/current")
def api_current():
    cfg = config.load_config()
    valid = {n["name"] for n in cfg.get("nodes", [])}
    conn = get_db()
    rows = conn.execute("""
        SELECT node_name, cpu_usage, ram_used_gb, ram_total_gb, disk_pct,
               net_in_kbps, net_out_kbps, active_vms, online, timestamp
        FROM server_logs
        WHERE id IN (SELECT MAX(id) FROM server_logs GROUP BY node_name)
          AND node_name IS NOT NULL AND node_name != '' AND node_name != 'null'
    """).fetchall()
    conn.close()
    node_map = {n["name"]: n for n in cfg.get("nodes", [])}
    out = []
    for r in rows:
        if r["node_name"] not in valid:
            continue
        d = dict(r)
        ncfg = node_map.get(r["node_name"], {})
        d["ip"] = ncfg.get("ip", "")
        d["node"] = ncfg.get("node", "")
        d["type"] = ncfg.get("type", "server")
        out.append(d)
    return out

@app.get("/api/logs/server")
def api_logs(limit: int = 40, node: str = None):
    conn = get_db()
    if node:
        rows = conn.execute("""
            SELECT timestamp, node_name, cpu_usage, ram_used_gb, disk_pct,
                   net_in_kbps, net_out_kbps
            FROM server_logs WHERE node_name = ?
            ORDER BY id DESC LIMIT ?
        """, (node, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT timestamp, node_name, cpu_usage, ram_used_gb, disk_pct,
                   net_in_kbps, net_out_kbps
            FROM server_logs
            WHERE node_name IS NOT NULL AND node_name != '' AND node_name != 'null'
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]

@app.post("/api/power/{node_name}/{action}")
def api_power(node_name: str, action: str):
    if action not in ("shutdown", "reboot"):
        return JSONResponse({"ok": False, "error": "invalid"}, 400)
    cfg = config.load_config()
    mgr = ProxmoxManager(cfg["nodes"])
    client = mgr.get_client(node_name)
    if not client:
        return JSONResponse({"ok": False, "error": "not found"}, 404)
    return {"ok": client.power(action)}

@app.post("/api/nodes/add")
async def api_add_node(request: Request):
    data = await request.json()
    cfg = config.load_config()
    new = {
        "name": data.get("name", "").strip(),
        "ip": data.get("ip", "").strip(),
        "node": data.get("node", "").strip() or data.get("name", "").strip(),
        "user": data.get("user", "root@pam").strip(),
        "password": data.get("password", ""),
        "type": data.get("type", "server"),
    }
    if not new["name"] or not new["ip"]:
        return JSONResponse({"ok": False, "error": "name and ip required"}, 400)
    if any(n["name"] == new["name"] for n in cfg["nodes"]):
        return JSONResponse({"ok": False, "error": "name already exists"}, 400)
    cfg["nodes"].append(new)
    config.save_config(cfg)
    return {"ok": True}

@app.put("/api/nodes/{name}")
async def api_edit_node(name: str, request: Request):
    data = await request.json()
    cfg = config.load_config()
    for n in cfg["nodes"]:
        if n["name"] == name:
            n["name"] = data.get("name", n["name"]).strip() or n["name"]
            n["ip"] = data.get("ip", n["ip"]).strip() or n["ip"]
            n["node"] = data.get("node", n["node"]).strip() or n["node"]
            n["user"] = data.get("user", n["user"]).strip() or n["user"]
            if data.get("password"):
                n["password"] = data["password"]
            n["type"] = data.get("type", n.get("type", "server"))
            config.save_config(cfg)
            return {"ok": True}
    return JSONResponse({"ok": False, "error": "not found"}, 404)

@app.delete("/api/nodes/{name}")
def api_delete_node(name: str):
    cfg = config.load_config()
    cfg["nodes"] = [n for n in cfg["nodes"] if n["name"] != name]
    config.save_config(cfg)
    return {"ok": True}

@app.post("/api/test-connection")
async def api_test_connection(request: Request):
    data = await request.json()
    client = NodeClient(
        data.get("name", "test"), data.get("ip", ""),
        data.get("node", "pve"), data.get("user", "root@pam"),
        data.get("password", ""),
    )
    return client.test_connection()

@app.get("/api/settings")
def api_get_settings():
    cfg = config.load_config()
    return {
        "buzzer_enabled": cfg.get("buzzer_enabled", True),
        "passive_buzzer_enabled": cfg.get("passive_buzzer_enabled", True),
        "quiet_mode": cfg.get("quiet_mode", False),
        "compact_cards": cfg.get("compact_cards", False),
        "log_interval": cfg.get("log_interval", 10),
        "dht_interval": cfg.get("dht_interval", 30),
        "cpu_alert": cfg.get("cpu_alert", 85),
        "disk_alert": cfg.get("disk_alert", 90),
        "ram_alert": cfg.get("ram_alert", 90),
        "hostname_flash": cfg.get("hostname_flash", 10),
        "lcd_contrast": cfg.get("lcd_contrast", 70),
        "theme": cfg.get("theme", "dark"),
        "graph_order": cfg.get("graph_order", ["cpu", "ram", "net"]),
        "graph_visible": cfg.get("graph_visible", {"cpu": True, "ram": True, "net": True, "disk": False}),
        "show_humidity": cfg.get("show_humidity", True),
        "auto_refresh": cfg.get("auto_refresh", 5),
    }

@app.post("/api/settings")
async def api_save_settings(request: Request):
    data = await request.json()
    cfg = config.load_config()
    for k in (
        "buzzer_enabled", "passive_buzzer_enabled", "quiet_mode", "compact_cards",
        "log_interval", "dht_interval", "cpu_alert", "disk_alert", "ram_alert",
        "hostname_flash", "lcd_contrast", "theme", "graph_order", "graph_visible",
        "show_humidity", "auto_refresh"
    ):
        if k in data:
            cfg[k] = data[k]
    config.save_config(cfg)
    return {"ok": True}

@app.post("/api/buzzer/test")
def api_test_buzzer():
    return {"ok": True, "msg": "Test triggered"}

@app.get("/api/export/csv")
def api_export_csv():
    conn = get_db()
    rows = conn.execute("""
        SELECT timestamp, node_name, cpu_usage, ram_used_gb, ram_total_gb,
               disk_pct, net_in_kbps, net_out_kbps, active_vms, online
        FROM server_logs ORDER BY id DESC LIMIT 2000
    """).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "node", "cpu%", "ram_used_gb", "ram_total_gb",
                     "disk%", "net_in_kbps", "net_out_kbps", "vms", "online"])
    for r in rows:
        writer.writerow([r[c] for c in r.keys()])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pve_monitor_export.csv"}
    )

@app.get("/api/lcd")
def api_lcd_state():
    return {
        "lines": lcd_state["last_lines"],
        "mode": lcd_state["mode"],
        "page": lcd_state["page"],
        "in_settings": lcd_state["in_settings"],
        "humidity": lcd_state.get("humidity"),
        "alerting": lcd_state.get("alerting", False),
    }

@app.post("/api/lcd/{action}")
def api_lcd_action(action: str):
    if action == "page":
        lcd_state["page"] = (lcd_state["page"] + 1) % 5
        lcd_state["in_settings"] = False
        lcd_state["mode"] = "PAGES"
    elif action == "settings":
        lcd_state["in_settings"] = True
        lcd_state["settings_idx"] = 0
        lcd_state["mode"] = "SETTINGS"
    elif action == "change":
        if lcd_state["in_settings"]:
            lcd_state["settings_idx"] = (lcd_state["settings_idx"] + 1) % 7
        else:
            lcd_state["force_flash"] = True
            lcd_state["mode"] = "FLASH"
    elif action == "exit":
        lcd_state["in_settings"] = False
        lcd_state["mode"] = "PAGES"
    return {"ok": True, "state": lcd_state}

@app.get("/setup", response_class=HTMLResponse)
def setup_page():
    cfg = config.load_config()
    if cfg.get("setup_done") and cfg.get("nodes"):
        return RedirectResponse("/")
    return SETUP_HTML

@app.post("/setup")
async def setup_submit(request: Request):
    form = await request.form()
    nodes = []
    i = 0
    while True:
        name = form.get(f"name_{i}")
        if not name:
            break
        nodes.append({
            "name": name.strip(),
            "ip": form.get(f"ip_{i}", "").strip(),
            "node": form.get(f"node_{i}", name).strip(),
            "user": form.get(f"user_{i}", "root@pam").strip(),
            "password": form.get(f"password_{i}", ""),
            "type": "server",
        })
        i += 1
    if not nodes:
        return HTMLResponse("Need at least one node", 400)
    cfg = config.load_config()
    cfg["nodes"] = nodes
    cfg["setup_done"] = True
    cfg["log_interval"] = int(form.get("log_interval", 10))
    cfg["buzzer_enabled"] = form.get("buzzer") == "on"
    cfg["passive_buzzer_enabled"] = form.get("passive") == "on"
    cfg["theme"] = "dark"
    config.save_config(cfg)
    return RedirectResponse("/", status_code=303)

@app.get("/", response_class=HTMLResponse)
def dashboard():
    cfg = config.load_config()
    if not cfg.get("setup_done") or not cfg.get("nodes"):
        return RedirectResponse("/setup")
    return DASHBOARD_HTML

SETUP_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="dark"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor – Setup</title>
<style>
:root{--bg:#0a0a0a;--card:#141414;--text:#f3f4f6;--muted:#9ca3af;--accent:#3b82f6;--border:#262626}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;padding:24px}
.card{background:var(--card);border-radius:16px;padding:32px;max-width:480px;width:100%;border:1px solid var(--border)}
h1{margin:0 0 4px;font-size:1.5rem;font-weight:700}
.sub{color:var(--muted);margin-bottom:24px;font-size:.9rem}
label{display:block;margin:14px 0 6px;font-size:.85rem;color:var(--muted);font-weight:500}
input,select{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:#0a0a0a;color:var(--text);font-size:.95rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.btn{margin-top:24px;width:100%;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-weight:600;font-size:1rem;cursor:pointer}
.node-block{border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:14px}
.add{background:transparent;border:1px dashed #4b5563;color:var(--muted);width:100%;padding:10px;border-radius:10px;cursor:pointer;margin-top:8px}
.check-row{display:flex;align-items:center;gap:10px;margin-top:16px}
.check-row input{width:16px;height:16px}
.check-row label{margin:0;color:var(--text)}
</style></head><body>
<div class="card">
<h1>PVE Node Monitor</h1>
<p class="sub">First-time setup · Desk Console</p>
<form method="post" action="/setup">
<div id="nodes">
<div class="node-block">
<label>Friendly name</label><input name="name_0" value="Precision" required>
<label>IP / Hostname</label><input name="ip_0" required>
<label>Proxmox node name</label><input name="node_0" value="pve" required>
<div class="row">
<div><label>User</label><input name="user_0" value="root@pam"></div>
<div><label>Password</label><input name="password_0" type="password" required></div>
</div>
</div>
</div>
<button type="button" class="add" onclick="addNode()">+ Add another</button>
<label style="margin-top:20px">Log interval (s)</label>
<select name="log_interval"><option>5</option><option selected>10</option><option>30</option><option>60</option></select>
<div class="check-row"><input type="checkbox" name="buzzer" id="buzzer" checked><label for="buzzer">Active buzzer (GPIO 6)</label></div>
<div class="check-row"><input type="checkbox" name="passive" id="passive" checked><label for="passive">Passive buzzer (pin 20)</label></div>
<button class="btn" type="submit">Save & Start</button>
</form>
</div>
<script>
let idx=1;
function addNode(){
  document.getElementById('nodes').insertAdjacentHTML('beforeend', `
<div class="node-block">
<label>Friendly name</label><input name="name_${idx}" required>
<label>IP / Hostname</label><input name="ip_${idx}" required>
<label>Proxmox node name</label><input name="node_${idx}" required>
<div class="row">
<div><label>User</label><input name="user_${idx}" value="root@pam"></div>
<div><label>Password</label><input name="password_${idx}" type="password" required></div>
</div></div>`);
  idx++;
}
</script></body></html>"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root, [data-theme="light"] {
  --bg: #f4f5f7; --card: #ffffff; --text: #111827; --muted: #6b7280;
  --border: #e5e7eb; --accent: #2563eb; --green: #16a34a; --red: #dc2626;
  --orange: #ea580c; --purple: #7c3aed; --bar-bg: #e5e7eb;
  --lcd-bg: #0a1a0a; --lcd-text: #33ff66; --header-bg: #ffffff;
  --hover: #f9fafb;
}
[data-theme="dark"] {
  --bg: #0a0a0a; --card: #141414; --text: #f3f4f6; --muted: #9ca3af;
  --border: #262626; --accent: #3b82f6; --green: #22c55e; --red: #ef4444;
  --orange: #f97316; --purple: #a855f7; --bar-bg: #1f1f1f;
  --lcd-bg: #051005; --lcd-text: #33ff66; --header-bg: #0f0f0f;
  --hover: #1a1a1a;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg); color: var(--text);
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  min-height: 100vh; padding-bottom: 64px;
}
header {
  background: var(--header-bg); border-bottom: 1px solid var(--border);
  padding: 12px 24px; display: flex; align-items: center; justify-content: space-between;
  position: sticky; top: 0; z-index: 50;
}
.brand { text-align: center; flex: 1; }
.brand .desk { font-size: .65rem; letter-spacing: .14em; color: var(--muted); text-transform: uppercase; font-weight: 500; }
.brand h1 { font-size: 1.2rem; font-weight: 700; margin-top: 1px; }
.header-right { display: flex; align-items: center; gap: 8px; }
.pill {
  background: var(--card); border: 1px solid var(--border); border-radius: 999px;
  padding: 5px 11px; font-size: .78rem; color: var(--muted); font-weight: 500;
}
.btn {
  border: none; border-radius: 8px; padding: 7px 13px; font-size: .82rem;
  font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 5px;
  transition: opacity .15s;
}
.btn:hover { opacity: .88; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-ghost {
  background: transparent; border: 1px solid var(--border); color: var(--text);
  width: 32px; height: 32px; padding: 0; justify-content: center; border-radius: 8px; font-size: 14px;
}
.btn-ghost:hover { background: var(--hover); }
.main {
  max-width: 1080px; margin: 24px auto; padding: 0 18px;
  display: grid; grid-template-columns: 1fr 300px; gap: 18px;
}
@media (max-width: 860px) { .main { grid-template-columns: 1fr; } }
.node-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 14px;
  padding: 16px 18px; position: relative;
}
.node-card.offline { opacity: .65; }
.node-card.compact .mini-stats, .node-card.compact .bar { display: none; }
.node-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
.node-name { font-size: 1.05rem; font-weight: 700; }
.node-meta { font-size: .74rem; color: var(--muted); margin-top: 2px; }
.badge {
  font-size: .62rem; font-weight: 700; letter-spacing: .05em; padding: 3px 8px;
  border-radius: 999px; text-transform: uppercase;
}
.badge-on { background: rgba(34,197,94,.15); color: var(--green); }
.badge-off { background: rgba(239,68,68,.15); color: var(--red); }
.stat-row { display: flex; justify-content: space-between; font-size: .82rem; margin: 5px 0 2px; }
.bar { height: 5px; background: var(--bar-bg); border-radius: 3px; overflow: hidden; margin-bottom: 9px; }
.bar > div { height: 100%; border-radius: 3px; transition: width .4s ease; }
.mini-stats { display: flex; gap: 7px; margin: 10px 0 12px; }
.mini {
  flex: 1; background: var(--bg); border-radius: 8px; padding: 7px 5px; text-align: center;
  font-size: .68rem; color: var(--muted);
}
.mini strong { display: block; font-size: .85rem; color: var(--text); margin-top: 1px; font-weight: 600; }
.actions { display: flex; gap: 7px; align-items: center; }
.btn-reboot { background: var(--orange); color: #fff; flex: 1; padding: 8px; font-size: .8rem; }
.btn-shutdown { background: var(--red); color: #fff; flex: 1; padding: 8px; font-size: .8rem; }
.btn-icon {
  background: transparent; border: 1px solid var(--border); color: var(--muted);
  width: 32px; height: 32px; border-radius: 8px; cursor: pointer; font-size: 13px;
  display: flex; align-items: center; justify-content: center;
}
.btn-icon:hover { color: var(--text); border-color: var(--muted); background: var(--hover); }
.side-col { display: flex; flex-direction: column; gap: 14px; }
.side-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 14px;
  padding: 14px 16px;
}
.side-card h3 { font-size: .88rem; font-weight: 600; margin-bottom: 2px; }
.side-card .sub { font-size: .72rem; color: var(--muted); margin-bottom: 10px; }
.lcd-preview {
  background: var(--lcd-bg); color: var(--lcd-text); font-family: "Courier New", monospace;
  font-size: 14px; line-height: 1.5; padding: 12px 14px; border-radius: 8px;
  letter-spacing: 1.2px; margin-bottom: 10px; min-height: 54px;
  box-shadow: inset 0 0 18px rgba(0,0,0,.5);
}
.lcd-btns { display: flex; gap: 5px; flex-wrap: wrap; }
.lcd-btns button {
  flex: 1; min-width: 56px; padding: 6px 3px; border-radius: 7px; border: 1px solid var(--border);
  background: var(--bg); color: var(--text); font-size: .72rem; font-weight: 500; cursor: pointer;
}
.lcd-btns button:hover { border-color: var(--accent); color: var(--accent); }
.lcd-mode {
  float: right; font-size: .62rem; background: var(--bg); border: 1px solid var(--border);
  padding: 2px 7px; border-radius: 999px; color: var(--muted); font-weight: 600;
}
.alert-box { font-size: .8rem; color: var(--muted); line-height: 1.4; }
.alert-box.active { color: var(--orange); }
.graphs-wrap {
  grid-column: 1 / -1; background: var(--card); border: 1px solid var(--border);
  border-radius: 14px; padding: 16px 18px; margin-top: 2px;
}
.graphs-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.graphs-head h2 { font-size: .95rem; font-weight: 600; }
.graphs-head .sub { font-size: .74rem; color: var(--muted); }
.graphs-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
.chart-box { background: var(--bg); border-radius: 10px; padding: 10px 12px; min-height: 150px; }
.chart-box h4 { font-size: .76rem; color: var(--muted); margin-bottom: 6px; font-weight: 500; }
canvas { width: 100% !important; max-height: 130px; }
.drawer-bg {
  position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 100;
  opacity: 0; pointer-events: none; transition: opacity .2s;
}
.drawer-bg.open { opacity: 1; pointer-events: auto; }
.drawer {
  position: fixed; top: 0; right: 0; width: 330px; max-width: 100%; height: 100%;
  background: var(--card); border-left: 1px solid var(--border); z-index: 101;
  transform: translateX(100%); transition: transform .25s ease; overflow-y: auto;
  padding: 18px 20px 48px;
}
.drawer.open { transform: translateX(0); }
.drawer h2 { font-size: 1.1rem; margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center; }
.drawer .close-btn { background: none; border: none; color: var(--muted); font-size: 1.15rem; cursor: pointer; }
.set-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 11px 0; border-bottom: 1px solid var(--border);
}
.set-row label { font-size: .88rem; }
.set-row .hint { font-size: .7rem; color: var(--muted); margin-top: 1px; }
.toggle {
  width: 40px; height: 22px; background: var(--bar-bg); border-radius: 999px; position: relative;
  cursor: pointer; transition: background .2s; border: none; flex-shrink: 0;
}
.toggle.on { background: var(--accent); }
.toggle::after {
  content: ""; position: absolute; width: 16px; height: 16px; background: #fff;
  border-radius: 50%; top: 3px; left: 3px; transition: transform .2s;
  box-shadow: 0 1px 2px rgba(0,0,0,.25);
}
.toggle.on::after { transform: translateX(18px); }
.set-input {
  width: 100%; padding: 7px 10px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--bg); color: var(--text); font-size: .88rem; margin-top: 5px;
}
.set-group { margin: 14px 0 6px; }
.set-group label { font-size: .76rem; color: var(--muted); font-weight: 500; }
.drawer-actions { display: flex; gap: 8px; margin-top: 20px; }
.drawer-actions button { flex: 1; padding: 9px; border-radius: 8px; font-weight: 600; cursor: pointer; border: none; font-size: .82rem; }
.modal-bg {
  position: fixed; inset: 0; background: rgba(0,0,0,.6); z-index: 200;
  display: flex; align-items: center; justify-content: center; padding: 16px;
  opacity: 0; pointer-events: none; transition: opacity .2s;
}
.modal-bg.open { opacity: 1; pointer-events: auto; }
.modal {
  background: var(--card); border-radius: 14px; padding: 22px; width: 100%; max-width: 400px;
  border: 1px solid var(--border);
}
.modal h2 { font-size: 1.1rem; margin-bottom: 3px; }
.modal .sub { font-size: .82rem; color: var(--muted); margin-bottom: 14px; }
.choice { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-bottom: 14px; }
.choice button {
  padding: 11px; border-radius: 9px; border: 2px solid var(--border); background: var(--bg);
  color: var(--text); font-weight: 600; cursor: pointer; font-size: .85rem;
}
.choice button.active { border-color: var(--accent); background: rgba(59,130,246,.1); color: var(--accent); }
.modal label { display: block; margin: 10px 0 3px; font-size: .76rem; color: var(--muted); font-weight: 500; }
.modal input {
  width: 100%; padding: 8px 11px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--bg); color: var(--text); font-size: .92rem;
}
.modal-actions { display: flex; gap: 7px; margin-top: 18px; align-items: center; }
.modal-actions .spacer { flex: 1; }
.btn-cancel { background: var(--bg); border: 1px solid var(--border); color: var(--text); }
.btn-save { background: var(--accent); color: #fff; }
.btn-test { background: transparent; border: 1px solid var(--border); color: var(--muted); font-size: .78rem; padding: 7px 11px; }
footer {
  position: fixed; bottom: 0; left: 0; right: 0; background: var(--header-bg);
  border-top: 1px solid var(--border); padding: 9px 14px; text-align: center;
  font-size: .74rem; color: var(--muted); z-index: 40;
}
footer a { color: var(--accent); text-decoration: none; margin: 0 5px; }
.hidden { display: none !important; }
.toast {
  position: fixed; bottom: 68px; left: 50%; transform: translateX(-50%);
  background: var(--card); border: 1px solid var(--border); color: var(--text);
  padding: 9px 16px; border-radius: 9px; font-size: .82rem; z-index: 300;
  opacity: 0; transition: opacity .25s; pointer-events: none;
}
.toast.show { opacity: 1; }
.hum-badge {
  display: inline-block; background: var(--bg); border: 1px solid var(--border);
  border-radius: 6px; padding: 3px 8px; font-size: .72rem; color: var(--muted); margin-top: 6px;
}
</style>
</head>
<body>
<header>
  <div style="width:160px"></div>
  <div class="brand">
    <div class="desk">Desk Console</div>
    <h1>PVE Node Monitor</h1>
  </div>
  <div class="header-right">
    <span class="pill" id="onlinePill">–/– online</span>
    <button class="btn btn-primary" onclick="openAddModal()">+ Add node / server</button>
    <button class="btn-ghost" onclick="openSettings()" title="Settings">⚙</button>
    <button class="btn-ghost" id="themeBtn" onclick="toggleTheme()" title="Theme">☾</button>
    <button class="btn-ghost" onclick="load()" title="Refresh">↻</button>
  </div>
</header>

<div class="main">
  <div id="nodesCol"></div>
  <div class="side-col">
    <div class="side-card">
      <span class="lcd-mode" id="lcdMode">PAGES</span>
      <h3>Desk LCD</h3>
      <div class="sub">16×2 · touch pad · GPIO 20 buzzer</div>
      <div class="lcd-preview" id="lcdPreview">CPU:  --.-%&nbsp;&nbsp;&nbsp;&nbsp;<br>RAM: --.-/--.-G</div>
      <div class="lcd-btns">
        <button onclick="lcdAction('page')">Page</button>
        <button onclick="lcdAction('settings')">Settings</button>
        <button onclick="lcdAction('change')">Change</button>
        <button onclick="lcdAction('exit')">Exit</button>
      </div>
      <div class="hum-badge" id="humBadge">Humidity: --%</div>
    </div>
    <div class="side-card">
      <h3>Alerts</h3>
      <div class="alert-box" id="alertStatus">Quiet. Thresholds fire on the LCD and pin 20.</div>
    </div>
  </div>

  <div class="graphs-wrap">
    <div class="graphs-head">
      <div>
        <h2>Graphs</h2>
        <div class="sub">Live samples from the active node</div>
      </div>
      <button class="btn btn-ghost" style="width:auto;padding:5px 11px;font-size:.78rem" onclick="openGraphEdit()">✎ Edit</button>
    </div>
    <div class="graphs-grid" id="graphsGrid">
      <div class="chart-box" id="cCpu"><h4>CPU</h4><canvas id="chartCpu"></canvas></div>
      <div class="chart-box" id="cRam"><h4>RAM</h4><canvas id="chartRam"></canvas></div>
      <div class="chart-box" id="cNet"><h4>Network</h4><canvas id="chartNet"></canvas></div>
      <div class="chart-box hidden" id="cDisk"><h4>Disk</h4><canvas id="chartDisk"></canvas></div>
    </div>
  </div>
</div>

<footer>
  Insta: <a href="https://instagram.com/vxprxx" target="_blank">vxprxx</a> ·
  GitHub: <a href="https://github.com/retardedmonkeygaming" target="_blank">retardedmonkeygaming</a>
  <br><span style="font-size:.68rem;opacity:.65">PVE Node Monitor · 16×2 desk console · v1.3</span>
</footer>

<div class="drawer-bg" id="drawerBg" onclick="closeSettings()"></div>
<div class="drawer" id="drawer">
  <h2>Settings <button class="close-btn" onclick="closeSettings()">✕</button></h2>
  <div class="set-row">
    <div><label>Active buzzer</label><div class="hint">GPIO 6 clicks</div></div>
    <button class="toggle" id="tBuzzer" onclick="toggleSet(this,'buzzer_enabled')"></button>
  </div>
  <div class="set-row">
    <div><label>Passive buzzer (pin 20)</label><div class="hint">Alert tones</div></div>
    <button class="toggle" id="tPassive" onclick="toggleSet(this,'passive_buzzer_enabled')"></button>
  </div>
  <div class="set-row">
    <div><label>Quiet mode</label><div class="hint">Mute LCD alert tones</div></div>
    <button class="toggle" id="tQuiet" onclick="toggleSet(this,'quiet_mode')"></button>
  </div>
  <div class="set-row">
    <div><label>Compact cards</label><div class="hint">Hide bars & mini stats</div></div>
    <button class="toggle" id="tCompact" onclick="toggleSet(this,'compact_cards')"></button>
  </div>
  <div class="set-row">
    <div><label>Show humidity</label></div>
    <button class="toggle" id="tHum" onclick="toggleSet(this,'show_humidity')"></button>
  </div>
  <div class="set-group"><label>Log interval (s)</label>
    <input class="set-input" type="number" id="sLog" min="5" max="120" onchange="saveNum('log_interval',this)">
  </div>
  <div class="set-group"><label>DHT interval (s)</label>
    <input class="set-input" type="number" id="sDht" min="10" max="300" onchange="saveNum('dht_interval',this)">
  </div>
  <div class="set-group"><label>Auto-refresh (s)</label>
    <input class="set-input" type="number" id="sRefresh" min="3" max="30" onchange="saveNum('auto_refresh',this)">
  </div>
  <div class="set-group"><label>CPU alert %</label>
    <input class="set-input" type="number" id="sCpu" min="50" max="100" onchange="saveNum('cpu_alert',this)">
  </div>
  <div class="set-group"><label>Disk alert %</label>
    <input class="set-input" type="number" id="sDisk" min="50" max="100" onchange="saveNum('disk_alert',this)">
  </div>
  <div class="set-group"><label>RAM alert %</label>
    <input class="set-input" type="number" id="sRam" min="50" max="100" onchange="saveNum('ram_alert',this)">
  </div>
  <div class="set-group"><label>Hostname flash (s)</label>
    <input class="set-input" type="number" id="sFlash" min="5" max="60" onchange="saveNum('hostname_flash',this)">
  </div>
  <div class="set-group"><label>LCD contrast</label>
    <input class="set-input" type="number" id="sContrast" min="0" max="100" onchange="saveNum('lcd_contrast',this)">
  </div>
  <div class="drawer-actions">
    <button class="btn-cancel" onclick="testBuzzer()">Test buzzer</button>
    <button class="btn-primary" onclick="exportCsv()">Export CSV</button>
  </div>
</div>

<div class="modal-bg" id="addModal">
  <div class="modal">
    <h2 id="modalTitle">Add node / server</h2>
    <div class="sub" id="modalSub">Another node on an existing host</div>
    <div class="choice">
      <button type="button" id="btnNode" class="active" onclick="setType('node')">New node</button>
      <button type="button" id="btnServer" onclick="setType('server')">New server</button>
    </div>
    <label>Friendly name</label><input id="mName" placeholder="e.g. Precision">
    <label>IP / hostname</label><input id="mIp" placeholder="192.168.x.x">
    <label>Proxmox node name</label><input id="mNode" placeholder="pve">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:9px">
      <div><label>User</label><input id="mUser" value="root@pam"></div>
      <div><label>Password</label><input id="mPass" type="password"></div>
    </div>
    <div class="modal-actions">
      <button class="btn-test" onclick="testConn()">Test connection</button>
      <div class="spacer"></div>
      <button class="btn btn-cancel" onclick="closeAddModal()">Cancel</button>
      <button class="btn btn-save" id="modalSaveBtn" onclick="saveNode()">Add</button>
    </div>
  </div>
</div>

<div class="modal-bg" id="graphModal">
  <div class="modal">
    <h2>Edit Graphs</h2>
    <div class="sub">Toggle which charts are visible</div>
    <div class="set-row"><label>CPU</label><button class="toggle on" id="gCpu" onclick="toggleGraph(this,'cpu')"></button></div>
    <div class="set-row"><label>RAM</label><button class="toggle on" id="gRam" onclick="toggleGraph(this,'ram')"></button></div>
    <div class="set-row"><label>Network</label><button class="toggle on" id="gNet" onclick="toggleGraph(this,'net')"></button></div>
    <div class="set-row"><label>Disk</label><button class="toggle" id="gDisk" onclick="toggleGraph(this,'disk')"></button></div>
    <div class="modal-actions">
      <div class="spacer"></div>
      <button class="btn btn-save" onclick="closeGraphEdit()">Done</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let addType = 'node', editName = null, settings = {}, charts = {};
let graphVisible = {cpu:true, ram:true, net:true, disk:false};
let refreshTimer = null;

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2400);
}
function applyTheme(t) {
  const theme = t === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', theme);
  document.getElementById('themeBtn').textContent = theme === 'dark' ? '☾' : '☀';
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  settings.theme = next;
  fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({theme:next})});
}
function makeChart(id, color) {
  return new Chart(document.getElementById(id), {
    type: 'line',
    data: { labels: [], datasets: [{ data: [], borderColor: color, tension: .35, pointRadius: 0, borderWidth: 2, fill: false }] },
    options: {
      responsive: true, animation: false, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 5, color: '#6b7280', font: {size:10} }, grid: {display:false} },
        y: { ticks: { color: '#6b7280', font: {size:10} }, grid: { color: 'rgba(100,100,100,.15)' } }
      }
    }
  });
}
charts.cpu = makeChart('chartCpu', '#3b82f6');
charts.ram = makeChart('chartRam', '#a855f7');
charts.net = new Chart(document.getElementById('chartNet'), {
  type: 'line',
  data: { labels: [], datasets: [
    { label: 'Down', data: [], borderColor: '#22c55e', tension: .35, pointRadius: 0, borderWidth: 2 },
    { label: 'Up', data: [], borderColor: '#f97316', tension: .35, pointRadius: 0, borderWidth: 2 }
  ]},
  options: {
    responsive: true, animation: false, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { maxTicksLimit: 5, color: '#6b7280', font: {size:10} }, grid: {display:false} },
      y: { ticks: { color: '#6b7280', font: {size:10} }, grid: { color: 'rgba(100,100,100,.15)' } }
    }
  }
});
charts.disk = makeChart('chartDisk', '#eab308');

function openSettings() {
  document.getElementById('drawerBg').classList.add('open');
  document.getElementById('drawer').classList.add('open');
  loadSettings();
}
function closeSettings() {
  document.getElementById('drawerBg').classList.remove('open');
  document.getElementById('drawer').classList.remove('open');
}
async function loadSettings() {
  const r = await fetch('/api/settings');
  settings = await r.json();
  applyTheme(settings.theme || 'dark');
  setToggle('tBuzzer', settings.buzzer_enabled);
  setToggle('tPassive', settings.passive_buzzer_enabled);
  setToggle('tQuiet', settings.quiet_mode);
  setToggle('tCompact', settings.compact_cards);
  setToggle('tHum', settings.show_humidity !== false);
  document.getElementById('sLog').value = settings.log_interval;
  document.getElementById('sDht').value = settings.dht_interval;
  document.getElementById('sRefresh').value = settings.auto_refresh || 5;
  document.getElementById('sCpu').value = settings.cpu_alert;
  document.getElementById('sDisk').value = settings.disk_alert;
  document.getElementById('sRam').value = settings.ram_alert;
  document.getElementById('sFlash').value = settings.hostname_flash;
  document.getElementById('sContrast').value = settings.lcd_contrast;
  graphVisible = settings.graph_visible || graphVisible;
  applyGraphVisibility();
  restartRefresh();
}
function setToggle(id, on) { document.getElementById(id).classList.toggle('on', !!on); }
function toggleSet(el, key) {
  el.classList.toggle('on');
  const val = el.classList.contains('on');
  settings[key] = val;
  fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({[key]: val})});
  if (key === 'compact_cards') load();
}
function saveNum(key, el) {
  const val = parseInt(el.value) || 0;
  settings[key] = val;
  fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({[key]: val})});
  if (key === 'auto_refresh') restartRefresh();
}
async function testBuzzer() {
  await fetch('/api/buzzer/test', {method:'POST'});
  toast('Buzzer test sent');
}
function exportCsv() { window.location = '/api/export/csv'; }
function openAddModal(edit = null) {
  editName = edit;
  document.getElementById('modalTitle').textContent = edit ? 'Edit node' : 'Add node / server';
  document.getElementById('modalSaveBtn').textContent = edit ? 'Save' : 'Add';
  if (!edit) {
    mName.value = mIp.value = mNode.value = mPass.value = '';
    mUser.value = 'root@pam';
    setType('node');
  }
  document.getElementById('addModal').classList.add('open');
}
function closeAddModal() { document.getElementById('addModal').classList.remove('open'); editName = null; }
function setType(t) {
  addType = t;
  btnNode.classList.toggle('active', t === 'node');
  btnServer.classList.toggle('active', t === 'server');
  modalSub.textContent = t === 'node' ? 'Another node on an existing host' : 'Independent Proxmox host';
}
async function testConn() {
  const payload = { name: mName.value, ip: mIp.value, node: mNode.value || 'pve', user: mUser.value, password: mPass.value };
  const r = await fetch('/api/test-connection', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
  const j = await r.json();
  toast(j.ok ? '✓ Connected' : '✗ ' + (j.message || 'Failed'));
}
async function saveNode() {
  const payload = {
    name: mName.value.trim(), ip: mIp.value.trim(),
    node: mNode.value.trim() || mName.value.trim(),
    user: mUser.value.trim() || 'root@pam', password: mPass.value, type: addType
  };
  if (!payload.name || !payload.ip) { toast('Name and IP required'); return; }
  let r;
  if (editName) {
    r = await fetch('/api/nodes/' + encodeURIComponent(editName), {
      method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
  } else {
    r = await fetch('/api/nodes/add', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
  }
  const j = await r.json();
  if (j.ok) { closeAddModal(); load(); toast(editName ? 'Saved' : 'Node added'); }
  else toast('Error: ' + (j.error || 'unknown'));
}
async function deleteNode(name) {
  if (!confirm('Delete "' + name + '"?')) return;
  await fetch('/api/nodes/' + encodeURIComponent(name), {method:'DELETE'});
  load(); toast('Deleted');
}
function openGraphEdit() {
  setToggle('gCpu', graphVisible.cpu);
  setToggle('gRam', graphVisible.ram);
  setToggle('gNet', graphVisible.net);
  setToggle('gDisk', graphVisible.disk);
  document.getElementById('graphModal').classList.add('open');
}
function closeGraphEdit() {
  document.getElementById('graphModal').classList.remove('open');
  fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({graph_visible: graphVisible})});
}
function toggleGraph(el, key) {
  el.classList.toggle('on');
  graphVisible[key] = el.classList.contains('on');
  applyGraphVisibility();
}
function applyGraphVisibility() {
  cCpu.classList.toggle('hidden', !graphVisible.cpu);
  cRam.classList.toggle('hidden', !graphVisible.ram);
  cNet.classList.toggle('hidden', !graphVisible.net);
  cDisk.classList.toggle('hidden', !graphVisible.disk);
}
async function lcdAction(act) {
  const r = await fetch('/api/lcd/' + act, {method:'POST'});
  const j = await r.json();
  if (j.state) lcdMode.textContent = j.state.mode || 'PAGES';
  toast('LCD → ' + act);
}
async function power(name, action) {
  if (!confirm('Really ' + action + ' "' + name + '"?')) return;
  const r = await fetch('/api/power/' + encodeURIComponent(name) + '/' + action, {method:'POST'});
  const j = await r.json();
  toast(j.ok ? action + ' sent' : 'Failed');
}
function restartRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  const sec = (settings.auto_refresh || 5) * 1000;
  refreshTimer = setInterval(load, sec);
}
async function load() {
  const [nodes, logs, lcd] = await Promise.all([
    fetch('/api/current').then(r => r.json()),
    fetch('/api/logs/server?limit=40').then(r => r.json()),
    fetch('/api/lcd').then(r => r.json()).catch(() => null)
  ]);
  const online = nodes.filter(n => n.online == 1).length;
  onlinePill.textContent = online + '/' + nodes.length + ' online';
  const compact = settings.compact_cards;
  const col = document.getElementById('nodesCol');
  col.innerHTML = '';
  nodes.forEach(n => {
    const on = n.online == 1;
    const ramPct = n.ram_total_gb ? (n.ram_used_gb / n.ram_total_gb * 100) : 0;
    const card = document.createElement('div');
    card.className = 'node-card' + (on ? '' : ' offline') + (compact ? ' compact' : '');
    card.innerHTML = `
      <div class="node-head">
        <div>
          <div class="node-name">${n.node_name}</div>
          <div class="node-meta">${n.ip || ''} · ${n.node || ''} · ${n.type || 'server'}</div>
        </div>
        <span class="badge ${on ? 'badge-on' : 'badge-off'}">${on ? 'ONLINE' : 'OFFLINE'}</span>
      </div>
      <div class="stat-row"><span>CPU</span><span>${(n.cpu_usage||0).toFixed(1)}%</span></div>
      <div class="bar"><div style="width:${n.cpu_usage||0}%;background:var(--accent)"></div></div>
      <div class="stat-row"><span>RAM</span><span>${(n.ram_used_gb||0).toFixed(1)} / ${(n.ram_total_gb||0).toFixed(1)} GB</span></div>
      <div class="bar"><div style="width:${ramPct}%;background:var(--purple)"></div></div>
      <div class="mini-stats">
        <div class="mini">DISK<strong>${(n.disk_pct||0).toFixed(1)}%</strong></div>
        <div class="mini">VMS<strong>${n.active_vms||0}</strong></div>
        <div class="mini">NET<strong>${(n.net_in_kbps||0).toFixed(0)} ↓</strong></div>
      </div>
      <div class="actions">
        <button class="btn btn-reboot" ${on?'':'disabled'} onclick="power('${n.node_name}','reboot')">↻ Reboot</button>
        <button class="btn btn-shutdown" ${on?'':'disabled'} onclick="power('${n.node_name}','shutdown')">⏻ Shutdown</button>
        <button class="btn-icon" onclick="openEdit('${n.node_name}','${n.ip||''}','${n.node||''}','${n.type||'server'}')" title="Edit">✎</button>
        <button class="btn-icon" onclick="deleteNode('${n.node_name}')" title="Delete">🗑</button>
      </div>`;
    col.appendChild(card);
  });
  if (lcd && lcd.lines) {
    lcdPreview.innerHTML = (lcd.lines[0] || '').padEnd(16).replace(/ /g,'&nbsp;') + '<br>' +
                           (lcd.lines[1] || '').padEnd(16).replace(/ /g,'&nbsp;');
    lcdMode.textContent = lcd.mode || 'PAGES';
    if (lcd.humidity != null) humBadge.textContent = 'Humidity: ' + Math.round(lcd.humidity) + '%';
    if (lcd.alerting) {
      alertStatus.textContent = '⚠ Threshold exceeded — LCD + pin 20 firing';
      alertStatus.classList.add('active');
    } else {
      alertStatus.textContent = 'Quiet. Thresholds fire on the LCD and pin 20.';
      alertStatus.classList.remove('active');
    }
  } else if (nodes.length) {
    const n = nodes[0];
    lcdPreview.innerHTML = `CPU: ${(n.cpu_usage||0).toFixed(1)}%&nbsp;&nbsp;&nbsp;&nbsp;<br>RAM: ${(n.ram_used_gb||0).toFixed(1)}/${(n.ram_total_gb||0).toFixed(1)}G`;
  }
  const times = logs.map(x => (x.timestamp||'').split(' ')[1] || '');
  charts.cpu.data.labels = times; charts.cpu.data.datasets[0].data = logs.map(x => x.cpu_usage); charts.cpu.update('none');
  charts.ram.data.labels = times; charts.ram.data.datasets[0].data = logs.map(x => x.ram_used_gb); charts.ram.update('none');
  charts.net.data.labels = times;
  charts.net.data.datasets[0].data = logs.map(x => x.net_in_kbps || 0);
  charts.net.data.datasets[1].data = logs.map(x => x.net_out_kbps || 0);
  charts.net.update('none');
  charts.disk.data.labels = times; charts.disk.data.datasets[0].data = logs.map(x => x.disk_pct || 0); charts.disk.update('none');
}
function openEdit(name, ip, node, type) {
  openAddModal(name);
  mName.value = name; mIp.value = ip; mNode.value = node;
  setType(type === 'node' ? 'node' : 'server');
}
loadSettings().then(() => load());
</script>
</body>
</html>"""
