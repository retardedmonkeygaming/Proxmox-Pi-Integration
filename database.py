import sqlite3
import time

DB_FILE = "monitor.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Enable WAL mode for smooth concurrent access
    cursor.execute("PRAGMA journal_mode=WAL;")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            cpu_usage REAL,
            ram_used_gb REAL,
            ram_total_gb REAL
        )
    """)
    
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

def log_server_metrics(cpu, ram_used, ram_total):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO server_logs (cpu_usage, ram_used_gb, ram_total_gb)
        VALUES (?, ?, ?)
    """, (cpu, ram_used, ram_total))
    conn.commit()
    conn.close()

def log_env_metrics(temp, humidity):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO environment_logs (temperature, humidity)
        VALUES (?, ?)
    """, (temp, humidity))
    conn.commit()
    conn.close()