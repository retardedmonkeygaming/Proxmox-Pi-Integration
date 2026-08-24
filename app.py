import sqlite3
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
    return [dict(r) for r in rows if r["node_name"] in valid]

@app.get("/api/logs/server")
def api_logs(limit: int = 40):
    conn = get_db()
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
    }
    if not new["name"] or not new["ip"]:
        return JSONResponse({"ok": False, "error": "name and ip required"}, 400)
    # prevent duplicate names
    if any(n["name"] == new["name"] for n in cfg["nodes"]):
        return JSONResponse({"ok": False, "error": "name already exists"}, 400)
    cfg["nodes"].append(new)
    config.save_config(cfg)
    return {"ok": True}

@app.delete("/api/nodes/{name}")
def api_delete_node(name: str):
    cfg = config.load_config()
    cfg["nodes"] = [n for n in cfg["nodes"] if n["name"] != name]
    config.save_config(cfg)
    return {"ok": True}

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
        })
        i += 1
    if not nodes:
        return HTMLResponse("Need at least one node", 400)
    cfg = config.load_config()
    cfg["nodes"] = nodes
    cfg["setup_done"] = True
    cfg["log_interval"] = int(form.get("log_interval", 10))
    cfg["buzzer_enabled"] = form.get("buzzer") == "on"
    config.save_config(cfg)
    return RedirectResponse("/", status_code=303)

@app.get("/", response_class=HTMLResponse)
def dashboard():
    cfg = config.load_config()
    if not cfg.get("setup_done") or not cfg.get("nodes"):
        return RedirectResponse("/setup")
    return DASHBOARD_HTML

