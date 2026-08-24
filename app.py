import sqlite3
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

app = FastAPI(title="T5500 Server Monitor Dashboard")
DB_FILE = "monitor.db"

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --- API Endpoints ---

@app.get("/api/logs/server")
def get_server_logs(limit: int = Query(50, ge=1, le=500)):
    """Fetch recent server CPU and RAM metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, cpu_usage, ram_used_gb, ram_total_gb 
        FROM server_logs 
        ORDER BY id DESC 
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]

@app.get("/api/logs/environment")
def get_env_logs(limit: int = Query(50, ge=1, le=500)):
    """Fetch recent desk temperature and humidity metrics."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, temperature, humidity 
        FROM environment_logs 
        ORDER BY id DESC 
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]

# --- Web Dashboard Interface ---

@app.get("/", response_class=HTMLResponse)
def render_dashboard():
    """Serves a dynamic HTML/Chart.js single-page dashboard."""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>T5500 & Desk Monitor</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }
            h1 { text-align: center; color: #38bdf8; margin-bottom: 25px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; max-width: 1200px; margin: 0 auto; }
            .card { background: #1e293b; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3); }
            h2 { font-size: 1.1rem; color: #94a3b8; margin-top: 0; }
            canvas { max-height: 280px; }
        </style>
    </head>
    <body>
        <h1>T5500 Server & Desk Dashboard</h1>
        <div class="grid">
            <div class="card">
                <h2>Proxmox CPU Usage (%)</h2>
                <canvas id="cpuChart"></canvas>
            </div>
            <div class="card">
                <h2>Proxmox Memory Usage (GB)</h2>
                <canvas id="ramChart"></canvas>
            </div>
            <div class="card">
                <h2>Desk Environment (Temp & Humidity)</h2>
                <canvas id="envChart"></canvas>
            </div>
        </div>

        <script>
            async function fetchLogs() {
                const [serverRes, envRes] = await Promise.all([
                    fetch('/api/logs/server?limit=30'),
                    fetch('/api/logs/environment?limit=30')
                ]);
                const serverData = await serverRes.json();
                const envData = await envRes.json();
                return { serverData, envData };
            }

            function createChart(ctx, label, color, yMax = null) {
                return new Chart(ctx, {
                    type: 'line',
                    data: { labels: [], datasets: [{ label, data: [], borderColor: color, tension: 0.3, fill: false }] },
                    options: {
                        responsive: true,
                        scales: {
                            x: { ticks: { color: '#64748b' } },
                            y: { ticks: { color: '#64748b' }, max: yMax }
                        },
                        plugins: { legend: { labels: { color: '#cbd5e1' } } }
                    }
                });
            }

            async function initDashboard() {
                const cpuCtx = document.getElementById('cpuChart').getContext('2d');
                const ramCtx = document.getElementById('ramChart').getContext('2d');
                const envCtx = document.getElementById('envChart').getContext('2d');

                const cpuChart = createChart(cpuCtx, 'CPU %', '#38bdf8', 100);
                const ramChart = createChart(ramCtx, 'RAM Used (GB)', '#a855f7');
                
                const envChart = new Chart(envCtx, {
                    type: 'line',
                    data: {
                        labels: [],
                        datasets: [
                            { label: 'Temp (°C)', data: [], borderColor: '#ef4444', tension: 0.3 },
                            { label: 'Humidity (%)', data: [], borderColor: '#3b82f6', tension: 0.3 }
                        ]
                    },
                    options: {
                        responsive: true,
                        scales: { x: { ticks: { color: '#64748b' } }, y: { ticks: { color: '#64748b' } } },
                        plugins: { legend: { labels: { color: '#cbd5e1' } } }
                    }
                });

                async function update() {
                    const { serverData, envData } = await fetchLogs();
                    
                    const timestamps = serverData.map(d => d.timestamp.split(' ')[1]);
                    
                    cpuChart.data.labels = timestamps;
                    cpuChart.data.datasets[0].data = serverData.map(d => d.cpu_usage);
                    cpuChart.update();

                    ramChart.data.labels = timestamps;
                    ramChart.data.datasets[0].data = serverData.map(d => d.ram_used_gb);
                    ramChart.update();

                    const envTimestamps = envData.map(d => d.timestamp.split(' ')[1]);
                    envChart.data.labels = envTimestamps;
                    envChart.data.datasets[0].data = envData.map(d => d.temperature);
                    envChart.data.datasets[1].data = envData.map(d => d.humidity);
                    envChart.update();
                }

                await update();
                setInterval(update, 5000); // Auto-refresh charts every 5s
            }

            initDashboard();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)