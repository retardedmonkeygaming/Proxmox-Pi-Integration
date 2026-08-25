import sqlite3
from typing import Optional, List, Dict, Any

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
        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            node_name TEXT,
            alert_type TEXT,
            message TEXT,
            value REAL,
            threshold REAL,
            acknowledged INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            action TEXT,
            detail TEXT,
            source TEXT DEFAULT 'system'
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

def log_alert(node_name: str, alert_type: str, message: str, value: float = 0, threshold: float = 0):
    conn = sqlite3.connect(DB_FILE, timeout=15)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO alert_history (node_name, alert_type, message, value, threshold)
        VALUES (?,?,?,?,?)
    """, (node_name, alert_type, message, value, threshold))
    conn.commit()
    conn.close()

def ack_alert(alert_id: int = None, node_name: str = None):
    conn = sqlite3.connect(DB_FILE, timeout=15)
    cur = conn.cursor()
    if alert_id:
        cur.execute("UPDATE alert_history SET acknowledged=1 WHERE id=?", (alert_id,))
    elif node_name:
        cur.execute("UPDATE alert_history SET acknowledged=1 WHERE node_name=? AND acknowledged=0", (node_name,))
    else:
        cur.execute("UPDATE alert_history SET acknowledged=1 WHERE acknowledged=0")
    conn.commit()
    conn.close()

def get_alerts(limit: int = 50, unacked_only: bool = False) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM alert_history"
    if unacked_only:
        q += " WHERE acknowledged=0"
    q += " ORDER BY id DESC LIMIT ?"
    rows = conn.execute(q, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def log_activity(action: str, detail: str = "", source: str = "system"):
    conn = sqlite3.connect(DB_FILE, timeout=15)
    cur = conn.cursor()
    cur.execute("INSERT INTO activity_log (action, detail, source) VALUES (?,?,?)",
                (action, detail, source))
    conn.commit()
    conn.close()

def get_activity(limit: int = 40) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_logs_range(node: str = None, minutes: int = 60, limit: int = 200) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    if node:
        rows = conn.execute("""
            SELECT * FROM server_logs
            WHERE node_name=? AND timestamp >= datetime('now', ?)
            ORDER BY id ASC LIMIT ?
        """, (node, f"-{minutes} minutes", limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM server_logs
            WHERE timestamp >= datetime('now', ?)
            ORDER BY id ASC LIMIT ?
        """, (f"-{minutes} minutes", limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