# -------------------- SETUP HTML --------------------
SETUP_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor – Setup</title>
<style>
:root{--bg:#0b1120;--card:#1e293b;--accent:#38bdf8}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:linear-gradient(135deg,#0b1120,#1e1b4b);color:#f1f5f9;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:rgba(30,41,59,.92);backdrop-filter:blur(16px);border-radius:20px;padding:32px;max-width:540px;width:100%;box-shadow:0 25px 50px -12px #0008}
h1{margin:0 0 6px;color:var(--accent);font-size:1.8rem}
.sub{color:#94a3b8;margin-bottom:24px}
label{display:block;margin:14px 0 5px;font-size:.9rem;color:#94a3b8}
input,select{width:100%;padding:11px 14px;border-radius:10px;border:1px solid #334155;background:#0f172a;color:#f1f5f9;font-size:1rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.btn{margin-top:28px;width:100%;padding:14px;border:none;border-radius:12px;background:var(--accent);color:#0f172a;font-weight:700;font-size:1.1rem;cursor:pointer}
.node-block{border:1px solid #334155;border-radius:14px;padding:18px;margin-bottom:16px}
.add{background:transparent;border:1px dashed #64748b;color:#94a3b8;width:100%;padding:11px;border-radius:10px;cursor:pointer}
.check-row{display:flex;align-items:center;gap:10px;margin-top:18px}
.check-row input{width:18px;height:18px;margin:0}
.check-row label{margin:0;color:#e2e8f0;font-size:1rem}
</style></head><body>
<div class="card">
<h1>PVE Node Monitor</h1>
<p class="sub">First-time setup</p>
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
<label style="margin-top:22px">Log interval</label>
<select name="log_interval"><option>5</option><option selected>10</option><option>30</option><option>60</option></select>
<div class="check-row">
<input type="checkbox" name="buzzer" id="buzzer" checked>
<label for="buzzer">Enable buzzer</label>
</div>
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

# -------------------- DASHBOARD HTML --------------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{--bg:#0b1120;--card:#1e293b;--accent:#38bdf8;--green:#22c55e;--red:#ef4444;--orange:#f97316;--purple:#a855f7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:#f1f5f9;font-family:system-ui,sans-serif;padding:20px 16px 60px;min-height:100vh}
h1{text-align:center;color:var(--accent);margin:0 0 4px;font-size:1.9rem}
.meta{text-align:center;color:#94a3b8;margin-bottom:18px;font-size:.9rem}
.toolbar{display:flex;flex-wrap:wrap;gap:12px;justify-content:center;margin-bottom:20px;align-items:center}
.toolbar label{display:flex;align-items:center;gap:6px;font-size:.9rem;color:#cbd5e1;cursor:pointer}
.toolbar input{width:16px;height:16px}
.btn{background:var(--accent);color:#0f172a;border:none;padding:8px 16px;border-radius:8px;font-weight:600;cursor:pointer}
.btn-outline{background:transparent;border:1px solid #475569;color:#e2e8f0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px;max-width:1200px;margin:0 auto}
.grid.single{display:flex;justify-content:center}
.grid.single .card{width:100%;max-width:420px}
.card{background:var(--card);border-radius:16px;padding:20px;position:relative;transition:transform .2s}
.card:hover{transform:translateY(-2px)}
.card.offline{opacity:.75;border:1px solid var(--red)}
.badge{position:absolute;top:16px;right:16px;padding:4px 11px;border-radius:999px;font-size:.72rem;font-weight:700}
.online{background:rgba(34,197,94,.18);color:var(--green)}
.offlineb{background:rgba(239,68,68,.18);color:var(--red)}
h2{margin:0 0 14px;font-size:1.25rem}
.stat{display:flex;justify-content:space-between;margin:7px 0;font-size:.95rem}
.bar{height:7px;background:#334155;border-radius:4px;margin:3px 0 11px;overflow:hidden}
.bar>div{height:100%;border-radius:4px}
.actions{margin-top:16px;display:flex;gap:10px}
.actions button{flex:1;padding:9px;border:none;border-radius:9px;font-weight:600;cursor:pointer;font-size:.9rem}
.btn-reboot{background:var(--orange);color:#000}
.btn-shutdown{background:var(--red);color:#fff}
.actions button:disabled{opacity:.4;cursor:not-allowed}
.charts{max-width:1200px;margin:28px auto 0;display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}
.chart-card{background:var(--card);border-radius:16px;padding:16px}
.chart-card h3{margin:0 0 10px;font-size:1rem;color:#94a3b8}
canvas{max-height:180px}
.hidden{display:none!important}
footer{position:fixed;bottom:0;left:0;right:0;background:#0f172a;border-top:1px solid #1e293b;padding:10px 16px;text-align:center;font-size:.85rem;color:#64748b}
footer a{color:var(--accent);text-decoration:none;margin:0 8px}
footer a:hover{text-decoration:underline}

/* Modal */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.7);display:flex;align-items:center;justify-content:center;z-index:100;padding:16px}
.modal{background:var(--card);border-radius:20px;padding:28px;max-width:480px;width:100%;max-height:90vh;overflow-y:auto}
.modal h2{margin:0 0 16px;color:var(--accent)}
.choice{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}
.choice button{padding:18px 12px;border-radius:14px;border:2px solid #334155;background:#0f172a;color:#e2e8f0;font-weight:600;cursor:pointer;font-size:1rem}
.choice button.active{border-color:var(--accent);background:rgba(56,189,248,.12);color:var(--accent)}
.modal label{display:block;margin:12px 0 4px;font-size:.9rem;color:#94a3b8}
.modal input{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #334155;background:#0f172a;color:#f1f5f9}
.modal-actions{display:flex;gap:10px;margin-top:22px}
.modal-actions button{flex:1;padding:12px;border:none;border-radius:10px;font-weight:600;cursor:pointer}
.btn-cancel{background:#334155;color:#e2e8f0}
.btn-save{background:var(--accent);color:#0f172a}
</style></head><body>
<h1>PVE Node Monitor</h1>
<div class="meta" id="clock">Loading…</div>

<div class="toolbar">
  <button class="btn" onclick="load()">↻ Refresh</button>
  <button class="btn btn-outline" onclick="openAddModal()">+ Add Node / Server</button>
  <label><input type="checkbox" id="gCpu" checked onchange="toggleCharts()"> CPU</label>
  <label><input type="checkbox" id="gRam" checked onchange="toggleCharts()"> RAM</label>
  <label><input type="checkbox" id="gNet" checked onchange="toggleCharts()"> Network</label>
  <label><input type="checkbox" id="gDisk" onchange="toggleCharts()"> Disk</label>
</div>

<div class="grid" id="nodes"></div>

<div class="charts">
  <div class="chart-card" id="cCpu"><h3>CPU %</h3><canvas id="chartCpu"></canvas></div>
  <div class="chart-card" id="cRam"><h3>RAM Used (GB)</h3><canvas id="chartRam"></canvas></div>
  <div class="chart-card" id="cNet"><h3>Network (KB/s)</h3><canvas id="chartNet"></canvas></div>
  <div class="chart-card hidden" id="cDisk"><h3>Disk %</h3><canvas id="chartDisk"></canvas></div>
</div>

<footer>
  Insta: <a href="https://instagram.com/vxprxx" target="_blank">vxprxx</a> ·
  GitHub: <a href="https://github.com/retardedmonkeygaming" target="_blank">retardedmonkeygaming</a>
</footer>

<!-- Add Modal -->
<div id="addModal" class="modal-bg hidden">
  <div class="modal">
    <h2>Add Node / Server</h2>
    <div class="choice">
      <button type="button" id="btnNode" class="active" onclick="setType('node')">New Node</button>
      <button type="button" id="btnServer" onclick="setType('server')">New Server</button>
    </div>
    <p id="typeHint" style="color:#94a3b8;font-size:.9rem;margin:0 0 12px">Add another node on an existing Proxmox host</p>
    <label>Friendly name</label><input id="mName" placeholder="e.g. Precision-2">
    <label>IP / Hostname</label><input id="mIp" placeholder="192.168.x.x">
    <label>Proxmox node name</label><input id="mNode" placeholder="pve">
    <label>User</label><input id="mUser" value="root@pam">
    <label>Password</label><input id="mPass" type="password">
    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeAddModal()">Cancel</button>
      <button class="btn-save" onclick="saveNode()">Save</button>
    </div>
  </div>
</div>

<script>
let addType = 'node';
const charts = {};
function makeChart(id, color) {
  return new Chart(document.getElementById(id), {
    type:'line', data:{labels:[], datasets:[{data:[], borderColor:color, tension:.3, pointRadius:0, borderWidth:2}]},
    options:{responsive:true, animation:false, plugins:{legend:{display:false}},
             scales:{x:{ticks:{maxTicksLimit:6,color:'#64748b'}}, y:{ticks:{color:'#64748b'}, grid:{color:'#1e293b'}}}}
  });
}
charts.cpu = makeChart('chartCpu', '#38bdf8');
charts.ram = makeChart('chartRam', '#a855f7');
charts.net = new Chart(document.getElementById('chartNet'), {
  type:'line', data:{labels:[], datasets:[
    {label:'Down', data:[], borderColor:'#22c55e', tension:.3, pointRadius:0, borderWidth:2},
    {label:'Up', data:[], borderColor:'#f97316', tension:.3, pointRadius:0, borderWidth:2}
  ]},
  options:{responsive:true, animation:false, plugins:{legend:{position:'bottom', labels:{color:'#94a3b8'}}},
           scales:{x:{ticks:{maxTicksLimit:6,color:'#64748b'}}, y:{ticks:{color:'#64748b'}, grid:{color:'#1e293b'}}}}
});
charts.disk = makeChart('chartDisk', '#eab308');

function toggleCharts() {
  cCpu.classList.toggle('hidden', !gCpu.checked);
  cRam.classList.toggle('hidden', !gRam.checked);
  cNet.classList.toggle('hidden', !gNet.checked);
  cDisk.classList.toggle('hidden', !gDisk.checked);
}

function openAddModal() {
  addModal.classList.remove('hidden');
  setType('node');
}
function closeAddModal() { addModal.classList.add('hidden'); }
function setType(t) {
  addType = t;
  btnNode.classList.toggle('active', t==='node');
  btnServer.classList.toggle('active', t==='server');
  typeHint.textContent = t==='node'
    ? 'Add another node on an existing Proxmox host'
    : 'Add a completely independent Proxmox server';
}

async function saveNode() {
  const payload = {
    name: mName.value.trim(),
    ip: mIp.value.trim(),
    node: mNode.value.trim() || mName.value.trim(),
    user: mUser.value.trim() || 'root@pam',
    password: mPass.value
  };
  if (!payload.name || !payload.ip) { alert('Name and IP required'); return; }
  const r = await fetch('/api/nodes/add', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
  });
  const j = await r.json();
  if (j.ok) {
    closeAddModal();
    mName.value = mIp.value = mNode.value = mPass.value = '';
    load();
    alert('Node added! It will appear after the next poll cycle.');
  } else {
    alert('Error: ' + (j.error || 'unknown'));
  }
}

async function load() {
  const [nodes, logs] = await Promise.all([
    fetch('/api/current').then(r=>r.json()),
    fetch('/api/logs/server?limit=40').then(r=>r.json())
  ]);
  const grid = document.getElementById('nodes');
  grid.className = nodes.length <= 1 ? 'grid single' : 'grid';
  grid.innerHTML = '';
  nodes.forEach(n => {
    const online = n.online == 1;
    const card = document.createElement('div');
    card.className = 'card' + (online ? '' : ' offline');
    const last = n.timestamp ? n.timestamp.split(' ')[1] : '—';
    card.innerHTML = `
      <span class="badge ${online?'online':'offlineb'}">${online?'ONLINE':'OFFLINE'}</span>
      <h2>${n.node_name}</h2>
      <div class="stat"><span>CPU:</span><span>${(n.cpu_usage||0).toFixed(1)}%</span></div>
      <div class="bar"><div style="width:${n.cpu_usage||0}%;background:var(--accent)"></div></div>
      <div class="stat"><span>RAM:</span><span>${(n.ram_used_gb||0).toFixed(1)} / ${(n.ram_total_gb||0).toFixed(1)} GB</span></div>
      <div class="bar"><div style="width:${((n.ram_used_gb/n.ram_total_gb)*100)||0}%;background:var(--purple)"></div></div>
      <div class="stat"><span>Disk:</span><span>${(n.disk_pct||0).toFixed(1)}%</span></div>
      <div class="stat"><span>VMs:</span><span>${n.active_vms||0}</span></div>
      <div class="stat"><span>Net ↓ / ↑:</span><span>${(n.net_in_kbps||0).toFixed(0)} / ${(n.net_out_kbps||0).toFixed(0)} KB/s</span></div>
      <div class="stat" style="font-size:.8rem;color:#64748b"><span>Last seen:</span><span>${last}</span></div>
      <div class="actions">
        <button class="btn-reboot" ${online?'':'disabled'} onclick="power('${n.node_name}','reboot')">Reboot</button>
        <button class="btn-shutdown" ${online?'':'disabled'} onclick="power('${n.node_name}','shutdown')">Shutdown</button>
      </div>`;
    grid.appendChild(card);
  });

  const times = logs.map(x => (x.timestamp||'').split(' ')[1]||'');
  charts.cpu.data.labels = times; charts.cpu.data.datasets[0].data = logs.map(x=>x.cpu_usage); charts.cpu.update('none');
  charts.ram.data.labels = times; charts.ram.data.datasets[0].data = logs.map(x=>x.ram_used_gb); charts.ram.update('none');
  charts.net.data.labels = times;
  charts.net.data.datasets[0].data = logs.map(x=>x.net_in_kbps||0);
  charts.net.data.datasets[1].data = logs.map(x=>x.net_out_kbps||0); charts.net.update('none');
  charts.disk.data.labels = times; charts.disk.data.datasets[0].data = logs.map(x=>x.disk_pct||0); charts.disk.update('none');
  clock.textContent = 'Updated ' + new Date().toLocaleTimeString();
}

async function power(name, action) {
  if (!confirm(`Really ${action} "${name}"?`)) return;
  const r = await fetch(`/api/power/${name}/${action}`, {method:'POST'});
  const j = await r.json();
  alert(j.ok ? `${action} sent` : 'Failed');
}

load();
setInterval(load, 5000);
</script></body></html>"""