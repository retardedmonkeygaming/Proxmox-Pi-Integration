import sqlite3
from typing import Any, Dict, List

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI(title="PVE Node Monitor")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB = "monitor.db"

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

@app.get("/api/logs/server")
def server_logs(limit: int = Query(40, ge=1, le=500)):
    conn = db()
    rows = conn.execute(
        """SELECT timestamp, cpu_usage, ram_used_gb, ram_total_gb,
                  disk_pct, net_in_kbps, net_out_kbps, active_vms
           FROM server_logs ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]

@app.get("/api/logs/environment")
def env_logs(limit: int = Query(40, ge=1, le=500)):
    conn = db()
    rows = conn.execute(
        "SELECT timestamp, temperature, humidity FROM environment_logs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]

@app.get("/api/current")
def current() -> Dict[str, Any]:
    conn = db()
    s = conn.execute(
        """SELECT * FROM server_logs ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    e = conn.execute(
        "SELECT temperature, humidity FROM environment_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return {
        "server": dict(s) if s else {},
        "environment": dict(e) if e else {},
    }

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PVE Node Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
body{margin:0;padding:16px;background:#0f172a;color:#f1f5f9;font-family:system-ui,sans-serif}
h1{text-align:center;color:#38bdf8;margin:0 0 4px}
.meta{text-align:center;color:#94a3b8;font-size:.85rem;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;max-width:1200px;margin:0 auto}
.card{background:#1e293b;border-radius:12px;padding:14px}
h2{margin:0 0 10px;font-size:1rem}
canvas{max-height:200px}
</style>
</head>
<body>
<h1>PVE Node Monitor</h1>
<div class="meta" id="ts">Loading…</div>
<div class="grid">
  <div class="card"><h2>CPU %</h2><canvas id="cCpu"></canvas></div>
  <div class="card"><h2>RAM (GB)</h2><canvas id="cRam"></canvas></div>
  <div class="card"><h2>Network (KB/s)</h2><canvas id="cNet"></canvas></div>
  <div class="card"><h2>Environment</h2><canvas id="cEnv"></canvas></div>
</div>
<script>
const opts={responsive:true,animation:false,plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:5}}}};
const cpu=new Chart(cCpu,{type:'line',data:{labels:[],datasets:[{data:[],borderColor:'#38bdf8',tension:.3,pointRadius:0}]},options:opts});
const ram=new Chart(cRam,{type:'line',data:{labels:[],datasets:[{data:[],borderColor:'#a855f7',tension:.3,pointRadius:0}]},options:opts});
const net=new Chart(cNet,{type:'line',data:{labels:[],datasets:[
  {label:'Down',data:[],borderColor:'#22c55e',tension:.3,pointRadius:0},
  {label:'Up',data:[],borderColor:'#f97316',tension:.3,pointRadius:0}
]},options:{...opts,plugins:{legend:{position:'bottom'}}}});
const env=new Chart(cEnv,{type:'line',data:{labels:[],datasets:[
  {label:'Temp',data:[],borderColor:'#ef4444',tension:.3,pointRadius:0},
  {label:'Hum',data:[],borderColor:'#3b82f6',tension:.3,pointRadius:0}
]},options:{...opts,plugins:{legend:{position:'bottom'}}}});

async function tick(){
  try{
    const [s,e]=await Promise.all([
      fetch('/api/logs/server?limit=40').then(r=>r.json()),
      fetch('/api/logs/environment?limit=40').then(r=>r.json())
    ]);
    const t=s.map(x=>(x.timestamp||'').split(' ')[1]||'');
    cpu.data.labels=t; cpu.data.datasets[0].data=s.map(x=>x.cpu_usage); cpu.update('none');
    ram.data.labels=t; ram.data.datasets[0].data=s.map(x=>x.ram_used_gb); ram.update('none');
    net.data.labels=t;
    net.data.datasets[0].data=s.map(x=>x.net_in_kbps||0);
    net.data.datasets[1].data=s.map(x=>x.net_out_kbps||0); net.update('none');
    env.data.labels=e.map(x=>(x.timestamp||'').split(' ')[1]||'');
    env.data.datasets[0].data=e.map(x=>x.temperature);
    env.data.datasets[1].data=e.map(x=>x.humidity); env.update('none');
    ts.textContent='Updated '+new Date().toLocaleTimeString();
  }catch(err){console.error(err); ts.textContent='Fetch error';}
}
tick(); setInterval(tick,5000);
</script>
</body></html>"""