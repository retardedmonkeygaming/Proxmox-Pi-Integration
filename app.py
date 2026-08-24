import sqlite3
from typing import Any, Dict, List

from fastapi import FastAPI, Request
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
    valid_names = {n["name"] for n in cfg.get("nodes", [])}

    conn = get_db()
    rows = conn.execute("""
        SELECT node_name, cpu_usage, ram_used_gb, ram_total_gb, disk_pct,
               net_in_kbps, net_out_kbps, active_vms, online, timestamp
        FROM server_logs
        WHERE id IN (SELECT MAX(id) FROM server_logs GROUP BY node_name)
          AND node_name IS NOT NULL
          AND node_name != ''
          AND node_name != 'null'
    """).fetchall()
    conn.close()

    # Only keep nodes that actually exist in config
    result = [dict(r) for r in rows if r["node_name"] in valid_names]
    return result

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
        return JSONResponse({"ok": False, "error": "invalid action"}, 400)
    cfg = config.load_config()
    mgr = ProxmoxManager(cfg["nodes"])
    client = mgr.get_client(node_name)
    if not client:
        return JSONResponse({"ok": False, "error": "node not found"}, 404)
    ok = client.power(action)
    return {"ok": ok}

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
        return HTMLResponse("At least one node required", 400)

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
    if not cfg.get("setup_done"):
        return RedirectResponse("/setup")
    return DASHBOARD_HTML

