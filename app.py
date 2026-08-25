import csv
import json
import io
import os
import sqlite3
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

import config
import database
from monitor import ProxmoxManager, NodeClient

app = FastAPI(title="PVE Node Monitor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB = "monitor.db"
BUZZER_TEST_FLAG = "/tmp/pve_buzzer_test"

lcd_state = {
    "page": 0, "in_settings": False, "settings_idx": 0,
    "force_flash": False, "last_lines": ("", ""), "mode": "PAGES",
    "alerting": False,
}

def get_db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

# -------------------- API (same as before) --------------------

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
        if r["node_name"] not in valid: continue
        d = dict(r)
        ncfg = node_map.get(r["node_name"], {})
        d["ip"] = ncfg.get("ip", ""); d["node"] = ncfg.get("node", ""); d["type"] = ncfg.get("type", "server")
        d["note"] = ncfg.get("note", ""); d["favorite"] = ncfg.get("favorite", False)
        d["cpu_alert"] = ncfg.get("cpu_alert"); d["ram_alert"] = ncfg.get("ram_alert"); d["disk_alert"] = ncfg.get("disk_alert")
        d["tags"] = ncfg.get("tags") or []
        out.append(d)
    out.sort(key=lambda x: (0 if x.get("favorite") else 1, x.get("node_name") or ""))
    return out

@app.get("/api/logs/server")
def api_logs(limit: int = 40, node: str = None):
    conn = get_db()
    if node:
        rows = conn.execute("SELECT timestamp, node_name, cpu_usage, ram_used_gb, disk_pct, net_in_kbps, net_out_kbps FROM server_logs WHERE node_name=? ORDER BY id DESC LIMIT ?", (node, limit)).fetchall()
    else:
        rows = conn.execute("SELECT timestamp, node_name, cpu_usage, ram_used_gb, disk_pct, net_in_kbps, net_out_kbps FROM server_logs WHERE node_name IS NOT NULL AND node_name!='' AND node_name!='null' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]

@app.post("/api/power/{node_name}/{action}")
def api_power(node_name: str, action: str):
    if action not in ("shutdown", "reboot"):
        return JSONResponse({"ok": False, "error": "invalid"}, 400)
    cfg = config.load_config()
    mgr = ProxmoxManager(cfg["nodes"])
    client = mgr.get_client(node_name)
    if not client: return JSONResponse({"ok": False, "error": "not found"}, 404)
    ok = client.power(action)
    try:
        database.log_activity(action, node_name, "web")
    except Exception:
        pass
    return {"ok": ok}

@app.post("/api/nodes/add")
async def api_add_node(request: Request):
    data = await request.json()
    cfg = config.load_config()
    new = {"name": data.get("name","").strip(), "ip": data.get("ip","").strip(),
           "node": data.get("node","").strip() or data.get("name","").strip(),
           "user": data.get("user","root@pam").strip(), "password": data.get("password",""),
           "type": data.get("type","server")}
    if not new["name"] or not new["ip"]:
        return JSONResponse({"ok": False, "error": "name and ip required"}, 400)
    if any(n["name"]==new["name"] for n in cfg["nodes"]):
        return JSONResponse({"ok": False, "error": "name already exists"}, 400)
    cfg["nodes"].append(new); config.save_config(cfg)
    try:
        database.log_activity("add_node", new["name"], "web")
    except Exception:
        pass
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
            if data.get("password"): n["password"] = data["password"]
            n["type"] = data.get("type", n.get("type","server"))
            config.save_config(cfg); return {"ok": True}
    return JSONResponse({"ok": False, "error": "not found"}, 404)

@app.delete("/api/nodes/{name}")
def api_delete_node(name: str):
    cfg = config.load_config()
    cfg["nodes"] = [n for n in cfg["nodes"] if n["name"] != name]
    config.save_config(cfg)
    try:
        database.log_activity("delete_node", name, "web")
    except Exception:
        pass
    return {"ok": True}

@app.post("/api/test-connection")
async def api_test_connection(request: Request):
    data = await request.json()
    client = NodeClient(data.get("name","test"), data.get("ip",""), data.get("node","pve"),
                        data.get("user","root@pam"), data.get("password",""))
    return client.test_connection()

@app.get("/api/settings")
def api_get_settings():
    cfg = config.load_config()
    keys = ["buzzer_enabled","passive_buzzer_enabled","quiet_mode","compact_cards","flash_hostname",
            "log_interval","cpu_alert","disk_alert","ram_alert","theme","graph_visible","graph_range",
            "auto_refresh","standalone","has_lcd","has_touch","has_active_buzzer","has_passive_buzzer",
            "show_net_on_card","confirm_power","alert_repeat_sec","flash_interval","density","accent"]
    return {k: cfg.get(k) for k in keys}

@app.post("/api/settings")
async def api_save_settings(request: Request):
    data = await request.json()
    cfg = config.load_config()
    for k, v in data.items():
        cfg[k] = v
    config.save_config(cfg)
    return {"ok": True}

@app.post("/api/buzzer/test")
def api_test_buzzer():
    open(BUZZER_TEST_FLAG, "w").close()
    return {"ok": True}

@app.get("/api/export/csv")
def api_export_csv():
    conn = get_db()
    rows = conn.execute("SELECT timestamp,node_name,cpu_usage,ram_used_gb,ram_total_gb,disk_pct,net_in_kbps,net_out_kbps,active_vms,online FROM server_logs ORDER BY id DESC LIMIT 2000").fetchall()
    conn.close()
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["timestamp","node","cpu%","ram_used_gb","ram_total_gb","disk%","net_in_kbps","net_out_kbps","vms","online"])
    for r in rows: writer.writerow([r[c] for c in r.keys()])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition":"attachment; filename=pve_monitor_export.csv"})

@app.get("/api/lcd")
def api_lcd_state():
    return {"lines": lcd_state["last_lines"], "mode": lcd_state["mode"], "page": lcd_state["page"],
            "in_settings": lcd_state["in_settings"], "alerting": lcd_state.get("alerting", False)}

@app.post("/api/lcd/{action}")
def api_lcd_action(action: str):
    if action == "page":
        lcd_state["page"] = (lcd_state["page"] + 1) % 5; lcd_state["in_settings"] = False; lcd_state["mode"] = "PAGES"
    elif action == "settings":
        lcd_state["in_settings"] = True; lcd_state["settings_idx"] = 0; lcd_state["mode"] = "SETTINGS"
    elif action == "change":
        if lcd_state["in_settings"]: lcd_state["settings_idx"] = (lcd_state["settings_idx"] + 1) % 7
        else: lcd_state["force_flash"] = True; lcd_state["mode"] = "FLASH"
    elif action == "exit":
        lcd_state["in_settings"] = False; lcd_state["mode"] = "PAGES"
    return {"ok": True, "state": lcd_state}



@app.post("/api/prune")
def api_prune(days: int = 14):
    from database import prune_old_logs
    n = prune_old_logs(days)
    return {"ok": True, "deleted": n}


@app.get("/api/discover")
def api_discover():
    """Scan local /24 for open Proxmox ports (8006). Best-effort, may take a few seconds."""
    import socket
    from concurrent.futures import ThreadPoolExecutor, as_completed
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        my_ip = s.getsockname()[0]
        s.close()
        base = ".".join(my_ip.split(".")[:3]) + "."
    except Exception:
        return {"ok": False, "nodes": [], "error": "no local ip"}
    def check(i):
        ip = base + str(i)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.35)
            r = sock.connect_ex((ip, 8006))
            sock.close()
            if r == 0:
                return ip
        except Exception:
            pass
        return None
    with ThreadPoolExecutor(max_workers=40) as ex:
        futs = [ex.submit(check, i) for i in range(1, 255)]
        for f in as_completed(futs):
            ip = f.result()
            if ip:
                found.append({"ip": ip, "name": "pve-" + ip.split(".")[-1]})
    return {"ok": True, "nodes": found}

@app.get("/api/health")
def api_health():
    cfg = config.load_config()
    return {
        "ok": True,
        "version": "1.7.0",
        "nodes": len(cfg.get("nodes", [])),
        "standalone": cfg.get("standalone", False),
        "setup_done": cfg.get("setup_done", False),
    }

@app.get("/api/alerts")
def api_alerts(limit: int = 50, unacked: int = 0):
    return database.get_alerts(limit=limit, unacked_only=bool(unacked))

@app.post("/api/alerts/ack")
async def api_ack_alerts(request: Request):
    data = await request.json() if request.headers.get("content-type","").startswith("application/json") else {}
    database.ack_alert(alert_id=data.get("id"), node_name=data.get("node"))
    # also silence hardware via flag
    try:
        open("/tmp/pve_alert_ack", "w").close()
    except Exception:
        pass
    database.log_activity("ack_alert", data.get("node") or "all", "web")
    return {"ok": True}

@app.get("/api/activity")
def api_activity(limit: int = 40):
    return database.get_activity(limit)

