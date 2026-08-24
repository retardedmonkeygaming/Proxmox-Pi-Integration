import sqlite3
from typing import List, Dict, Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

app = FastAPI(title="PVE Node Monitor Dashboard")
DB_FILE = "monitor.db"

def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

@app.get("/api/logs/server")
def get_server_logs(limit: int = Query(50, ge=1, le=500)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, cpu_usage, ram_used_gb, ram_total_gb,
               disk_pct, net_in_kbps, net_out_kbps, active_vms
        FROM server_logs ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]

@app.get("/api/logs/environment")
def get_env_logs(limit: int = Query(50, ge=1, le=500)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, temperature, humidity
        FROM environment_logs ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]

@app.get("/api/current")
def get_current():
    """Latest single snapshot for quick status."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, cpu_usage, ram_used_gb, ram_total_gb,
               disk_pct, net_in_kbps, net_out_kbps, active_vms
        FROM server_logs ORDER BY id DESC LIMIT 1
    """)
    s = cursor.fetchone()
    cursor.execute("""
        SELECT temperature, humidity FROM environment_logs
        ORDER BY id DESC LIMIT 1
    """)
    e = cursor.fetchone()
    conn.close()
    return {
        "server": dict(s) if s else {},
        "environment": dict(e) if e else {},
    }

@app.get("/", response_class=HTMLResponse)
def render_dashboard():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>T5500 & Desk Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root { --bg:#0f172a; --card:#1e293b; --accent:#38bdf8; --text:#f8fafc; }
        body { font-family: system-ui,sans-serif; background:var(--bg); color:var(--text); margin:0; padding:16px; }
        h1 { text-align:center; color:var(--accent); margin-bottom:8px; }
        .meta { text-align:center; color:#94a3b8; font-size:0.85rem; margin-bottom:20px; }
        .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; max-width:1280px; margin:0 auto; }
        .card { background:var(--card); border-radius:12px; padding:16px; }
        canvas { max-height:220px; }
        h2 { margin:0 0 12px; font-size:1.1rem; }
    </style>
</head>
<body>
    <h1>T5500 Server & Desk Dashboard</h1>
    <div class="meta" id="lastUpdate">Loading…</div>
    <div class="grid">
        <div class="card"><h2>CPU Usage (%)</h2><canvas id="cpuChart"></canvas></div>
        <div class="card"><h2>RAM Usage (GB)</h2><canvas id="ramChart"></canvas></div>
        <div class="card"><h2>Network (KB/s)</h2><canvas id="netChart"></canvas></div>
        <div class="card"><h2>Desk Environment</h2><canvas id="envChart"></canvas></div>
    </div>
    <script>
        async function fetchLogs() {
            const [s, e] = await Promise.all([
                fetch('/api/logs/server?limit=40').then(r => r.json()),
                fetch('/api/logs/environment?limit=40').then(r => r.json())
            ]);
            return { s, e };
        }
        function makeChart(id, label, color, extra={}) {
            return new Chart(document.getElementById(id), {
                type: 'line',
                data: { labels: [], datasets: [{ label, data: [], borderColor: color, tension: 0.25, pointRadius: 0, ...extra }] },
                options: { responsive: true, plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: 6 } } } }
            });
        }
        async function init() {
            const cpuC = makeChart('cpuChart', 'CPU %', '#38bdf8');
            const ramC = makeChart('ramChart', 'RAM GB', '#a855f7');
            const netC = new Chart(document.getElementById('netChart'), {
                type: 'line',
                data: { labels: [], datasets: [
                    { label: 'Down', data: [], borderColor: '#22c55e', tension: 0.25, pointRadius: 0 },
                    { label: 'Up', data: [], borderColor: '#f97316', tension: 0.25, pointRadius: 0 }
                ]},
                options: { responsive: true, plugins: { legend: { position: 'bottom' } }, scales: { x: { ticks: { maxTicksLimit: 6 } } } }
            });
            const envC = new Chart(document.getElementById('envChart'), {
                type: 'line',
                data: { labels: [], datasets: [
                    { label: 'Temp °C', data: [], borderColor: '#ef4444', tension: 0.25, pointRadius: 0 },
                    { label: 'Humidity %', data: [], borderColor: '#3b82f6', tension: 0.25, pointRadius: 0 }
                ]},
                options: { responsive: true, plugins: { legend: { position: 'bottom' } }, scales: { x: { ticks: { maxTicksLimit: 6 } } } }
            });

            async function update() {
                try {
                    const { s, e } = await fetchLogs();
                    const times = s.map(d => (d.timestamp || '').split(' ')[1] || '');
                    cpuC.data.labels = times;
                    cpuC.data.datasets[0].data = s.map(d => d.cpu_usage);
                    cpuC.update('none');
                    ramC.data.labels = times;
                    ramC.data.datasets[0].data = s.map(d => d.ram_used_gb);
                    ramC.update('none');
                    netC.data.labels = times;
                    netC.data.datasets[0].data = s.map(d => d.net_in_kbps || 0);
                    netC.data.datasets[1].data = s.map(d => d.net_out_kbps || 0);
                    netC.update('none');
                    envC.data.labels = e.map(d => (d.timestamp || '').split(' ')[1] || '');
                    envC.data.datasets[0].data = e.map(d => d.temperature);
                    envC.data.datasets[1].data = e.map(d => d.humidity);
                    envC.update('none');
                    document.getElementById('lastUpdate').textContent =
                        'Last update: ' + new Date().toLocaleTimeString();
                } catch (err) {
                    console.error(err);
                }
            }
            await update();
            setInterval(update, 5000);
        }
        init();
    </script>
</body>
</html>
"""