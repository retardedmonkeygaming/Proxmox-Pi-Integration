import sqlite3

DB_FILE = "monitor.db"

def init_db() -> None:
    conn = sqlite3.connect(DB_FILE, timeout=15)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA busy_timeout=8000;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS server_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            node_name TEXT,
            cpu_usage REAL,
            ram_used_gb REAL,
            ram_total_gb REAL,
            disk_pct REAL,
            net_in_kbps REAL,
            net_out_kbps REAL,
            active_vms INTEGER,
            online INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS environment_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            humidity REAL
        )
    """)
    for col, typ in [
        ("node_name", "TEXT"), ("disk_pct", "REAL"),
        ("net_in_kbps", "REAL"), ("net_out_kbps", "REAL"),
        ("active_vms", "INTEGER"), ("online", "INTEGER"),
    ]:
        try:
            cur.execute(f"ALTER TABLE server_logs ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

def log_server_metrics(node_name, cpu, ram_u, ram_t, disk, net_in, net_out, vms, online=1):
    conn = sqlite3.connect(DB_FILE, timeout=15)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO server_logs
        (node_name, cpu_usage, ram_used_gb, ram_total_gb, disk_pct,
         net_in_kbps, net_out_kbps, active_vms, online)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (node_name, cpu, ram_u, ram_t, disk, net_in, net_out, vms, online))
    conn.commit()
    conn.close()

def log_humidity(humidity: float):
    conn = sqlite3.connect(DB_FILE, timeout=15)
    cur = conn.cursor()
    cur.execute("INSERT INTO environment_logs (humidity) VALUES (?)", (humidity,))
    conn.commit()
    conn.close()