@app.get("/api/logs/range")
def api_logs_range(range: str = "1h", node: str = None):
    # approximate: 1h≈360 samples at 10s, 6h≈2160, 24h≈8640 — cap for perf
    limits = {"1h": 120, "6h": 400, "24h": 900, "7d": 2000}
    limit = limits.get(range, 120)
    conn = get_db()
    if node:
        rows = conn.execute("""
            SELECT timestamp, node_name, cpu_usage, ram_used_gb, disk_pct, net_in_kbps, net_out_kbps
            FROM server_logs WHERE node_name=? ORDER BY id DESC LIMIT ?
        """, (node, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT timestamp, node_name, cpu_usage, ram_used_gb, disk_pct, net_in_kbps, net_out_kbps
            FROM server_logs WHERE node_name IS NOT NULL AND node_name!='' 
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]

@app.get("/api/config/backup")
def api_config_backup():
    cfg = config.load_config()
    # redact passwords partially
    safe = dict(cfg)
    safe_nodes = []
    for n in cfg.get("nodes", []):
        nn = dict(n)
        if nn.get("password"):
            nn["password"] = "***"
        safe_nodes.append(nn)
    safe["nodes"] = safe_nodes
    return safe

@app.post("/api/config/restore")
async def api_config_restore(request: Request):
    data = await request.json()
    cfg = config.load_config()
    # merge non-sensitive
    for k in ("log_interval", "theme", "density", "cpu_alert", "ram_alert", "disk_alert",
              "graph_visible", "graph_range", "auto_refresh", "quiet_mode", "flash_hostname",
              "alert_repeat_sec", "confirm_power", "show_net_on_card", "accent"):
        if k in data:
            cfg[k] = data[k]
    config.save_config(cfg)
    database.log_activity("config_restore", "settings restored", "web")
    return {"ok": True}



@app.post("/api/nodes/{name}/meta")
async def api_node_meta(name: str, request: Request):
    data = await request.json()
    cfg = config.load_config()
    for n in cfg["nodes"]:
        if n["name"] == name:
            if "note" in data: n["note"] = str(data["note"])[:200]
            if "favorite" in data: n["favorite"] = bool(data["favorite"])
            if "tags" in data: n["tags"] = data["tags"] if isinstance(data["tags"], list) else []
            for k in ("cpu_alert", "ram_alert", "disk_alert"):
                if k in data:
                    v = data[k]
                    n[k] = None if v is None or v == "" else float(v)
            config.save_config(cfg)
            database.log_activity("node_meta", name, "web")
            return {"ok": True}
    return JSONResponse({"ok": False, "error": "not found"}, 404)


@app.get("/api/nodes/{name}/guests")
def api_guests(name: str):
    cfg = config.load_config()
    mgr = ProxmoxManager(cfg["nodes"])
    client = mgr.get_client(name)
    if not client:
        return JSONResponse({"ok": False, "error": "not found"}, 404)
    return {"ok": True, "guests": client.list_guests()}

@app.post("/api/nodes/{name}/guests/{vmid}/{action}")
def api_guest_power(name: str, vmid: int, action: str, kind: str = "qemu"):
    cfg = config.load_config()
    mgr = ProxmoxManager(cfg["nodes"])
    client = mgr.get_client(name)
    if not client:
        return JSONResponse({"ok": False, "error": "not found"}, 404)
    ok = client.guest_power(vmid, kind, action)
    try:
        database.log_activity(f"guest_{action}", f"{name} {kind}/{vmid}", "web")
    except Exception:
        pass
    return {"ok": ok}

@app.post("/api/power/bulk")
async def api_power_bulk(request: Request):
    data = await request.json()
    action = data.get("action")
    names = data.get("nodes") or []
    if action not in ("shutdown", "reboot") or not names:
        return JSONResponse({"ok": False, "error": "invalid"}, 400)
    cfg = config.load_config()
    mgr = ProxmoxManager(cfg["nodes"])
    results = {}
    for name in names:
        client = mgr.get_client(name)
        if not client:
            results[name] = False
            continue
        results[name] = client.power(action)
        try:
            database.log_activity(f"bulk_{action}", name, "web")
        except Exception:
            pass
    return {"ok": True, "results": results}



@app.post("/api/nodes/import")
async def api_import_nodes(request: Request):
    data = await request.json()
    nodes_in = data.get("nodes") or []
    if not isinstance(nodes_in, list) or not nodes_in:
        return JSONResponse({"ok": False, "error": "nodes array required"}, 400)
    cfg = config.load_config()
    existing = {n["name"] for n in cfg["nodes"]}
    added = 0
    for raw in nodes_in:
        name = (raw.get("name") or "").strip()
        ip = (raw.get("ip") or "").strip()
        if not name or not ip or name in existing:
            continue
        cfg["nodes"].append({
            "name": name,
            "ip": ip,
            "node": (raw.get("node") or name).strip(),
            "user": (raw.get("user") or "root@pam").strip(),
            "password": raw.get("password") or "",
            "type": raw.get("type") or "server",
            "note": raw.get("note") or "",
            "favorite": bool(raw.get("favorite", False)),
            "tags": raw.get("tags") or [],
            "cpu_alert": raw.get("cpu_alert"),
            "ram_alert": raw.get("ram_alert"),
            "disk_alert": raw.get("disk_alert"),
        })
        existing.add(name)
        added += 1
    config.save_config(cfg)
    try:
        database.log_activity("import_nodes", f"added {added}", "web")
    except Exception:
        pass
    return {"ok": True, "added": added}

@app.get("/api/export/json")
def api_export_json(range: str = "1h"):
    limits = {"1h": 120, "6h": 400, "24h": 900, "7d": 2000}
    limit = limits.get(range, 120)
    conn = get_db()
    rows = conn.execute("""
        SELECT timestamp, node_name, cpu_usage, ram_used_gb, ram_total_gb,
               disk_pct, net_in_kbps, net_out_kbps, active_vms, online
        FROM server_logs ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


# -------------------- SETUP FLOW --------------------

@app.get("/setup", response_class=HTMLResponse)
def setup_page():
    cfg = config.load_config()
    if cfg.get("setup_done") and cfg.get("pins_done") and cfg.get("nodes"):
        return RedirectResponse("/", status_code=303)
    if cfg.get("setup_done") and not cfg.get("pins_done"):
        return RedirectResponse("/setup/pins", status_code=303)
    return SETUP_HTML

@app.post("/setup")
async def setup_submit(request: Request):
    form = await request.form()
    nodes = []
    i = 0
    while True:
        name = form.get(f"name_{i}")
        if not name: break
        nodes.append({
            "name": name.strip(), "ip": form.get(f"ip_{i}","").strip(),
            "node": form.get(f"node_{i}", name).strip(),
            "user": form.get(f"user_{i}","root@pam").strip(),
            "password": form.get(f"password_{i}",""), "type": "server",
        })
        i += 1
    if not nodes:
        return HTMLResponse("Need at least one node", 400)
    cfg = config.load_config()
    cfg["nodes"] = nodes
    cfg["setup_done"] = True
    cfg["log_interval"] = int(form.get("log_interval", 10))
    cfg["theme"] = "dark"
    config.save_config(cfg)
    return RedirectResponse("/setup/pins", status_code=303)

@app.get("/setup/pins", response_class=HTMLResponse)
def setup_pins_page():
    cfg = config.load_config()
    if not cfg.get("setup_done"):
        return RedirectResponse("/setup")
    if cfg.get("pins_done"):
        return RedirectResponse("/")
    return PINS_HTML

@app.post("/setup/pins")
async def setup_pins_submit(request: Request):
    form = await request.form()
    cfg = config.load_config()
    cfg["has_lcd"] = form.get("has_lcd") == "on"
    cfg["has_touch"] = form.get("has_touch") == "on"
    cfg["has_active_buzzer"] = form.get("has_active_buzzer") == "on"
    cfg["has_passive_buzzer"] = form.get("has_passive_buzzer") == "on"
    cfg["buzzer_enabled"] = cfg["has_active_buzzer"]
    cfg["passive_buzzer_enabled"] = cfg["has_passive_buzzer"]
    # standalone if nothing selected
    any_hw = any([cfg["has_lcd"], cfg["has_touch"], cfg["has_active_buzzer"],
                  cfg["has_passive_buzzer"]])
    cfg["standalone"] = not any_hw or form.get("standalone") == "on"
    for k in ("gpio_touch","gpio_active_buzzer","gpio_passive_buzzer",
              "lcd_rs","lcd_en","lcd_d4","lcd_d5","lcd_d6","lcd_d7"):
        try: cfg[k] = int(form.get(k, cfg.get(k, 0)))
        except: pass
    cfg["lcd_mode"] = form.get("lcd_mode", "parallel")
    cfg["lcd_i2c_addr"] = form.get("lcd_i2c_addr", "0x27")
    cfg["pins_done"] = True
    config.save_config(cfg)
    return RedirectResponse("/", status_code=303)

@app.get("/", response_class=HTMLResponse)
def dashboard():
    cfg = config.load_config()
    if not cfg.get("setup_done") or not cfg.get("nodes"):
        return RedirectResponse("/setup", status_code=303)
    if cfg.get("setup_done") and not cfg.get("pins_done"):
        return RedirectResponse("/setup/pins", status_code=303)
    return DASHBOARD_HTML("/setup", status_code=303)
    if cfg.get("setup_done") and not cfg.get("pins_done"):
        return RedirectResponse("/setup/pins", status_code=303)
    return DASHBOARD_HTML("/setup")
    if not cfg.get("pins_done"):
        return RedirectResponse("/setup/pins")
    return DASHBOARD_HTML

# -------------------- HTML --------------------

SETUP_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f172a">
<meta name="apple-mobile-web-app-capable" content="yes">

<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor – Setup</title>
<style>
:root{--bg:#0a0a0a;--card:#141414;--text:#f3f4f6;--muted:#9ca3af;--accent:#3b82f6;--border:#262626}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;padding:24px}
.card{background:var(--card);border-radius:16px;padding:28px;max-width:520px;width:100%;border:1px solid var(--border)}
h1{margin:0 0 4px;font-size:1.4rem;font-weight:700}.sub{color:var(--muted);margin-bottom:20px;font-size:.88rem}
label{display:block;margin:12px 0 5px;font-size:.82rem;color:var(--muted);font-weight:500}
input,select{width:100%;padding:9px 11px;border-radius:9px;border:1px solid var(--border);background:#0a0a0a;color:var(--text);font-size:.92rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.btn{margin-top:20px;width:100%;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-weight:600;font-size:1rem;cursor:pointer}
.node-block{border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:12px}
.add{background:transparent;border:1px dashed #4b5563;color:var(--muted);width:100%;padding:9px;border-radius:9px;cursor:pointer;margin-top:6px}

@media (max-width: 780px) {
  #bottomNav { display: flex !important; }
  body { padding-bottom: 56px; }

  .layout { flex-direction: column; }
  #nodesCol, .side { width: 100% !important; }
  .node-card { margin-bottom: 10px; }
  .actions { flex-wrap: wrap; }
  .btn-reboot, .btn-shutdown { flex: 1 1 40%; }
  .modal-box { margin: 12px; max-width: calc(100vw - 24px); }
  .mini-stats { gap: 6px; }
}


.node-card.stale .badge-on { animation: pulse-red 1.4s infinite; }
@keyframes pulse-red {
  0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,.6); }
  50%     { box-shadow: 0 0 0 6px rgba(239,68,68,0); }
}
.node-card.stale .node-meta:last-of-type { color: #f87171 !important; }


/* Theme packs */
body.theme-cyberpunk { --accent:#ff2a6d; --purple:#05d9e8; --bg:#0d0221; --card:#1a0a2e; }
body.theme-terminal  { --accent:#33ff33; --purple:#33ff99; --bg:#0a0a0a; --card:#111; color:#33ff33; }
body.theme-nord      { --accent:#88c0d0; --purple:#b48ead; --bg:#2e3440; --card:#3b4252; }
body.cinema .topbar, body.cinema .side, body.cinema #nodesCol { display:none !important; }
body.cinema .graphs { position:fixed; inset:0; z-index:50; background:var(--bg); padding:12px; }


body.guest-mode .actions button:not(.btn-icon),
body.guest-mode .btn-reboot,
body.guest-mode .btn-shutdown { display: none !important; }
body.guest-mode #bottomNav { display: none !important; }

body.setup-page #alertHistoryModal,
body.setup-page #qrModal,
body.setup-page #pinOverlay,
body.setup-page #bottomNav,
body.setup-page #quickBar,
body.setup-page .footer-diag {
  display: none !important;
}

body.setup-page #alertHistoryModal,
body.setup-page #qrModal,
body.setup-page #pinOverlay,
body.setup-page #bottomNav,
body.setup-page #quickBar,
body.setup-page .footer-diag,
body.setup-page a[onclick*="copyDiagnostics"],
body.setup-page a[onclick*="showQr"] {
  display: none !important;
}
/* also hide the floating version text */
body.setup-page > div:last-of-type {
  display: none !important;
}


/* hide phase4 extras on setup */
body:not(.dashboard) #pinOverlay,
body:not(.dashboard) #qrModal,
body:not(.dashboard) #alertHistoryModal,
body:not(.dashboard) #bottomNav,
body:not(.dashboard) #quickBar,
body:not(.dashboard) a[onclick*="copyDiagnostics"],
body:not(.dashboard) a[onclick*="showQr"] {
  display: none !important;
}

</style>
</head><body class=\"setup-page\">
<div class="card">
<h1>PVE Node Monitor</h1>
<p class="sub">Step 1 · Add your Proxmox nodes</p>
<form method="post" action="/setup">
<div id="nodes">
<div class="node-block">
<label>Friendly name</label><input name="name_0" value="Precision" required>
<label>IP / Hostname</label><input name="ip_0" required>
<label>Proxmox node name</label><input name="node_0" value="pve" required>
<div class="row"><div><label>User</label><input name="user_0" value="root@pam"></div>
<div><label>Password</label><input name="password_0" type="password" required></div></div>
</div></div>
<button type="button" class="add" onclick="addNode()">+ Add another</button>
<label style="margin-top:18px">Log interval (s)</label>
<select name="log_interval"><option>5</option><option selected>10</option><option>30</option><option>60</option></select>
<button class="btn" type="submit">Proceed to Pin Layout →</button>
</form></div>
<script>
let idx=1;
function addNode(){document.getElementById('nodes').insertAdjacentHTML('beforeend',`
<div class="node-block">
<label>Friendly name</label><input name="name_${idx}" required>
<label>IP / Hostname</label><input name="ip_${idx}" required>
<label>Proxmox node name</label><input name="node_${idx}" required>
<div class="row"><div><label>User</label><input name="user_${idx}" value="root@pam"></div>
<div><label>Password</label><input name="password_${idx}" type="password" required></div></div></div>`);idx++}
</script>
<div id="quickBar" style="display:none;position:fixed;bottom:18px;left:50%;transform:translateX(-50%);
  background:var(--card);border:1px solid var(--border);border-radius:12px;padding:10px 16px;
  box-shadow:0 8px 30px rgba(0,0,0,.45);z-index:100;gap:10px;align-items:center">
  <span id="quickCount" style="font-size:.85rem;opacity:.8">0 selected</span>
  <button class="btn-reboot" style="padding:6px 12px" onclick="quickPower('reboot')">Reboot</button>
  <button class="btn-shutdown" style="padding:6px 12px" onclick="quickPower('shutdown')">Shutdown</button>
  <button class="btn btn-ghost" style="padding:6px 10px" onclick="clearQuick()">Clear</button>
</div>


<div id="alertHistoryModal" class="modal">
  <div class="modal-box" style="max-width:480px;max-height:80vh;overflow:auto">
    <h3>Alert history</h3>
    <div id="alertHistoryList" style="font-size:.85rem"></div>
    <button class="btn btn-ghost" style="margin-top:12px" onclick="closeAlertHistory()">Close</button>
  </div>
</div>


<div id="pinOverlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:9999;
  align-items:center;justify-content:center;flex-direction:column;gap:12px">
  <div style="font-size:1.1rem;opacity:.8">Enter PIN</div>
  <input id="pinInput" type="password" maxlength="8" style="font-size:1.4rem;text-align:center;width:140px;
    padding:10px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:inherit"
    onkeydown="if(event.key==='Enter')checkPin()">
  <button class="btn btn-primary" onclick="checkPin()">Unlock</button>
  <div id="pinErr" style="color:#f87171;font-size:.85rem;display:none">Wrong PIN</div>
</div>


<div style="text-align:center;padding:16px 8px 24px;opacity:.55;font-size:.75rem">
  PVE Node Monitor <span id="verLabel">v1.9.0</span> ·
  <a href="#" onclick="copyDiagnostics();return false" style="color:var(--accent)">Copy diagnostics</a> ·
  <a href="#" onclick="showQr();return false" style="color:var(--accent)">QR</a>
</div>
<div id="qrModal" class="modal">
  <div class="modal-box" style="text-align:center;max-width:280px">
    <h3>Open on phone</h3>
    <div id="qrBox" style="margin:12px auto"></div>
    <button class="btn btn-ghost" onclick="document.getElementById('qrModal').classList.remove('open')">Close</button>
  </div>
</div>


<nav id="bottomNav" style="display:none;position:fixed;bottom:0;left:0;right:0;background:var(--card);
  border-top:1px solid var(--border);padding:8px 0;z-index:40;justify-content:space-around">
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="window.scrollTo(0,0)">Nodes</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="openSettings()">Settings</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="openAddModal()">Add</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="toggleCinema()">Cinema</button>
</nav>

</body></html>"""

PINS_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f172a">
<meta name="apple-mobile-web-app-capable" content="yes">

<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor – Pin Layout</title>
<style>
:root{--bg:#0a0a0a;--card:#141414;--text:#f3f4f6;--muted:#9ca3af;--accent:#3b82f6;--border:#262626}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;padding:24px}
.card{background:var(--card);border-radius:16px;padding:28px;max-width:540px;width:100%;border:1px solid var(--border)}
h1{margin:0 0 4px;font-size:1.4rem;font-weight:700}.sub{color:var(--muted);margin-bottom:20px;font-size:.88rem}
label{display:block;margin:10px 0 4px;font-size:.82rem;color:var(--muted);font-weight:500}
input,select{width:100%;padding:9px 11px;border-radius:9px;border:1px solid var(--border);background:#0a0a0a;color:var(--text);font-size:.92rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.btn{margin-top:22px;width:100%;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-weight:600;font-size:1rem;cursor:pointer}
.check-row{display:flex;align-items:center;gap:10px;margin:10px 0;padding:8px 0;border-bottom:1px solid var(--border)}
.check-row input{width:18px;height:18px;flex-shrink:0}
.check-row label{margin:0;color:var(--text);font-size:.92rem}
.section{margin-top:18px}.section h3{font-size:.95rem;margin-bottom:8px}
.pin-fields{display:none}.pin-fields.show{display:block}
.hint{font-size:.75rem;color:var(--muted);margin-top:4px}
.dim{opacity:.35;pointer-events:none;filter:grayscale(1)}

@media (max-width: 780px) {
  #bottomNav { display: flex !important; }
  body { padding-bottom: 56px; }

  .layout { flex-direction: column; }
  #nodesCol, .side { width: 100% !important; }
  .node-card { margin-bottom: 10px; }
  .actions { flex-wrap: wrap; }
  .btn-reboot, .btn-shutdown { flex: 1 1 40%; }
  .modal-box { margin: 12px; max-width: calc(100vw - 24px); }
  .mini-stats { gap: 6px; }
}


.node-card.stale .badge-on { animation: pulse-red 1.4s infinite; }
@keyframes pulse-red {
  0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,.6); }
  50%     { box-shadow: 0 0 0 6px rgba(239,68,68,0); }
}
.node-card.stale .node-meta:last-of-type { color: #f87171 !important; }


/* Theme packs */
body.theme-cyberpunk { --accent:#ff2a6d; --purple:#05d9e8; --bg:#0d0221; --card:#1a0a2e; }
body.theme-terminal  { --accent:#33ff33; --purple:#33ff99; --bg:#0a0a0a; --card:#111; color:#33ff33; }
body.theme-nord      { --accent:#88c0d0; --purple:#b48ead; --bg:#2e3440; --card:#3b4252; }
body.cinema .topbar, body.cinema .side, body.cinema #nodesCol { display:none !important; }
body.cinema .graphs { position:fixed; inset:0; z-index:50; background:var(--bg); padding:12px; }


body.guest-mode .actions button:not(.btn-icon),
body.guest-mode .btn-reboot,
body.guest-mode .btn-shutdown { display: none !important; }
body.guest-mode #bottomNav { display: none !important; }

body.setup-page #alertHistoryModal,
body.setup-page #qrModal,
body.setup-page #pinOverlay,
body.setup-page #bottomNav,
body.setup-page #quickBar,
body.setup-page .footer-diag {
  display: none !important;
}

body.setup-page #alertHistoryModal,
body.setup-page #qrModal,
body.setup-page #pinOverlay,
body.setup-page #bottomNav,
body.setup-page #quickBar,
body.setup-page .footer-diag,
body.setup-page a[onclick*="copyDiagnostics"],
body.setup-page a[onclick*="showQr"] {
  display: none !important;
}
/* also hide the floating version text */
body.setup-page > div:last-of-type {
  display: none !important;
}


/* hide phase4 extras on setup */
body:not(.dashboard) #pinOverlay,
body:not(.dashboard) #qrModal,
body:not(.dashboard) #alertHistoryModal,
body:not(.dashboard) #bottomNav,
body:not(.dashboard) #quickBar,
body:not(.dashboard) a[onclick*="copyDiagnostics"],
body:not(.dashboard) a[onclick*="showQr"] {
  display: none !important;
}

</style>
</head><body class=\"setup-page\">
<div class="card">
<h1>Pin Layout</h1>
<p class="sub">Step 2 · Components & GPIO pins</p>
<form method="post" action="/setup/pins">
<div class="check-row"><input type="checkbox" name="standalone" id="standalone" onchange="togStand()"><label for="standalone">Standalone web UI only (no hardware)</label></div>
<div id="hwSection">
<div class="section"><h3>Components installed</h3>
<div class="check-row"><input type="checkbox" name="has_lcd" id="has_lcd" checked onchange="sync()"><label for="has_lcd">1602 LCD</label></div>
<div class="check-row"><input type="checkbox" name="has_touch" id="has_touch" checked onchange="sync()"><label for="has_touch">Touch sensor</label></div>
<div class="check-row"><input type="checkbox" name="has_active_buzzer" id="has_active_buzzer" checked onchange="sync()"><label for="has_active_buzzer">Active buzzer (clicks)</label></div>
<div class="check-row"><input type="checkbox" name="has_passive_buzzer" id="has_passive_buzzer" checked onchange="sync()"><label for="has_passive_buzzer">Passive buzzer (alert tones)</label></div>
</div>
<div class="section" id="gpioSection"><h3>GPIO pins (BCM)</h3>
<div class="row" id="rowTouch"><div><label>Touch</label><input name="gpio_touch" type="number" value="27"></div>
<div id="rowAct"><label>Active buzzer</label><input name="gpio_active_buzzer" type="number" value="6"></div></div>
<div class="row" id="rowPas"><div><label>Passive buzzer</label><input name="gpio_passive_buzzer" type="number" value="16"></div><div></div></div>
</div>
<div class="section" id="lcdSection"><h3>LCD connection</h3>
<label>Mode</label>
<select name="lcd_mode" id="lcd_mode" onchange="togMode()">
<option value="parallel" selected>Parallel 4-bit (RS/EN/D4-D7)</option>
<option value="i2c">I2C backpack (4 pins)</option>
</select>
<div class="hint">I2C backpack = PCF8574-style module</div>
<div id="parallelBox" class="pin-fields show">
<div class="row" style="margin-top:10px">
<div><label>RS</label><input name="lcd_rs" type="number" value="22"></div>
<div><label>EN</label><input name="lcd_en" type="number" value="17"></div>
</div>
<div class="row">
<div><label>D4</label><input name="lcd_d4" type="number" value="25"></div>
<div><label>D5</label><input name="lcd_d5" type="number" value="24"></div>
</div>
<div class="row">
<div><label>D6</label><input name="lcd_d6" type="number" value="23"></div>
<div><label>D7</label><input name="lcd_d7" type="number" value="18"></div>
</div>
</div>
<div id="i2cBox" class="pin-fields">
<label style="margin-top:10px">I2C address</label>
<input name="lcd_i2c_addr" value="0x27">
<div class="hint">Common: 0x27 or 0x3F</div>
</div>
</div>
</div>
<button class="btn" type="submit">Save & Start</button>
</form></div>
<script>
function togStand(){const s=standalone.checked;hwSection.classList.toggle('dim',s)}
function sync(){
  lcdSection.classList.toggle('dim',!has_lcd.checked);
  rowTouch.classList.toggle('dim',!has_touch.checked);
  rowAct.classList.toggle('dim',!has_active_buzzer.checked);
  rowPas.classList.toggle('dim',!has_passive_buzzer.checked);
}
function togMode(){
  const i2c=lcd_mode.value==='i2c';
  parallelBox.classList.toggle('show',!i2c);
  i2cBox.classList.toggle('show',i2c);
}
sync();
</script>
<div id="quickBar" style="display:none;position:fixed;bottom:18px;left:50%;transform:translateX(-50%);
  background:var(--card);border:1px solid var(--border);border-radius:12px;padding:10px 16px;
  box-shadow:0 8px 30px rgba(0,0,0,.45);z-index:100;gap:10px;align-items:center">
  <span id="quickCount" style="font-size:.85rem;opacity:.8">0 selected</span>
  <button class="btn-reboot" style="padding:6px 12px" onclick="quickPower('reboot')">Reboot</button>
  <button class="btn-shutdown" style="padding:6px 12px" onclick="quickPower('shutdown')">Shutdown</button>
  <button class="btn btn-ghost" style="padding:6px 10px" onclick="clearQuick()">Clear</button>
</div>


<div id="alertHistoryModal" class="modal">
  <div class="modal-box" style="max-width:480px;max-height:80vh;overflow:auto">
    <h3>Alert history</h3>
    <div id="alertHistoryList" style="font-size:.85rem"></div>
    <button class="btn btn-ghost" style="margin-top:12px" onclick="closeAlertHistory()">Close</button>
  </div>
</div>


<div id="pinOverlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:9999;
  align-items:center;justify-content:center;flex-direction:column;gap:12px">
  <div style="font-size:1.1rem;opacity:.8">Enter PIN</div>
  <input id="pinInput" type="password" maxlength="8" style="font-size:1.4rem;text-align:center;width:140px;
    padding:10px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:inherit"
    onkeydown="if(event.key==='Enter')checkPin()">
  <button class="btn btn-primary" onclick="checkPin()">Unlock</button>
  <div id="pinErr" style="color:#f87171;font-size:.85rem;display:none">Wrong PIN</div>
</div>


<div style="text-align:center;padding:16px 8px 24px;opacity:.55;font-size:.75rem">
  PVE Node Monitor <span id="verLabel">v1.9.0</span> ·
  <a href="#" onclick="copyDiagnostics();return false" style="color:var(--accent)">Copy diagnostics</a> ·
  <a href="#" onclick="showQr();return false" style="color:var(--accent)">QR</a>
</div>
<div id="qrModal" class="modal">
  <div class="modal-box" style="text-align:center;max-width:280px">
    <h3>Open on phone</h3>
    <div id="qrBox" style="margin:12px auto"></div>
    <button class="btn btn-ghost" onclick="document.getElementById('qrModal').classList.remove('open')">Close</button>
  </div>
</div>


<nav id="bottomNav" style="display:none;position:fixed;bottom:0;left:0;right:0;background:var(--card);
  border-top:1px solid var(--border);padding:8px 0;z-index:40;justify-content:space-around">
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="window.scrollTo(0,0)">Nodes</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="openSettings()">Settings</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="openAddModal()">Add</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="toggleCinema()">Cinema</button>
</nav>

</body></html>"""

# Dashboard – reuse previous polished version (abbreviated key parts, full in file)
DASHBOARD_HTML = open("/home/workdir/artifacts/_dash_snippet.html").read() if False else r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f172a">
<meta name="apple-mobile-web-app-capable" content="yes">

<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
[data-theme="dark"]{--accent-user:var(--accent);--bg:#0a0a0a;--card:#141414;--text:#f3f4f6;--muted:#9ca3af;--border:#262626;--accent:#3b82f6;--green:#22c55e;--red:#ef4444;--orange:#f97316;--purple:#a855f7;--bar-bg:#1f1f1f;--lcd-bg:#051005;--lcd-text:#33ff66;--header-bg:#0f0f0f;--hover:#1a1a1a}
[data-theme="light"]{--bg:#f4f5f7;--card:#fff;--text:#111827;--muted:#6b7280;--border:#e5e7eb;--accent:#2563eb;--green:#16a34a;--red:#dc2626;--orange:#ea580c;--purple:#7c3aed;--bar-bg:#e5e7eb;--lcd-bg:#0a1a0a;--lcd-text:#33ff66;--header-bg:#fff;--hover:#f9fafb}
*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;min-height:100vh;padding-bottom:60px}
header{background:var(--header-bg);border-bottom:1px solid var(--border);padding:12px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:50}
.brand{text-align:center;flex:1}.brand .desk{font-size:.65rem;letter-spacing:.14em;color:var(--muted);text-transform:uppercase;font-weight:500}.brand h1{font-size:1.2rem;font-weight:700;margin-top:1px}
.header-right{display:flex;align-items:center;gap:8px}
.pill{background:var(--card);border:1px solid var(--border);border-radius:999px;padding:5px 11px;font-size:.78rem;color:var(--muted);font-weight:500}
.btn{border:none;border-radius:8px;padding:7px 13px;font-size:.82rem;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:5px}.btn:hover{opacity:.88}.btn-primary{background:var(--accent);color:#fff}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text);width:32px;height:32px;padding:0;justify-content:center;border-radius:8px;font-size:14px}.btn-ghost:hover{background:var(--hover)}
.main{max-width:1080px;margin:24px auto;padding:0 18px;display:grid;grid-template-columns:1fr 300px;gap:18px}@media(max-width:860px){.main{grid-template-columns:1fr}}
.node-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 18px}.node-card.offline{opacity:.65}
.node-head{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px}.node-name{font-size:1.05rem;font-weight:700}.node-meta{font-size:.74rem;color:var(--muted);margin-top:2px}
.badge{font-size:.62rem;font-weight:700;letter-spacing:.05em;padding:3px 8px;border-radius:999px;text-transform:uppercase}.badge-on{background:rgba(34,197,94,.15);color:var(--green)}.badge-off{background:rgba(239,68,68,.15);color:var(--red)}
.stat-row{display:flex;justify-content:space-between;font-size:.82rem;margin:5px 0 2px;align-items:center}.stat-row .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.bar{height:5px;background:var(--bar-bg);border-radius:3px;overflow:hidden;margin-bottom:9px}.bar>div{height:100%;border-radius:3px;transition:width .4s}
.mini-stats{display:flex;gap:7px;margin:10px 0 12px}.mini{flex:1;background:var(--bg);border-radius:8px;padding:7px 5px;text-align:center;font-size:.68rem;color:var(--muted)}.mini strong{display:block;font-size:.85rem;color:var(--text);margin-top:1px;font-weight:600}
.actions{display:flex;gap:7px;align-items:center}
.btn-reboot{background:var(--orange);color:#fff;flex:1;padding:9px;font-size:.82rem;border-radius:8px;border:none;font-weight:600;cursor:pointer}
.btn-shutdown{background:var(--red);color:#fff;flex:1;padding:9px;font-size:.82rem;border-radius:8px;border:none;font-weight:600;cursor:pointer}
.btn-icon{background:transparent;border:1px solid var(--border);color:var(--muted);width:32px;height:32px;border-radius:8px;cursor:pointer;font-size:13px;display:flex;align-items:center;justify-content:center}.btn-icon:hover{color:var(--text);background:var(--hover)}
.side-col{display:flex;flex-direction:column;gap:14px}.side-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 16px}
.side-card h3{font-size:.88rem;font-weight:600;margin-bottom:2px}.side-card .sub{font-size:.72rem;color:var(--muted);margin-bottom:10px}
.lcd-preview{background:var(--lcd-bg);color:var(--lcd-text);font-family:"Courier New",monospace;font-size:15px;line-height:1.55;padding:14px 16px;border-radius:8px;letter-spacing:1.5px;margin-bottom:10px;min-height:56px;box-shadow:inset 0 0 20px rgba(0,0,0,.55);white-space:pre}
.lcd-btns{display:flex;gap:5px;flex-wrap:wrap}.lcd-btns button{flex:1;min-width:56px;padding:6px 3px;border-radius:7px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:.72rem;font-weight:500;cursor:pointer}.lcd-btns button:hover{border-color:var(--accent);color:var(--accent)}
.lcd-mode{float:right;font-size:.62rem;background:var(--bg);border:1px solid var(--border);padding:2px 7px;border-radius:999px;color:var(--muted);font-weight:600}
.alert-box{font-size:.8rem;color:var(--muted);line-height:1.4}.alert-box.active{color:var(--orange)}
.graphs-wrap{grid-column:1/-1;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 18px;margin-top:2px}
.graphs-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.graphs-head h2{font-size:.95rem;font-weight:600}.graphs-head .sub{font-size:.74rem;color:var(--muted)}
.graphs-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.chart-box{background:var(--bg);border-radius:10px;padding:10px 12px;min-height:150px}.chart-box h4{font-size:.76rem;color:var(--muted);margin-bottom:6px;font-weight:500}
canvas{width:100%!important;max-height:130px}
.drawer-bg{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:100;opacity:0;pointer-events:none;transition:opacity .2s}.drawer-bg.open{opacity:1;pointer-events:auto}
.drawer{position:fixed;top:0;right:0;width:330px;max-width:100%;height:100%;background:var(--card);border-left:1px solid var(--border);z-index:101;transform:translateX(100%);transition:transform .25s ease;overflow-y:auto;padding:18px 20px 48px}.drawer.open{transform:translateX(0)}
.drawer h2{font-size:1.1rem;margin-bottom:18px;display:flex;justify-content:space-between;align-items:center}.drawer .close-btn{background:none;border:none;color:var(--muted);font-size:1.15rem;cursor:pointer}
.set-row{display:flex;justify-content:space-between;align-items:center;padding:11px 0;border-bottom:1px solid var(--border)}.set-row label{font-size:.88rem}.set-row .hint{font-size:.7rem;color:var(--muted);margin-top:1px}
.toggle{width:40px;height:22px;background:var(--bar-bg);border-radius:999px;position:relative;cursor:pointer;transition:background .2s;border:none;flex-shrink:0}.toggle.on{background:var(--accent)}.toggle::after{content:"";position:absolute;width:16px;height:16px;background:#fff;border-radius:50%;top:3px;left:3px;transition:transform .2s;box-shadow:0 1px 2px rgba(0,0,0,.25)}.toggle.on::after{transform:translateX(18px)}
.set-input{width:100%;padding:7px 10px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:.88rem;margin-top:5px}.set-group{margin:14px 0 6px}.set-group label{font-size:.76rem;color:var(--muted);font-weight:500}
.drawer-actions{display:flex;gap:8px;margin-top:20px;flex-wrap:wrap}.drawer-actions button{flex:1;min-width:120px;padding:10px;border-radius:8px;font-weight:600;cursor:pointer;border:none;font-size:.82rem}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;display:flex;align-items:center;justify-content:center;padding:16px;opacity:0;pointer-events:none;transition:opacity .2s}.modal-bg.open{opacity:1;pointer-events:auto}
.modal{background:var(--card);border-radius:14px;padding:22px;width:100%;max-width:400px;border:1px solid var(--border)}.modal h2{font-size:1.1rem;margin-bottom:3px}.modal .sub{font-size:.82rem;color:var(--muted);margin-bottom:14px}
.choice{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:14px}.choice button{padding:11px;border-radius:9px;border:2px solid var(--border);background:var(--bg);color:var(--text);font-weight:600;cursor:pointer;font-size:.85rem}.choice button.active{border-color:var(--accent);background:rgba(59,130,246,.1);color:var(--accent)}
.modal label{display:block;margin:10px 0 3px;font-size:.76rem;color:var(--muted);font-weight:500}.modal input{width:100%;padding:8px 11px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:.92rem}
.modal-actions{display:flex;gap:7px;margin-top:18px;align-items:center}.modal-actions .spacer{flex:1}.btn-cancel{background:var(--bg);border:1px solid var(--border);color:var(--text)}.btn-save{background:var(--accent);color:#fff}.btn-test{background:transparent;border:1px solid var(--border);color:var(--muted);font-size:.78rem;padding:7px 11px}
footer{position:fixed;bottom:0;left:0;right:0;background:var(--header-bg);border-top:1px solid var(--border);padding:9px 14px;text-align:center;font-size:.74rem;color:var(--muted);z-index:40}footer a{color:var(--accent);text-decoration:none;margin:0 5px}
.hidden{display:none!important}
.empty-state{text-align:center;padding:48px 20px;color:var(--muted)}
.empty-state h3{color:var(--text);margin-bottom:8px}
@media(max-width:640px){
  header{flex-wrap:wrap;gap:8px;padding:10px 12px}
  .brand{order:-1;width:100%;text-align:center}
  .header-right{width:100%;justify-content:center;flex-wrap:wrap}
  #nodeSearch{width:100px}
  .main{padding:0 10px;margin:16px auto}
  .node-card .actions{flex-wrap:wrap}
}
.toast{position:fixed;bottom:68px;left:50%;transform:translateX(-50%);background:var(--card);border:1px solid var(--border);color:var(--text);padding:9px 16px;border-radius:9px;font-size:.82rem;z-index:300;opacity:0;transition:opacity .25s;pointer-events:none}.toast.show{opacity:1}

@media (max-width: 780px) {
  #bottomNav { display: flex !important; }
  body { padding-bottom: 56px; }

  .layout { flex-direction: column; }
  #nodesCol, .side { width: 100% !important; }
  .node-card { margin-bottom: 10px; }
  .actions { flex-wrap: wrap; }
  .btn-reboot, .btn-shutdown { flex: 1 1 40%; }
  .modal-box { margin: 12px; max-width: calc(100vw - 24px); }
  .mini-stats { gap: 6px; }
}


.node-card.stale .badge-on { animation: pulse-red 1.4s infinite; }
@keyframes pulse-red {
  0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,.6); }
  50%     { box-shadow: 0 0 0 6px rgba(239,68,68,0); }
}
.node-card.stale .node-meta:last-of-type { color: #f87171 !important; }


/* Theme packs */
body.theme-cyberpunk { --accent:#ff2a6d; --purple:#05d9e8; --bg:#0d0221; --card:#1a0a2e; }
body.theme-terminal  { --accent:#33ff33; --purple:#33ff99; --bg:#0a0a0a; --card:#111; color:#33ff33; }
body.theme-nord      { --accent:#88c0d0; --purple:#b48ead; --bg:#2e3440; --card:#3b4252; }
body.cinema .topbar, body.cinema .side, body.cinema #nodesCol { display:none !important; }
body.cinema .graphs { position:fixed; inset:0; z-index:50; background:var(--bg); padding:12px; }


body.guest-mode .actions button:not(.btn-icon),
body.guest-mode .btn-reboot,
body.guest-mode .btn-shutdown { display: none !important; }
body.guest-mode #bottomNav { display: none !important; }

body.setup-page #alertHistoryModal,
body.setup-page #qrModal,
body.setup-page #pinOverlay,
body.setup-page #bottomNav,
body.setup-page #quickBar,
body.setup-page .footer-diag {
  display: none !important;
}

body.setup-page #alertHistoryModal,
body.setup-page #qrModal,
body.setup-page #pinOverlay,
body.setup-page #bottomNav,
body.setup-page #quickBar,
body.setup-page .footer-diag,
body.setup-page a[onclick*="copyDiagnostics"],
body.setup-page a[onclick*="showQr"] {
  display: none !important;
}
/* also hide the floating version text */
body.setup-page > div:last-of-type {
  display: none !important;
}


/* hide phase4 extras on setup */
body:not(.dashboard) #pinOverlay,
body:not(.dashboard) #qrModal,
body:not(.dashboard) #alertHistoryModal,
body:not(.dashboard) #bottomNav,
body:not(.dashboard) #quickBar,
body:not(.dashboard) a[onclick*="copyDiagnostics"],
body:not(.dashboard) a[onclick*="showQr"] {
  display: none !important;
}

</style>
</head>
<body class="setup-page">
<header>
  <div style="width:160px"></div>
  <div class="brand"><div class="desk">Desk Console</div><h1>PVE Node Monitor</h1></div>
  <div class="header-right">
    <input id="nodeSearch" placeholder="Search nodes…" oninput="filterNodes()" style="background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:6px 10px;font-size:.8rem;width:140px">

<div id="tagChips" style="display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 4px"></div>

    <span class="pill" id="onlinePill">–/– online</span>
    <button class="btn btn-primary" onclick="openAddModal()">+ Add node / server</button>
    <button class="btn btn-ghost" style="width:auto;padding:7px 10px;font-size:.78rem" onclick="openBulk()" title="Bulk power">Bulk</button>
    <button class="btn btn-ghost" style="width:auto;padding:7px 10px;font-size:.78rem" onclick="openImport()" title="Import"> Discover</button><button class="btn btn-ghost" style="width:auto;padding:4px 10px;font-size:.75rem" onclick="openDiscover()">Import</button>
    <button class="btn-ghost" onclick="openSettings()" title="Settings">⚙</button>
    <button class="btn-ghost" id="themeBtn" onclick="toggleTheme()" title="Theme">☾</button>
    <button class="btn-ghost" onclick="load()" title="Refresh">↻</button>
  </div>
</header>
<div class="main">
  <div id="nodesCol"></div>
  <div class="side-col">
    <div class="side-card" id="lcdCard">
      <span class="lcd-mode" id="lcdMode">PAGES</span>
      <h3>Desk LCD</h3>
      <div class="sub">16×2 · touch · buzzer</div>
      <div class="lcd-preview" id="lcdPreview">CPU:  --.-%&#10;RAM: --.-/--.-G</div>
      <div class="lcd-btns">
        <button onclick="lcdAction('page')">Page</button>
        <button onclick="lcdAction('settings')">Settings</button>
        <button onclick="lcdAction('change')">Change</button>
        <button onclick="lcdAction('exit')">Exit</button>
      </div>
    </div>
    <div class="side-card">
      <h3>Alerts <button class="btn btn-ghost" style="float:right;width:auto;padding:2px 8px;font-size:.7rem" onclick="ackAll()">Ack all</button></h3>
      <div class="alert-box" id="alertStatus">Quiet. Thresholds fire on the LCD and buzzer.</div>
      <div 
<button class="btn btn-ghost" style="width:auto;padding:4px 10px;font-size:.75rem;margin-left:6px"
        onclick="openAlertHistory()">History</button>
id="alertList" style="margin-top:8px;font-size:.72rem;color:var(--muted);max-height:100px;overflow-y:auto"></div>
    </div>
    <div class="side-card">
      <h3>Activity</h3>
      <div id="activityList" style="font-size:.72rem;color:var(--muted);max-height:90px;overflow-y:auto"></div>
    </div>

<div class="card" style="margin-top:10px">
  <div style="display:flex;align-items:center;justify-content:space-between">
    <div>
      <div style="font-weight:600">Guests</div>
      <div style="font-size:.75rem;opacity:.65">VM / LXC on active node</div>
    </div>
    <button class="btn btn-ghost" style="width:auto;padding:6px 10px;font-size:.78rem"
            onclick="openGuestsForActive()">Open</button>
  </div>
</div>
  </div>
  <div class="graphs-wrap">
    <div class="graphs-head"><div><h2>
<div id="bigStats" style="display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 12px"></div>
Graphs</h2><div class="sub">Live samples from the active node</div></div>
      <div style="display:flex;gap:6px;align-items:center">
        <select id="graphRange" onchange="changeRange()" style="background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:4px 8px;font-size:.78rem">
          <option value="1h">1h</option><option value="6h">6h</option><option value="24h">24h</option><option value="7d">7d</option>
        </select>
<button class="btn btn-ghost" style="width:auto;padding:4px 10px;font-size:.75rem;margin-left:6px" onclick="exportGraphsPng()">PNG</button>

        <button class="btn btn-ghost" style="width:auto;padding:5px 11px;font-size:.78rem" onclick="openGraphEdit()">✎ Edit</button>
      </div></div>
    <div class="graphs-grid">
      <div class="chart-box" id="cCpu"><h4>CPU</h4><canvas id="chartCpu"></canvas></div>
      <div class="chart-box" id="cRam"><h4>RAM</h4><canvas id="chartRam"></canvas></div>
      <div class="chart-box" id="cNet"><h4>Network</h4><canvas id="chartNet"></canvas></div>
      <div class="chart-box hidden" id="cDisk"><h4>Disk</h4><canvas id="chartDisk"></canvas></div>
    </div>
  </div>
</div>
<footer>Insta: <a href="https://instagram.com/vxprxx" target="_blank">vxprxx</a> · GitHub: <a href="https://github.com/retardedmonkeygaming" target="_blank">retardedmonkeygaming</a>
<br><span style="font-size:.68rem;opacity:.65">PVE Node Monitor · v1.9</span></footer>

<div class="drawer-bg" id="drawerBg" onclick="closeSettings()"></div>
<div class="drawer" id="drawer">
  <h2>Settings <button class="close-btn" onclick="closeSettings()">✕</button></h2>
  <div class="set-row"><div><label>Active buzzer</label><div class="hint">GPIO clicks</div></div><button class="toggle" id="tBuzzer" onclick="toggleSet(this,'buzzer_enabled')"></button></div>
  <div class="set-row"><div><label>Passive buzzer</label><div class="hint">Alert tones</div></div><button class="toggle" id="tPassive" onclick="toggleSet(this,'passive_buzzer_enabled')"></button></div>
  <div class="set-row"><div><label>Quiet mode</label><div class="hint">Mute all alert tones</div></div><button class="toggle" id="tQuiet" onclick="toggleSet(this,'quiet_mode')"></button></div>
  <div class="set-row"><div><label>Hostname flash</label><div class="hint">Show node name on LCD</div></div><button class="toggle" id="tFlash" onclick="toggleSet(this,'flash_hostname')"></button></div>
  <div class="set-row"><div><label>Compact cards</label><div class="hint">Denser node cards</div></div><button class="toggle" id="tCompact" onclick="toggleSet(this,'compact_cards')"></button></div>
  <div class="set-row"><div><label>Show net on card</label></div><button class="toggle" id="tNet" onclick="toggleSet(this,'show_net_on_card')"></button></div>
  <div class="set-row"><div><label>Confirm power actions</label></div><button class="toggle" id="tConfirm" onclick="toggleSet(this,'confirm_power')"></button></div>
  <div class="set-group"><label>Log interval (s)</label><input class="set-input" type="number" id="sLog" min="5" max="120"></div>
  <div class="set-group"><label>Auto-refresh UI (s)</label><input class="set-input" type="number" id="sRefresh" min="3" max="30"></div>
  <div class="set-group"><label>Hostname flash interval (s)</label><input class="set-input" type="number" id="sFlashInt" min="5" max="60"></div>
  <div class="set-group"><label>Alert repeat (s)</label><input class="set-input" type="number" id="sAlertRep" min="5" max="120"></div>
  <div class="set-group"><label>CPU alert %</label><input class="set-input" type="number" id="sCpu" min="1" max="100"></div>
  <div class="set-group"><label>Disk alert %</label><input class="set-input" type="number" id="sDisk" min="1" max="100">

<label>Quiet hours (HH:MM-HH:MM)</label>
<input id="sQuietHours" placeholder="22:00-07:00" style="width:100%;margin-bottom:8px">
<label>Disk free alert (GB left)</label>
<input id="sDiskFree" type="number" placeholder="e.g. 20" style="width:100%;margin-bottom:8px">
</div>
  <div class="set-group"><label>RAM alert %</label><input class="set-input" type="number" id="sRam" min="1" max="100"></div>
  <div class="set-group"><label>Density</label>
    <select class="set-input" id="sDensity">
      <option value="comfortable">Comfortable</option>
      <option value="compact">Compact</option>
      <option value="dense">Dense</option>
    </select>

<label>Theme pack</label>
<select id="sThemePack" style="width:100%;margin-bottom:8px">
  <option value="default">Default</option>
  <option value="cyberpunk">Cyberpunk</option>
  <option value="terminal">Terminal green</option>
  <option value="nord">Nord</option>
</select>
<label>Layout preset</label>
<select id="sLayout" style="width:100%;margin-bottom:8px">
  <option value="work">Work</option>
  <option value="night">Night</option>
  <option value="minimal">Minimal</option>
</select>
<label>UI PIN (blank = off)</label>
<input id="sPin" type="password" maxlength="8" placeholder="e.g. 1234" style="width:100%;margin-bottom:8px">

<label>Webhook URL (optional)</label>
<input id="sWebhook" placeholder="https://…" style="width:100%;margin-bottom:8px">
<label><input type="checkbox" id="sOfflineSound" checked> Offline sound</label>
<label><input type="checkbox" id="sShowMinMax" checked> Show min/max on graphs</label>

<label><input type="checkbox" id="sClock12"> 12-hour clock</label>

  </div>
  <div class="drawer-actions">
    <button class="btn-cancel" onclick="testBuzzer()">Test buzzer</button>
    <button class="btn-primary" onclick="saveAllSettings()">Save settings</button>
    <button class="btn-cancel" onclick="exportCsv()">Export CSV</button>
    <button class="btn-cancel" onclick="backupConfig()">Backup config</button>
    <button class="btn-cancel" onclick="restoreConfig()">Restore config</button>
    <button class="btn-cancel" onclick="exportJson()">Export JSON</button>
  </div>
</div>

<div class="modal-bg" id="addModal"><div class="modal">
  <h2 id="modalTitle">Add node / server</h2><div class="sub" id="modalSub">Another node on an existing host</div>
  <div class="choice"><button type="button" id="btnNode" class="active" onclick="setType('node')">New node</button>
  <button type="button" id="btnServer" onclick="setType('server')">New server</button></div>
  <label>Friendly name</label><input id="mName"><label>IP / hostname</label><input id="mIp">
  <label>Proxmox node name</label><input id="mNode">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:9px"><div><label>User</label><input id="mUser" value="root@pam"></div><div><label>Password</label><input id="mPass" type="password"></div></div>
  <div class="modal-actions"><button class="btn-test" onclick="testConn()">Test connection</button><div class="spacer"></div>
  <button class="btn btn-cancel" onclick="closeAddModal()">Cancel</button><button class="btn btn-save" id="modalSaveBtn" onclick="saveNode()">Add</button></div>
</div></div>

<div class="modal-bg" id="graphModal"><div class="modal">
  <h2>Edit Graphs</h2><div class="sub">Toggle charts</div>
  <div class="set-row"><label>CPU</label><button class="toggle on" id="gCpu" onclick="toggleGraph(this,'cpu')"></button></div>
  <div class="set-row"><label>RAM</label><button class="toggle on" id="gRam" onclick="toggleGraph(this,'ram')"></button></div>
  <div class="set-row"><label>Network</label><button class="toggle on" id="gNet" onclick="toggleGraph(this,'net')"></button></div>
  <div class="set-row"><label>Disk</label><button class="toggle" id="gDisk" onclick="toggleGraph(this,'disk')"></button></div>
  <div class="modal-actions"><div class="spacer"></div><button class="btn btn-save" onclick="closeGraphEdit()">Done</button></div>
</div></div>

<div class="modal-bg" id="guestModal"><div class="modal" style="max-width:520px">
  <h2 id="guestTitle">Guests</h2>
  <div class="sub" id="guestSub">VMs & containers</div>
  <div id="guestList" style="max-height:360px;overflow-y:auto;font-size:.85rem"></div>
  <div class="modal-actions"><div class="spacer"></div>
  <button class="btn btn-cancel" onclick="closeGuests()">Close</button></div>
</div></div>
<div class="modal-bg" id="bulkModal"><div class="modal">
  <h2>Bulk power</h2>
  <div class="sub">Select nodes then reboot or shutdown</div>
  <div id="bulkList" style="max-height:240px;overflow-y:auto;margin:12px 0"></div>
  <div class="modal-actions">
    <button class="btn btn-reboot" onclick="bulkPower('reboot')">Reboot selected</button>
    <button class="btn btn-shutdown" onclick="bulkPower('shutdown')">Shutdown selected</button>
    <button class="btn btn-cancel" onclick="closeBulk()">Cancel</button>
  </div>
</div></div>

<div class="modal-bg" id="detailModal"><div class="modal" style="max-width:440px">
  <h2 id="detailTitle">Node</h2>
  <div class="sub" id="detailSub"></div>
  <label>Note</label><input id="dNote">
  <label>Tags (comma-separated)</label><input id="dTags" placeholder="prod, lab">
  <div class="row" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:8px">
    <div><label>CPU alert %</label><input id="dCpu" type="number" placeholder="global"></div>
    <div><label>RAM alert %</label><input id="dRam" type="number" placeholder="global"></div>
    <div><label>Disk alert %</label><input id="dDisk" type="number" placeholder="global"></div>
  </div>
  <div class="modal-actions" style="margin-top:16px">
    <button class="btn btn-ghost" style="width:auto" onclick="openGuests(detailName)">Guests</button>
    <div class="spacer"></div>
    <button class="btn btn-cancel" onclick="closeDetail()">Close</button>
    <button class="btn btn-save" onclick="saveDetail()">Save</button>
  </div>
</div></div>
<div class="modal-bg" id="importModal"><div class="modal">
  <h2>Import nodes</h2>
  <div class="sub">Paste JSON array of nodes</div>
  <textarea id="importText" rows="8" style="width:100%;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:10px;font-family:monospace;font-size:.8rem" placeholder='[{"name":"pve2","ip":"192.168.1.2","node":"pve","user":"root@pam","password":"..."}]'></textarea>
  <div class="modal-actions">
    <button class="btn btn-cancel" onclick="closeImport()">Cancel</button>
    <button class="btn btn-save" onclick="doImport()">Import</button>
  </div>
</div></div>
<div class="toast" id="toast"></div>
<script>
let addType='node',editName=null,settings={},charts={},graphVisible={cpu:true,ram:true,net:true,disk:false},refreshTimer=null;
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2400)}
function applyTheme(t){const theme=t==='light'?'light':'dark';document.documentElement.setAttribute('data-theme',theme);document.getElementById('themeBtn').textContent=theme==='dark'?'☾':'☀'}
function toggleTheme(){const next=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';applyTheme(next);settings.theme=next;fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({theme:next})})}
function makeChart(id,color){return new Chart(document.getElementById(id),{type:'line',data:{labels:[],datasets:[{data:[],borderColor:color,tension:.35,pointRadius:0,borderWidth:2}]},options:{responsive:true,animation:false,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:5,color:'#6b7280',font:{size:10}},grid:{display:false}},y:{ticks:{color:'#6b7280',font:{size:10}},grid:{color:'rgba(100,100,100,.15)'}}}}})}
charts.cpu=makeChart('chartCpu','#3b82f6');charts.ram=makeChart('chartRam','#a855f7');
charts.net=new Chart(document.getElementById('chartNet'),{type:'line',data:{labels:[],datasets:[{data:[],borderColor:'#22c55e',tension:.35,pointRadius:0,borderWidth:2},{data:[],borderColor:'#f97316',tension:.35,pointRadius:0,borderWidth:2}]},options:{responsive:true,animation:false,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:5,color:'#6b7280',font:{size:10}},grid:{display:false}},y:{ticks:{color:'#6b7280',font:{size:10}},grid:{color:'rgba(100,100,100,.15)'}}}}});
charts.disk=makeChart('chartDisk','#eab308');
function openSettings(){document.getElementById('drawerBg').classList.add('open');document.getElementById('drawer').classList.add('open');loadSettings()}
function closeSettings(){document.getElementById('drawerBg').classList.remove('open');document.getElementById('drawer').classList.remove('open')}

let uiPin = '';

function applyGuestMode() {
  const unlocked = sessionStorage.getItem('pve_unlocked') === '1';
  const hasPin = !!(window.uiPin);
  // if PIN is set and not unlocked → guest
  const guest = hasPin && !unlocked;
  document.body.classList.toggle('guest-mode', guest);
  // hide power / settings / add / bulk / delete controls
  document.querySelectorAll('.btn-reboot, .btn-shutdown, #bottomNav, [onclick*="openSettings"], [onclick*="openAddModal"], [onclick*="openBulk"], [onclick*="deleteNode"], [onclick*="power("]').forEach(el => {
    if (guest) el.style.display = 'none';
    else el.style.display = '';
  });
  // keep graphs + cards visible
}

function applyPinLock() {
  if (!uiPin) return;
  const unlocked = sessionStorage.getItem('pve_unlocked') === '1';
  document.getElementById('pinOverlay').style.display = unlocked ? 'none' : 'flex';
}
function checkPin() {
  const v = document.getElementById('pinInput').value;
  if (v === uiPin) {
    sessionStorage.setItem('pve_unlocked', '1');
    document.getElementById('pinOverlay').style.display = 'none';
    applyGuestMode();
    document.getElementById('pinErr').style.display = 'none';
  } else {
    document.getElementById('pinErr').style.display = 'block';
  }
}

async function loadSettings(){const r=await fetch('/api/settings');settings=await r.json();applyTheme(settings.theme||'dark');
setToggle('tBuzzer',settings.buzzer_enabled);setToggle('tPassive',settings.passive_buzzer_enabled);setToggle('tQuiet',settings.quiet_mode);setToggle('tFlash',settings.flash_hostname!==false);setToggle('tCompact',settings.compact_cards);
if(document.getElementById('sDensity'))sDensity.value=settings.density||'comfortable';
if(document.getElementById('sAccent')){sAccent.value=settings.accent||'#3b82f6';applyAccent(sAccent.value)}
sLog.value=settings.log_interval;sRefresh.value=settings.auto_refresh||5;sCpu.value=settings.cpu_alert;sDisk.value=settings.disk_alert;sRam.value=settings.ram_alert;
if(document.getElementById('sFlashInt'))sFlashInt.value=settings.flash_interval||10;
if(document.getElementById('sAlertRep'))sAlertRep.value=settings.alert_repeat_sec||25;
setToggle('tNet',settings.show_net_on_card!==false);setToggle('tConfirm',settings.confirm_power!==false);
graphVisible=settings.graph_visible||graphVisible;applyGraphVisibility();restartRefresh();
if(settings.standalone||!settings.has_lcd){const el=document.getElementById('lcdCard');if(el)el.style.display='none'}
applyDensity(settings.density||'comfortable');}
function setToggle(id,on){document.getElementById(id).classList.toggle('on',!!on)}
function toggleSet(el,key){el.classList.toggle('on');settings[key]=el.classList.contains('on')}
async function saveAllSettings(){if(document.getElementById('sDensity')){settings.density=sDensity.value;applyDensity(settings.density);
  uiPin = settings.ui_pin || '';
  applyPinLock();
  applyGuestMode();
  applyThemePack(settings.theme_pack || 'default');
  applyLayoutPreset(settings.layout_preset || 'work');
  if (settings.clock_12h) window.clock12h = true;}
if(document.getElementById('sAccent')){settings.accent=sAccent.value;applyAccent(settings.accent)}
settings.log_interval=parseInt(sLog.value)||10;settings.auto_refresh=parseInt(sRefresh.value)||5;settings.cpu_alert=parseInt(sCpu.value)||85;settings.disk_alert=parseInt(sDisk.value)||90;
if(document.getElementById('sQuietHours'))settings.quiet_hours=sQuietHours.value||'';
if(document.getElementById('sDiskFree'))settings.disk_free_gb_alert=sDiskFree.value===''?null:parseFloat(sDiskFree.value);settings.ram_alert=parseInt(sRam.value)||90;
if(document.getElementById('sFlashInt'))settings.flash_interval=parseInt(sFlashInt.value)||10;
if(document.getElementById('sAlertRep'))settings.alert_repeat_sec=parseInt(sAlertRep.value)||25;
await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)});toast('Settings saved');restartRefresh();load()}
async function testBuzzer(){await fetch('/api/buzzer/test',{method:'POST'});toast('Buzzer test sent')}
function rerunWizard() {
  if (!confirm('Reset setup flags and open wizard? (nodes kept)')) return;
  fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({setup_done:false, pins_done:false})}).then(()=> location.href='/setup');
}
function backupConfig(){window.location='/api/config/backup'}

function exportCsv(){window.location='/api/export/csv'}
function openAddModal(edit=null){editName=edit;modalTitle.textContent=edit?'Edit node':'Add node / server';modalSaveBtn.textContent=edit?'Save':'Add';
if(!edit){mName.value=mIp.value=mNode.value=mPass.value='';mUser.value='root@pam';setType('node')}addModal.classList.add('open')}
function closeAddModal(){addModal.classList.remove('open');editName=null}
function setType(t){addType=t;btnNode.classList.toggle('active',t==='node');btnServer.classList.toggle('active',t==='server');modalSub.textContent=t==='node'?'Another node on an existing host':'Independent Proxmox host'}
async function testConn(){const r=await fetch('/api/test-connection',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:mName.value,ip:mIp.value,node:mNode.value||'pve',user:mUser.value,password:mPass.value})});const j=await r.json();toast(j.ok?('✓ Connected'+(j.latency_ms!=null?' · '+j.latency_ms+'ms':'')):'✗ '+(j.message||'Failed'))}
async function saveNode(){const payload={name:mName.value.trim(),ip:mIp.value.trim(),node:mNode.value.trim()||mName.value.trim(),user:mUser.value.trim()||'root@pam',password:mPass.value,type:addType};
if(!payload.name||!payload.ip){toast('Name and IP required');return}
const r=editName?await fetch('/api/nodes/'+encodeURIComponent(editName),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}):await fetch('/api/nodes/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
const j=await r.json();if(j.ok){closeAddModal();load();toast(editName?'Saved':'Node added')}else toast('Error: '+(j.error||'unknown'))}

let pendingDelete = null;
async function deleteNode(name) {
  if (!confirm('Delete "' + name + '"? (undo available 8s)')) return;
  pendingDelete = name;
  // soft: just hide card
  document.querySelectorAll('.node-card').forEach(c => {
    if (c.textContent.includes(name)) c.style.opacity = '0.3';
  });
  toast('Deleted – click Undo in toast area or wait', 8000);
  // show undo via a temporary button if possible
  setTimeout(async () => {
    if (pendingDelete === name) {
      await fetch('/api/nodes/' + encodeURIComponent(name), {method:'DELETE'});
      pendingDelete = null;
      load();
    }
  }, 8000);
}
function undoDelete() {
  if (!pendingDelete) return;
  pendingDelete = null;
  load();
  toast('Delete cancelled');
}

async function deleteNode_DISABLED(name){if(!confirm('Delete "'+name+'"?'))return;await fetch('/api/nodes/'+encodeURIComponent(name),{method:'DELETE'});load();toast('Deleted')}
function openGraphEdit(){setToggle('gCpu',graphVisible.cpu);setToggle('gRam',graphVisible.ram);setToggle('gNet',graphVisible.net);setToggle('gDisk',graphVisible.disk);graphModal.classList.add('open')}
function closeGraphEdit(){graphModal.classList.remove('open');fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({graph_visible:graphVisible})})}
function toggleGraph(el,key){el.classList.toggle('on');graphVisible[key]=el.classList.contains('on');applyGraphVisibility()}
function applyGraphVisibility(){cCpu.classList.toggle('hidden',!graphVisible.cpu);cRam.classList.toggle('hidden',!graphVisible.ram);cNet.classList.toggle('hidden',!graphVisible.net);cDisk.classList.toggle('hidden',!graphVisible.disk)}
async function lcdAction(act){const r=await fetch('/api/lcd/'+act,{method:'POST'});const j=await r.json();if(j.state)lcdMode.textContent=j.state.mode||'PAGES';toast('LCD → '+act)}
let lastPowerAt = 0;
async function power(name, action) {
  const limit = (settings && settings.power_rate_limit_sec) || 30;
  const now = Date.now()/1000;
  if (now - lastPowerAt < limit) {
    toast('Wait ' + Math.ceil(limit - (now-lastPowerAt)) + 's (rate limit)');
    return;
  }
  // countdown confirm
  let sec = 3;
  const id = toast('Really ' + action + ' "' + name + '"? ' + sec + '…', 4000);
  const t = setInterval(()=>{
    sec--;
    if (sec <= 0) { clearInterval(t); }
  }, 1000);
  if (!confirm('Confirm ' + action + ' of "' + name + '"?')) return;
  lastPowerAt = now;
  const r = await fetch('/api/power/' + encodeURIComponent(name) + '/' + action, {method:'POST'});
  const j = await r.json();
  toast(j.ok ? action + ' sent' : 'Failed');
});const j=await r.json();toast(j.ok?action+' sent':'Failed')}
function restartRefresh(){if(refreshTimer)clearInterval(refreshTimer);refreshTimer=setInterval(load,(settings.auto_refresh||5)*1000)}

async function sendWebhook(payload) {
  const url = (settings && settings.webhook_url) || '';
  if (!url) return;
  try {
    await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({...payload, source:'pve-node-monitor'})});
  } catch(e) {}
}


const offlineAudio = (()=>{ try {
  const ctx = new (window.AudioContext||window.webkitAudioContext)();
  return () => {
    if (settings && settings.offline_sound === false) return;
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = 440; g.gain.value = 0.08;
    o.start(); g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime+0.4); o.stop(ctx.currentTime+0.4);
  };
} catch(e){ return ()=>{}; } })();

async function load(){
  const [nodes,logs,lcd]=await Promise.all([fetch('/api/current').then(r=>r.json()),fetch('/api/logs/server?limit=40').then(r=>r.json()),fetch('/api/lcd').then(r=>r.json()).catch(()=>null)]);
  allNodesCache=nodes;onlinePill.textContent=nodes.filter(n=>n.online==1).length+'/'+nodes.length+' online';
  if(!nodes.length){document.getElementById('nodesCol').innerHTML='<div class="empty-state"><h3>No nodes yet</h3><p>Add a Proxmox node to start monitoring.</p><button class="btn btn-primary" style="margin-top:12px" onclick="openAddModal()">+ Add node</button></div>';renderBigStats(nodes); renderTagChips(nodes);
  loadCharts();return}
  const col=document.getElementById('nodesCol');col.innerHTML='';
  nodes.forEach(n=>{const on=n.online==1;
  const ageSec = (()=>{try{const d=new Date((n.timestamp||'').includes('T')?n.timestamp: (n.timestamp||'').replace(' ','T')+'Z');return Math.floor((Date.now()-d.getTime())/1000)}catch(e){return 999}})();const ramPct=n.ram_total_gb?(n.ram_used_gb/n.ram_total_gb*100):0;
  const card=document.createElement('div');card.className='node-card'+(on?'':' offline')+(ageSec>60?' stale':'');
  // offline toast
  if(prevOnline[n.node_name]===1 && !on) sendWebhook({event:'offline', node:n.node_name}); offlineAudio(); toast('⚠ '+n.node_name+' went offline', 6000);
  if(prevOnline[n.node_name]===0 && on) sendWebhook({event:'online', node:n.node_name}); toast('✓ '+n.node_name+' back online');
  prevOnline[n.node_name]=on?1:0;
  const fav=n.favorite?'★':'☆';
  const seen=relTime(n.timestamp);
  card.innerHTML=`<div class="node-head"><input type="checkbox" class="node-check" data-node-check style="margin-right:6px" onclick="event.stopPropagation()"><div><div class="node-name"><span style="cursor:pointer;margin-right:4px" onclick="toggleFav('${n.node_name}',${!!n.favorite})" title="Favorite">${fav}</span><span style="cursor:pointer" onclick="openDetailByName('${n.node_name}')">${n.node_name}</span></div><div class="node-meta">${n.ip||''} · ${n.node||''} · ${n.type||'server'}${n.note?' · '+n.note:''}${(n.tags&&n.tags.length)?' · '+n.tags.join(', '):''}</div><div class="node-meta" style="margin-top:2px">Last seen: ${seen}</div></div>
  <span class="badge ${on?'badge-on':'badge-off'}">${on?'ONLINE':'OFFLINE'}</span></div>
  <div class="stat-row"><span><span class="dot" style="background:var(--accent)"></span>CPU</span><span>${(n.cpu_usage||0).toFixed(1)}%</span></div>
  <div class="bar"><div style="width:${n.cpu_usage||0}%;background:var(--accent)"></div></div>
  <div class="stat-row"><span><span class="dot" style="background:var(--purple)"></span>RAM</span><span>${(n.ram_used_gb||0).toFixed(1)} / ${(n.ram_total_gb||0).toFixed(1)} GB</span></div>
  <div class="bar"><div style="width:${ramPct}%;background:var(--purple)"></div></div>
  <div class="mini-stats"><div class="mini">DISK<strong>${(n.disk_pct||0).toFixed(1)}%</strong></div><div class="mini">VMS<strong>${n.active_vms||0}</strong></div><div class="mini">NET<strong>${(n.net_in_kbps||0).toFixed(0)} ↓</strong></div></div>
  <div class="actions"><button class="btn-reboot" ${on?'':'disabled'} onclick="power('${n.node_name}','reboot')">↻ Reboot</button>
  <button class="btn-shutdown" ${on?'':'disabled'} onclick="power('${n.node_name}','shutdown')">⏻ Shutdown</button>
  <button class="btn-icon" onclick="openGuests('${n.node_name}')" title="Guests">▣</button>
  <button class="btn-icon" onclick="editNote('${n.node_name}')" title="Note">📝</button>
  <button class="btn-icon" onclick="openEdit('${n.node_name}','${n.ip||''}','${n.node||''}','${n.type||'server'}')">✎</button>
  <button class="btn-icon" onclick="deleteNode('${n.node_name}')">🗑</button></div>`;col.appendChild(card)});
  if(lcd&&lcd.lines){lcdPreview.textContent=(lcd.lines[0]||'').padEnd(16)+'\n'+(lcd.lines[1]||'').padEnd(16);lcdMode.textContent=lcd.mode||'PAGES';
  if(lcd.alerting){alertStatus.textContent='⚠ Threshold exceeded';alertStatus.classList.add('active')}else{alertStatus.textContent='Quiet. Thresholds fire on the LCD and buzzer.';alertStatus.classList.remove('active')}}
  else if(nodes.length){const n=nodes[0];lcdPreview.textContent=`CPU: ${(n.cpu_usage||0).toFixed(1)}%    \nRAM: ${(n.ram_used_gb||0).toFixed(1)}/${(n.ram_total_gb||0).toFixed(1)}G`}
  renderBigStats(nodes); renderTagChips(nodes);
  loadCharts();
}

let graphRange = '1h';
let prevOnline = {};

function relTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts.includes('T') || ts.includes('Z') ? ts : ts.replace(' ', 'T') + 'Z');
    const sec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec/60) + 'm ago';
    if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
    return Math.floor(sec/86400) + 'd ago';
  } catch(e) { return ts; }
}

async 
async function exportGraphsPng() {
  const canvases = [cCpu, cRam, cNet, cDisk].filter(c => c && !c.classList.contains('hidden'));
  if (!canvases.length) { toast('No graphs'); return; }
  // simple: download each
  canvases.forEach((cv, i) => {
    const a = document.createElement('a');
    a.href = cv.toDataURL('image/png');
    a.download = `pve-graph-${i+1}.png`;
    a.click();
  });
  toast('PNG exported');
}

function changeRange() {
  graphRange = document.getElementById('graphRange').value;
  await renderBigStats(nodes); renderTagChips(nodes);
  loadCharts();
}


function showMinMax(logs, key, elId) {
  if (!settings || settings.show_minmax === false) return;
  const vals = logs.map(x => x[key]).filter(v => v != null);
  if (!vals.length) return;
  const mn = Math.min(...vals), mx = Math.max(...vals), avg = vals.reduce((a,b)=>a+b,0)/vals.length;
  // append small text if container exists
}

async function loadCharts() {
  const logs = await fetch('/api/logs/range?range=' + graphRange).then(r => r.json());
  const times = logs.map(x => (x.timestamp||'').split(' ')[1] || '');
  charts.cpu.data.labels = times; charts.cpu.data.datasets[0].data = logs.map(x => x.cpu_usage); charts.cpu.update('none');
  charts.ram.data.labels = times; charts.ram.data.datasets[0].data = logs.map(x => x.ram_used_gb); charts.ram.update('none');
  charts.net.data.labels = times;
  charts.net.data.datasets[0].data = logs.map(x => x.net_in_kbps || 0);
  charts.net.data.datasets[1].data = logs.map(x => x.net_out_kbps || 0);
  charts.net.update('none');
  charts.disk.data.labels = times; charts.disk.data.datasets[0].data = logs.map(x => x.disk_pct || 0); charts.disk.update('none');
}


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

async function loadAlerts() {
  const list = document.getElementById('alertList');
  if (!list) return;
  const alerts = await fetch('/api/alerts?limit=20').then(r => r.json());
  if (!alerts.length) { list.innerHTML = '<div style="opacity:.6">No recent alerts</div>'; return; }
  list.innerHTML = alerts.slice(0, 12).map(a =>
    `<div style="padding:3px 0;border-bottom:1px solid var(--border);${a.acknowledged?'opacity:.5':''}">
      <b>${a.node_name||'?'}</b> ${a.message||a.alert_type}
      ${a.acknowledged?'':' <a href="#" onclick="ackOne('+a.id+');return false" style="color:var(--accent)">ack</a>'}
    </div>`
  ).join('');
}

async function loadActivity() {
  const list = document.getElementById('activityList');
  if (!list) return;
  const acts = await fetch('/api/activity?limit=15').then(r => r.json());
  if (!acts.length) { list.innerHTML = '<div style="opacity:.6">No activity yet</div>'; return; }
  list.innerHTML = acts.map(a =>
    `<div style="padding:2px 0">${(a.timestamp||'').split(' ')[1]||''} · <b>${a.action}</b> ${a.detail||''}</div>`
  ).join('');
}

async function ackAll() {
  await fetch('/api/alerts/ack', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
  toast('All alerts acknowledged');
  loadAlerts();
}

async function ackOne(id) {
  await fetch('/api/alerts/ack', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
  loadAlerts();
}

async function toggleFav(name, cur) {
  await fetch('/api/nodes/' + encodeURIComponent(name) + '/meta', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({favorite: !cur})
  });
  load();
}


function applyThemePack(name) {
  document.body.classList.remove('theme-cyberpunk','theme-terminal','theme-nord');
  if (name && name !== 'default') document.body.classList.add('theme-' + name);
}
function applyLayoutPreset(name) {
  document.body.classList.remove('layout-work','layout-night','layout-minimal');
  document.body.classList.add('layout-' + (name || 'work'));
  // minimal hides side cards
  const side = document.querySelector('.side');
  if (side) side.style.display = name === 'minimal' ? 'none' : '';
}
function toggleCinema() {
  document.body.classList.toggle('cinema');
  toast(document.body.classList.contains('cinema') ? 'Cinema mode' : 'Normal');
}


async function copyDiagnostics() {
  const [health, settings] = await Promise.all([
    fetch('/api/health').then(r=>r.json()).catch(()=>({})),
    fetch('/api/settings').then(r=>r.json()).catch(()=>({}))
  ]);
  const safe = {...settings};
  // already no passwords in /api/settings
  const txt = JSON.stringify({health, settings: safe, ua: navigator.userAgent, ts: new Date().toISOString()}, null, 2);
  await navigator.clipboard.writeText(txt);
  toast('Diagnostics copied');
}
function showQr() {
  const url = location.origin;
  // simple QR via external API (no dependency)
  const img = document.createElement('img');
  img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=' + encodeURIComponent(url);
  img.alt = 'QR';
  const box = document.getElementById('qrBox');
  box.innerHTML = '';
  box.appendChild(img);
  document.getElementById('qrModal').classList.add('open');
}

function applyDensity(d) {
  document.body.classList.remove('density-compact','density-comfortable','density-dense');
  document.body.classList.add('density-' + (d || 'comfortable'));
}

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
  const k = e.key.toLowerCase();
  if (k === 'r') { e.preventDefault(); load(); toast('Refreshed'); }
  if (k === 's') { e.preventDefault(); openSettings(); }
  if (k === 'a') { e.preventDefault(); openAddModal(); }
  if (k === 'c') { e.preventDefault(); toggleCinema(); }
  if (k === 'u') { e.preventDefault(); undoDelete(); }
  if (k === 'escape') { closeSettings(); closeAddModal(); closeGraphEdit(); }
});


async function openGuestsForActive() {
  const n = allNodesCache.find(x => x.online == 1) || allNodesCache[0];
  if (n) openGuests(n.node_name);
  else toast('No nodes');
}
function openGuests(name) {
  guestTitle.textContent = name + ' — Guests';
  guestList.innerHTML = 'Loading…';
  guestModal.classList.add('open');
  const r = await fetch('/api/nodes/' + encodeURIComponent(name) + '/guests');
  const j = await r.json();
  if (!j.ok) { guestList.innerHTML = 'Failed to load'; return; }
  if (!j.guests.length) { guestList.innerHTML = '<div style="opacity:.6;padding:12px">No VMs or containers</div>'; return; }
  guestList.innerHTML = j.guests.map(g => {
    const run = g.status === 'running';
    return `<div style="display:flex;align-items:center;gap:8px;padding:8px 4px;border-bottom:1px solid var(--border)">
      <span style="flex:1"><b>${g.name}</b> <span style="opacity:.6">(${g.type} ${g.vmid})</span><br>
      <span style="font-size:.75rem;color:${run?'var(--green)':'var(--muted)'}">${g.status}</span>
      ${run?` · CPU ${g.cpu}% · RAM ${g.mem}/${g.maxmem}G`:''}</span>
      <button class="btn btn-ghost" style="width:auto;padding:4px 8px;font-size:.72rem" onclick="guestAct('${name}',${g.vmid},'${g.type}','${run?'shutdown':'start'}')">${run?'Stop':'Start'}</button>
      ${run?`<button class="btn btn-ghost" style="width:auto;padding:4px 8px;font-size:.72rem" onclick="guestAct('${name}',${g.vmid},'${g.type}','reboot')">Reboot</button>`:''}
    </div>`;
  }).join('');
}
function closeGuests(){guestModal.classList.remove('open')}
async function guestAct(node, vmid, kind, action) {
  if (action !== 'start' && !confirm(action + ' ' + kind + '/' + vmid + '?')) return;
  const r = await fetch(`/api/nodes/${encodeURIComponent(node)}/guests/${vmid}/${action}?kind=${kind}`, {method:'POST'});
  const j = await r.json();
  toast(j.ok ? action + ' sent' : 'Failed');
  openGuests(node);
}

let bulkNodes = [];
async 
function updateQuickBar() {
  const boxes = [...document.querySelectorAll('[data-node-check]:checked')];
  const bar = document.getElementById('quickBar');
  if (!bar) return;
  bar.style.display = boxes.length ? 'flex' : 'none';
  document.getElementById('quickCount').textContent = boxes.length + ' selected';
}
document.addEventListener('change', e => {
  if (e.target.matches('[data-node-check]')) updateQuickBar();
});
function clearQuick() {
  document.querySelectorAll('[data-node-check]').forEach(c => c.checked = false);
  updateQuickBar();
}
async function quickPower(action) {
  const names = [...document.querySelectorAll('[data-node-check]:checked')].map(c => {
    const card = c.closest('.node-card');
    return card ? card.querySelector('.node-name span:last-child')?.textContent?.trim() : null;
  }).filter(Boolean);
  if (!names.length) return;
  if (!confirm(action + ' ' + names.length + ' node(s)?')) return;
  const r = await fetch('/api/power/bulk', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action, nodes: names})});
  const j = await r.json();
  const ok = Object.values(j.results||{}).filter(Boolean).length;
  toast(ok + '/' + names.length + ' ' + action + ' sent');
  clearQuick();
}

function openBulk() {
  const nodes = await fetch('/api/current').then(r=>r.json());
  bulkList.innerHTML = nodes.map(n =>
    `<label style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);cursor:pointer">
      <input type="checkbox" value="${n.node_name}" ${n.online==1?'':'disabled'}> ${n.node_name}
      <span style="margin-left:auto;font-size:.72rem;color:var(--muted)">${n.online==1?'online':'offline'}</span>
    </label>`
  ).join('');
  bulkModal.classList.add('open');
}
function closeBulk(){bulkModal.classList.remove('open')}
async function bulkPower(action) {
  const boxes = [...bulkList.querySelectorAll('input:checked')].map(c => c.value);
  if (!boxes.length) { toast('Select at least one node'); return; }
  if (!confirm(action + ' ' + boxes.length + ' node(s)?')) return;
  const r = await fetch('/api/power/bulk', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action, nodes: boxes})});
  const j = await r.json();
  const ok = Object.values(j.results||{}).filter(Boolean).length;
  toast(ok + '/' + boxes.length + ' ' + action + ' sent');
  closeBulk();
}

async function editNote(name) {
  const note = prompt('Note for ' + name + ' (leave empty to clear)', '');
  if (note === null) return;
  await fetch('/api/nodes/' + encodeURIComponent(name) + '/meta', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({note})
  });
  load();
}


let detailName = null;
let allNodesCache = [];


let activeTag = null;

function renderBigStats(nodes) {
  const box = document.getElementById('bigStats');
  if (!box) return;
  const online = nodes.filter(n => n.online == 1);
  const cpu = online.length ? (online.reduce((s,n)=>s+(n.cpu_usage||0),0)/online.length) : 0;
  const ram = online.length ? (online.reduce((s,n)=>s+(n.ram_used_gb||0),0)) : 0;
  box.innerHTML = `
    <div class="card" style="flex:1;min-width:100px;text-align:center;padding:10px">
      <div style="font-size:1.4rem;font-weight:700">${online.length}/${nodes.length}</div>
      <div style="font-size:.7rem;opacity:.6">Online</div>
    </div>
    <div class="card" style="flex:1;min-width:100px;text-align:center;padding:10px">
      <div style="font-size:1.4rem;font-weight:700">${cpu.toFixed(0)}%</div>
      <div style="font-size:.7rem;opacity:.6">Avg CPU</div>
    </div>
    <div class="card" style="flex:1;min-width:100px;text-align:center;padding:10px">
      <div style="font-size:1.4rem;font-weight:700">${ram.toFixed(1)}G</div>
      <div style="font-size:.7rem;opacity:.6">RAM used</div>
    </div>`;
}

function renderTagChips(nodes) {
  const box = document.getElementById('tagChips');
  if (!box) return;
  const tags = [...new Set(nodes.flatMap(n => n.tags || []))].sort();
  if (!tags.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<button class="btn btn-ghost" style="padding:3px 8px;font-size:.72rem" onclick="setTag(null)">All</button>` +
    tags.map(t => `<button class="btn btn-ghost" style="padding:3px 8px;font-size:.72rem${activeTag===t?';outline:1px solid var(--accent)':''}" onclick="setTag('${t}')">${t}</button>`).join('');
}
function setTag(t) {
  activeTag = t;
  document.querySelectorAll('#nodesCol .node-card').forEach(card => {
    const has = !t || card.textContent.toLowerCase().includes(t.toLowerCase());
    card.style.display = has ? '' : 'none';
  });
  renderTagChips(allNodesCache);
}

function filterNodes() {
  const q = (document.getElementById('nodeSearch')?.value || '').toLowerCase().trim();
  document.querySelectorAll('#nodesCol .node-card').forEach(card => {
    const text = card.textContent.toLowerCase();
    card.style.display = !q || text.includes(q) ? '' : 'none';
  });
}

function openDetailByName(name){const n=allNodesCache.find(x=>x.node_name===name);if(n)openDetail(n)}

async function loadDetailCharts(name) {
  const logs = await fetch('/api/logs/range?range=1h&node=' + encodeURIComponent(name)).then(r=>r.json());
  // reuse existing chart objects if present, otherwise skip
  if (typeof charts !== 'undefined' && charts.cpu) {
    // temporary – just show a toast with sample count
    toast(logs.length + ' samples loaded for ' + name);
  }
}

function openDetail(n) {
  detailName = n.node_name;
  detailTitle.textContent = n.node_name;
  detailSub.textContent = (n.ip||'') + ' · ' + (n.online==1?'online':'offline') + ' · last ' + relTime(n.timestamp);
  dNote.value = n.note || '';
  dTags.value = (n.tags||[]).join(', ');
  dCpu.value = n.cpu_alert != null ? n.cpu_alert : '';
  dRam.value = n.ram_alert != null ? n.ram_alert : '';
  dDisk.value = n.disk_alert != null ? n.disk_alert : '';
  detailModal.classList.add('open');
  // quick guests button if not present
  if (!document.getElementById('detailGuestsBtn')) {
    const b = document.createElement('button'); b.id='detailGuestsBtn'; b.className='btn btn-ghost';
    b.textContent='View Guests'; b.style.marginTop='8px';
    b.onclick=()=>{closeDetail(); openGuests(detailName);};
    detailModal.querySelector('.modal-box')?.appendChild(b);
  }
  loadDetailCharts(n.node_name);
}
function closeDetail(){detailModal.classList.remove('open');detailName=null}
async function saveDetail() {
  if (!detailName) return;
  const tags = dTags.value.split(',').map(s=>s.trim()).filter(Boolean);
  const payload = {
    note: dNote.value,
    tags,
    cpu_alert: dCpu.value === '' ? null : parseFloat(dCpu.value),
    ram_alert: dRam.value === '' ? null : parseFloat(dRam.value),
    disk_alert: dDisk.value === '' ? null : parseFloat(dDisk.value),
  };
  await fetch('/api/nodes/' + encodeURIComponent(detailName) + '/meta', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
  });
  toast('Saved');
  closeDetail();
  load();
}


async function openDiscover() {
  toast('Scanning LAN for :8006 …');
  const r = await fetch('/api/discover').then(x => x.json());
  if (!r.ok) { toast(r.error || 'Scan failed'); return; }
  if (!r.nodes.length) { toast('No Proxmox hosts found'); return; }
  const list = r.nodes.map(n =>
    `<label style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
      <input type="checkbox" value="${n.ip}" data-name="${n.name}"> ${n.ip}
    </label>`
  ).join('');
  const html = `<div id="discModal" class="modal open"><div class="modal-box" style="max-width:360px">
    <h3>Discovered hosts</h3><div style="max-height:240px;overflow:auto">${list}</div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn btn-primary" onclick="importDiscovered()">Add selected</button>
      <button class="btn btn-ghost" onclick="document.getElementById('discModal').remove()">Cancel</button>
    </div></div></div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}
async function importDiscovered() {
  const boxes = [...document.querySelectorAll('#discModal input:checked')];
  if (!boxes.length) { toast('Select at least one'); return; }
  const nodes = boxes.map(b => ({name: b.dataset.name, ip: b.value, node: 'pve', user: 'root@pam', password: ''}));
  const r = await fetch('/api/nodes/import', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({nodes})});
  const j = await r.json();
  toast(j.ok ? ('Added ' + j.added) : (j.error || 'Failed'));
  document.getElementById('discModal')?.remove();
  load();
}

function openImport(){importText.value='';importModal.classList.add('open')}
function closeImport(){importModal.classList.remove('open')}
async function doImport() {
  let nodes;
  try { nodes = JSON.parse(importText.value); }
  catch(e) { toast('Invalid JSON'); return; }
  if (!Array.isArray(nodes)) { toast('Need a JSON array'); return; }
  const r = await fetch('/api/nodes/import', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({nodes})
  });
  const j = await r.json();
  toast(j.ok ? ('Imported ' + j.added + ' node(s)') : (j.error || 'Failed'));
  if (j.ok) { closeImport(); load(); }
}

async function restoreConfig() {
  const raw = prompt('Paste config JSON (from backup). Passwords are not restored.');
  if (!raw) return;
  let data;
  try { data = JSON.parse(raw); } catch(e) { toast('Invalid JSON'); return; }
  const r = await fetch('/api/config/restore', {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)
  });
  const j = await r.json();
  toast(j.ok ? 'Settings restored' : 'Failed');
  if (j.ok) loadSettings().then(load);
}

function exportJson() {
  const r = document.getElementById('graphRange')?.value || '1h';
  window.location = '/api/export/json?range=' + r;
}

function applyAccent(c) {
  if (!c) return;
  document.documentElement.style.setProperty('--accent', c);
}

function openEdit(name,ip,node,type){openAddModal(name);mName.value=name;mIp.value=ip;mNode.value=node;setType(type==='node'?'node':'server')}
loadSettings().then(()=>{load();loadAlerts();loadActivity();});
setInterval(()=>{loadAlerts();loadActivity();}, 15000);
</script>
<div id="quickBar" style="display:none;position:fixed;bottom:18px;left:50%;transform:translateX(-50%);
  background:var(--card);border:1px solid var(--border);border-radius:12px;padding:10px 16px;
  box-shadow:0 8px 30px rgba(0,0,0,.45);z-index:100;gap:10px;align-items:center">
  <span id="quickCount" style="font-size:.85rem;opacity:.8">0 selected</span>
  <button class="btn-reboot" style="padding:6px 12px" onclick="quickPower('reboot')">Reboot</button>
  <button class="btn-shutdown" style="padding:6px 12px" onclick="quickPower('shutdown')">Shutdown</button>
  <button class="btn btn-ghost" style="padding:6px 10px" onclick="clearQuick()">Clear</button>
</div>


<div id="alertHistoryModal" class="modal">
  <div class="modal-box" style="max-width:480px;max-height:80vh;overflow:auto">
    <h3>Alert history</h3>
    <div id="alertHistoryList" style="font-size:.85rem"></div>
    <button class="btn btn-ghost" style="margin-top:12px" onclick="closeAlertHistory()">Close</button>
  </div>
</div>


<div id="pinOverlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:9999;
  align-items:center;justify-content:center;flex-direction:column;gap:12px">
  <div style="font-size:1.1rem;opacity:.8">Enter PIN</div>
  <input id="pinInput" type="password" maxlength="8" style="font-size:1.4rem;text-align:center;width:140px;
    padding:10px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:inherit"
    onkeydown="if(event.key==='Enter')checkPin()">
  <button class="btn btn-primary" onclick="checkPin()">Unlock</button>
  <div id="pinErr" style="color:#f87171;font-size:.85rem;display:none">Wrong PIN</div>
</div>


<div style="text-align:center;padding:16px 8px 24px;opacity:.55;font-size:.75rem">
  PVE Node Monitor <span id="verLabel">v1.9.0</span> ·
  <a href="#" onclick="copyDiagnostics();return false" style="color:var(--accent)">Copy diagnostics</a> ·
  <a href="#" onclick="showQr();return false" style="color:var(--accent)">QR</a>
</div>
<div id="qrModal" class="modal">
  <div class="modal-box" style="text-align:center;max-width:280px">
    <h3>Open on phone</h3>
    <div id="qrBox" style="margin:12px auto"></div>
    <button class="btn btn-ghost" onclick="document.getElementById('qrModal').classList.remove('open')">Close</button>
  </div>
</div>


<nav id="bottomNav" style="display:none;position:fixed;bottom:0;left:0;right:0;background:var(--card);
  border-top:1px solid var(--border);padding:8px 0;z-index:40;justify-content:space-around">
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="window.scrollTo(0,0)">Nodes</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="openSettings()">Settings</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="openAddModal()">Add</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="toggleCinema()">Cinema</button>
</nav>

</body></html>"""