# ===================== SETUP HTML =====================
SETUP_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor – Setup</title>
<style>
:root{--bg:#0b1120;--card:#1e293b;--accent:#38bdf8;--text:#f1f5f9}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:linear-gradient(135deg,#0b1120,#1e1b4b);color:var(--text);font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:rgba(30,41,59,.9);backdrop-filter:blur(16px);border-radius:20px;padding:32px;max-width:540px;width:100%;box-shadow:0 25px 50px -12px rgb(0 0 0 / .5)}
h1{margin:0 0 6px;color:var(--accent);font-size:1.8rem}
.sub{color:#94a3b8;margin-bottom:24px}
label{display:block;margin:14px 0 5px;font-size:.9rem;color:#94a3b8}
input,select{width:100%;padding:11px 14px;border-radius:10px;border:1px solid #334155;background:#0f172a;color:#f1f5f9;font-size:1rem}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.btn{margin-top:28px;width:100%;padding:14px;border:none;border-radius:12px;background:var(--accent);color:#0f172a;font-weight:700;font-size:1.1rem;cursor:pointer}
.btn:hover{filter:brightness(1.1)}
.node-block{border:1px solid #334155;border-radius:14px;padding:18px;margin-bottom:16px}
.add{background:transparent;border:1px dashed #64748b;color:#94a3b8;width:100%;padding:11px;border-radius:10px;cursor:pointer;margin-top:4px}
.check-row{display:flex;align-items:center;gap:10px;margin-top:18px}
.check-row input{width:18px;height:18px;margin:0}
.check-row label{margin:0;color:#e2e8f0;font-size:1rem}
</style></head><body>
<div class="card">
<h1>PVE Node Monitor</h1>
<p class="sub">First-time setup – add one or more Proxmox nodes</p>
<form method="post" action="/setup" id="f">
<div id="nodes">
<div class="node-block">
<label>Friendly name</label><input name="name_0" value="Precision" required>
<label>IP / Hostname</label><input name="ip_0" placeholder="192.168.x.x" required>
<label>Node name (inside Proxmox)</label><input name="node_0" value="pve" required>
<div class="row">
<div><label>User</label><input name="user_0" value="root@pam"></div>
<div><label>Password</label><input name="password_0" type="password" required></div>
</div>
</div>
</div>
<button type="button" class="add" onclick="addNode()">+ Add another node</button>

<label style="margin-top:22px">Log interval (seconds)</label>
<select name="log_interval">
<option>5</option><option selected>10</option><option>30</option><option>60</option>
</select>

<div class="check-row">
<input type="checkbox" name="buzzer" id="buzzer" checked>
<label for="buzzer">Enable buzzer</label>
</div>

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

# ===================== DASHBOARD HTML =====================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{--bg:#0b1120;--card:#1e293b;--accent:#38bdf8;--green:#22c55e;--red:#ef4444;--orange:#f97316;--purple:#a855f7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:#f1f5f9;font-family:system-ui,sans-serif;padding:20px 16px 40px}
h1{text-align:center;color:var(--accent);margin:0 0 4px;font-size:1.9rem}
.meta{text-align:center;color:#94a3b8;margin-bottom:20px;font-size:.9rem}
.toolbar{display:flex;flex-wrap:wrap;gap:12px;justify-content:center;margin-bottom:22px;align-items:center}
.toolbar label{display:flex;align-items:center;gap:6px;font-size:.9rem;color:#cbd5e1;cursor:pointer}
.toolbar input{width:16px;height:16px}
.btn-refresh{background:var(--accent);color:#0f172a;border:none;padding:8px 16px;border-radius:8px;font-weight:600;cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px;max-width:1200px;margin:0 auto}
.grid.single{display:flex;justify-content:center}
.grid.single .card{width:100%;max-width:420px}
.card{background:var(--card);border-radius:16px;padding:20px;position:relative;transition:transform .2s}
.card:hover{transform:translateY(-2px)}
.card.offline{opacity:.75;border:1px solid var(--red)}
.badge{position:absolute;top:16px;right:16px;padding:4px 11px;border-radius:999px;font-size:.72rem;font-weight:700;letter-spacing:.3px}
.online{background:rgba(34,197,94,.18);color:var(--green)}
.offlineb{background:rgba(239,68,68,.18);color:var(--red)}
h2{margin:0 0 14px;font-size:1.25rem}
.stat{display:flex;justify-content:space-between;margin:7px 0;font-size:.95rem}
.bar{height:7px;background:#334155;border-radius:4px;margin:3px 0 11px;overflow:hidden}
.bar>div{height:100%;border-radius:4px;transition:width .4s}
.actions{margin-top:16px;display:flex;gap:10px}
.actions button{flex:1;padding:9px;border:none;border-radius:9px;font-weight:600;cursor:pointer;font-size:.9rem}
.btn-reboot{background:var(--orange);color:#000}
.btn-shutdown{background:var(--red);color:#fff}
.actions button:disabled{opacity:.4;cursor:not-allowed}
.charts{max-width:1200px;margin:32px auto 0;display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}
.chart-card{background:var(--card);border-radius:16px;padding:16px}
.chart-card h3{margin:0 0 10px;font-size:1rem;color:#94a3b8}
canvas{max-height:180px}
.hidden{display:none !important}
</style></head><body>
<h1>PVE Node Monitor</h1>
<div class="meta" id="clock">Loading…</div>

<div class="toolbar">
  <button class="btn-refresh" onclick="load()">↻ Refresh</button>
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

<script>
const charts = {};
function makeChart(id, color, extra={}) {
  return new Chart(document.getElementById(id), {
    type: 'line',
    data: { labels: [], datasets: [{ data: [], borderColor: color, tension: 0.3, pointRadius: 0, borderWidth: 2, ...extra }] },
    options: { responsive: true, animation: false, plugins: { legend: { display: false } },
               scales: { x: { ticks: { maxTicksLimit: 6, color: '#64748b' } }, y: { ticks: { color: '#64748b' }, grid: { color: '#1e293b' } } } }
  });
}
charts.cpu = makeChart('chartCpu', '#38bdf8');
charts.ram = makeChart('chartRam', '#a855f7');
charts.net = new Chart(document.getElementById('chartNet'), {
  type: 'line',
  data: { labels: [], datasets: [
    { label: 'Down', data: [], borderColor: '#22c55e', tension: 0.3, pointRadius: 0, borderWidth: 2 },
    { label: 'Up', data: [], borderColor: '#f97316', tension: 0.3, pointRadius: 0, borderWidth: 2 }
  ]},
  options: { responsive: true, animation: false, plugins: { legend: { position: 'bottom', labels: { color: '#94a3b8' } } },
             scales: { x: { ticks: { maxTicksLimit: 6, color: '#64748b' } }, y: { ticks: { color: '#64748b' }, grid: { color: '#1e293b' } } } }
});
charts.disk = makeChart('chartDisk', '#eab308');

function toggleCharts() {
  document.getElementById('cCpu').classList.toggle('hidden', !gCpu.checked);
  document.getElementById('cRam').classList.toggle('hidden', !gRam.checked);
  document.getElementById('cNet').classList.toggle('hidden', !gNet.checked);
  document.getElementById('cDisk').classList.toggle('hidden', !gDisk.checked);
}

async function load() {
  const [nodes, logs] = await Promise.all([
    fetch('/api/current').then(r => r.json()),
    fetch('/api/logs/server?limit=40').then(r => r.json())
  ]);

  const grid = document.getElementById('nodes');
  grid.className = nodes.length === 1 ? 'grid single' : 'grid';
  grid.innerHTML = '';

  nodes.forEach(n => {
    const online = n.online == 1;
    const card = document.createElement('div');
    card.className = 'card' + (online ? '' : ' offline');
    const last = n.timestamp ? n.timestamp.split(' ')[1] : '—';
    card.innerHTML = `
      <span class="badge ${online ? 'online' : 'offlineb'}">${online ? 'ONLINE' : 'OFFLINE'}</span>
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

  // Charts (use latest node or aggregate – simple version uses all points)
  const times = logs.map(x => (x.timestamp||'').split(' ')[1] || '');
  charts.cpu.data.labels = times;
  charts.cpu.data.datasets[0].data = logs.map(x => x.cpu_usage);
  charts.cpu.update('none');
  charts.ram.data.labels = times;
  charts.ram.data.datasets[0].data = logs.map(x => x.ram_used_gb);
  charts.ram.update('none');
  charts.net.data.labels = times;
  charts.net.data.datasets[0].data = logs.map(x => x.net_in_kbps||0);
  charts.net.data.datasets[1].data = logs.map(x => x.net_out_kbps||0);
  charts.net.update('none');
  charts.disk.data.labels = times;
  charts.disk.data.datasets[0].data = logs.map(x => x.disk_pct||0);
  charts.disk.update('none');

  document.getElementById('clock').textContent = 'Updated ' + new Date().toLocaleTimeString();
}

async function power(name, action) {
  if (!confirm(`Really ${action} node "${name}"?`)) return;
  const r = await fetch(`/api/power/${name}/${action}`, {method:'POST'});
  const j = await r.json();
  alert(j.ok ? `${action} command sent` : 'Failed: ' + (j.error||'unknown'));
}

load();
setInterval(load, 5000);
</script></body></html>"""