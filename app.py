import sqlite3
import time
from typing import Any, Dict, List

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import config
from monitor import ProxmoxManager

app = FastAPI(title="PVE Node Monitor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB = "monitor.db"

def get_db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

# ---------- API ----------
@app.get("/api/current")
def api_current():
    cfg = config.load_config()
    # Live poll would be heavy; we use last DB rows per node
    conn = get_db()
    rows = conn.execute("""
        SELECT node_name, cpu_usage, ram_used_gb, ram_total_gb, disk_pct,
               net_in_kbps, net_out_kbps, active_vms, online, timestamp
        FROM server_logs
        WHERE id IN (SELECT MAX(id) FROM server_logs GROUP BY node_name)
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/power/{node_name}/{action}")
def api_power(node_name: str, action: str):
    if action not in ("shutdown", "reboot"):
        return JSONResponse({"ok": False, "error": "invalid action"}, 400)
    cfg = config.load_config()
    mgr = ProxmoxManager(cfg["nodes"])
    client = mgr.get_client(node_name)
    if not client:
        return JSONResponse({"ok": False, "error": "node not found"}, 404)
    ok = client.power(action)
    return {"ok": ok}

# ---------- Setup Wizard (first boot) ----------
@app.get("/setup", response_class=HTMLResponse)
def setup_page():
    cfg = config.load_config()
    if cfg.get("setup_done"):
        return RedirectResponse("/")
    return SETUP_HTML

@app.post("/setup")
async def setup_submit(request: Request):
    form = await request.form()
    nodes = []
    # Simple multi-node form handling (name_0, ip_0 …)
    i = 0
    while True:
        name = form.get(f"name_{i}")
        if not name:
            break
        nodes.append({
            "name": name,
            "ip": form.get(f"ip_{i}", ""),
            "node": form.get(f"node_{i}", name),
            "user": form.get(f"user_{i}", "root@pam"),
            "password": form.get(f"password_{i}", ""),
        })
        i += 1
    if not nodes:
        return HTMLResponse("At least one node required", 400)

    cfg = config.load_config()
    cfg["nodes"] = nodes
    cfg["setup_done"] = True
    cfg["log_interval"] = int(form.get("log_interval", 10))
    cfg["buzzer_enabled"] = form.get("buzzer") == "on"
    config.save_config(cfg)
    return RedirectResponse("/", status_code=303)

# ---------- Main Dashboard ----------
@app.get("/", response_class=HTMLResponse)
def dashboard():
    cfg = config.load_config()
    if not cfg.get("setup_done"):
        return RedirectResponse("/setup")
    return DASHBOARD_HTML

# ---------- HTML Templates (kept compact but beautiful) ----------
SETUP_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor – Setup</title>
<style>
:root{--bg:#0b1120;--card:#1e293b;--accent:#38bdf8;--text:#f1f5f9}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:linear-gradient(135deg,#0b1120,#1e1b4b);color:var(--text);font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:rgba(30,41,59,.85);backdrop-filter:blur(12px);border-radius:20px;padding:32px;max-width:520px;width:100%;box-shadow:0 25px 50px -12px rgb(0 0 0 / .5)}
h1{margin:0 0 8px;color:var(--accent);font-size:1.8rem}
.sub{color:#94a3b8;margin-bottom:24px}
label{display:block;margin:12px 0 4px;font-size:.9rem;color:#94a3b8}
input,select{width:100%;padding:10px 14px;border-radius:10px;border:1px solid #334155;background:#0f172a;color:#f1f5f9;font-size:1rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.btn{margin-top:24px;width:100%;padding:14px;border:none;border-radius:12px;background:var(--accent);color:#0f172a;font-weight:700;font-size:1.1rem;cursor:pointer}
.btn:hover{filter:brightness(1.1)}
.node-block{border:1px solid #334155;border-radius:12px;padding:16px;margin-bottom:16px}
.add{background:transparent;border:1px dashed #64748b;color:#94a3b8;width:100%;padding:10px;border-radius:10px;cursor:pointer}
</style></head><body>
<div class="card">
<h1>PVE Node Monitor</h1>
<p class="sub">First-time setup – add one or more Proxmox nodes</p>
<form method="post" action="/setup" id="f">
<div id="nodes">
<div class="node-block">
<label>Friendly name</label><input name="name_0" value="pve" required>
<label>IP / Hostname</label><input name="ip_0" placeholder="192.168.1.10" required>
<label>Node name (inside Proxmox)</label><input name="node_0" value="pve" required>
<div class="row">
<div><label>User</label><input name="user_0" value="root@pam"></div>
<div><label>Password</label><input name="password_0" type="password" required></div>
</div>
</div>
</div>
<button type="button" class="add" onclick="addNode()">+ Add another node</button>
<label style="margin-top:20px">Log interval (seconds)</label>
<select name="log_interval"><option>5</option><option selected>10</option><option>30</option><option>60</option></select>
<label><input type="checkbox" name="buzzer" checked> Enable buzzer</label>
<button class="btn" type="submit">Save & Start Monitoring</button>
</form>
</div>
<script>
let idx=1;
function addNode(){
  const html=`<div class="node-block">
<label>Friendly name</label><input name="name_${idx}" required>
<label>IP / Hostname</label><input name="ip_${idx}" required>
<label>Node name</label><input name="node_${idx}" required>
<div class="row">
<div><label>User</label><input name="user_${idx}" value="root@pam"></div>
<div><label>Password</label><input name="password_${idx}" type="password" required></div>
</div></div>`;
  document.getElementById('nodes').insertAdjacentHTML('beforeend',html);
  idx++;
}
</script></body></html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{--bg:#0b1120;--card:#1e293b;--accent:#38bdf8;--green:#22c55e;--red:#ef4444;--orange:#f97316}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:#f1f5f9;font-family:system-ui,sans-serif;padding:20px}
h1{text-align:center;color:var(--accent);margin:0 0 6px}
.meta{text-align:center;color:#94a3b8;margin-bottom:24px;font-size:.9rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px;max-width:1400px;margin:0 auto}
.card{background:var(--card);border-radius:16px;padding:18px;position:relative;overflow:hidden}
.card.offline{opacity:.7;border:1px solid var(--red)}
.badge{position:absolute;top:14px;right:14px;padding:4px 10px;border-radius:999px;font-size:.75rem;font-weight:600}
.online{background:rgba(34,197,94,.2);color:var(--green)}
.offlineb{background:rgba(239,68,68,.2);color:var(--red)}
h2{margin:0 0 12px;font-size:1.15rem}
.stat{display:flex;justify-content:space-between;margin:6px 0;font-size:.95rem}
.bar{height:6px;background:#334155;border-radius:3px;margin:4px 0 10px;overflow:hidden}
.bar>div{height:100%;border-radius:3px}
.actions{margin-top:14px;display:flex;gap:8px}
.actions button{flex:1;padding:8px;border:none;border-radius:8px;font-weight:600;cursor:pointer}
.btn-reboot{background:var(--orange);color:#000}
.btn-shutdown{background:var(--red);color:#fff}
canvas{max-height:160px}
</style></head><body>
<h1>PVE Node Monitor</h1>
<div class="meta" id="clock">Loading…</div>
<div class="grid" id="nodes"></div>
<script>
async function load(){
  const data = await fetch('/api/current').then(r=>r.json());
  const el = document.getElementById('nodes');
  el.innerHTML = '';
  data.forEach(n=>{
    const online = n.online == 1;
    const card = document.createElement('div');
    card.className = 'card' + (online ? '' : ' offline');
    card.innerHTML = `
      <span class="badge ${online?'online':'offlineb'}">${online?'ONLINE':'OFFLINE'}</span>
      <h2>${n.node_name}</h2>
      <div class="stat"><span>CPU</span><span>${n.cpu_usage?.toFixed(1)||0}%</span></div>
      <div class="bar"><div style="width:${n.cpu_usage||0}%;background:var(--accent)"></div></div>
      <div class="stat"><span>RAM</span><span>${n.ram_used_gb?.toFixed(1)||0} / ${n.ram_total_gb?.toFixed(1)||0} GB</span></div>
      <div class="bar"><div style="width:${(n.ram_used_gb/n.ram_total_gb*100)||0}%;background:#a855f7"></div></div>
      <div class="stat"><span>Disk</span><span>${n.disk_pct?.toFixed(1)||0}%</span></div>
      <div class="stat"><span>VMs</span><span>${n.active_vms||0}</span></div>
      <div class="stat"><span>Net ↓ / ↑</span><span>${(n.net_in_kbps||0).toFixed(0)} / ${(n.net_out_kbps||0).toFixed(0)} KB/s</span></div>
      <div class="actions">
        <button class="btn-reboot" onclick="power('${n.node_name}','reboot')">Reboot</button>
        <button class="btn-shutdown" onclick="power('${n.node_name}','shutdown')">Shutdown</button>
      </div>`;
    el.appendChild(card);
  });
  document.getElementById('clock').textContent = 'Updated ' + new Date().toLocaleTimeString();
}
async function power(name, action){
  if(!confirm(`Really ${action} node "${name}"?`)) return;
  const r = await fetch(`/api/power/${name}/${action}`, {method:'POST'});
  const j = await r.json();
  alert(j.ok ? `${action} sent` : 'Failed: '+(j.error||''));
}
load(); setInterval(load, 5000);
</script></body></html>"""