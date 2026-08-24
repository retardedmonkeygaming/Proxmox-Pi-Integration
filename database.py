import sqlite3
from typing import Optional

DB_FILE = "monitor.db"

def init_db() -> None:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            cpu_usage REAL,
            ram_used_gb REAL,
            ram_total_gb REAL,
            disk_pct REAL,
            net_in_kbps REAL,
            net_out_kbps REAL,
            active_vms INTEGER
        )
    """)

    # Migration for older DBs
    for col, typ in [
        ("disk_pct", "REAL"),
        ("net_in_kbps", "REAL"),
        ("net_out_kbps", "REAL"),
        ("active_vms", "INTEGER"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE server_logs ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS environment_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            temperature REAL,
            humidity REAL
        )
    """)
    conn.commit()
    conn.close()

def log_server_metrics(
    cpu: float,
    ram_used: float,
    ram_total: float,
    disk_pct: float = 0.0,
    net_in: float = 0.0,
    net_out: float = 0.0,
    active_vms: int = 0,
) -> None:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO server_logs
        (cpu_usage, ram_used_gb, ram_total_gb, disk_pct, net_in_kbps, net_out_kbps, active_vms)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (cpu, ram_used, ram_total, disk_pct, net_in, net_out, active_vms))
    conn.commit()
    conn.close()

def log_env_metrics(temp: float, humidity: float) -> None:
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO environment_logs (temperature, humidity)
        VALUES (?, ?)
    """, (temp, humidity))
    conn.commit()
    conn.close()