"""
SQLite persistence: the samples time series and the recorded incidents.
"""

import json
import sqlite3


def init_db(path):
    """Open (creating if needed) the database and ensure the schema exists."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            ts TEXT, cpu_usage REAL, psi_cpu_some REAL,
            mem_available_pct REAL, swap_free_pct REAL, psi_mem_full REAL,
            psi_io_full REAL, disk_used_pct REAL,
            load1 REAL, in_incident INTEGER, mem_demand_gb REAL
        )""")
    scols = {r[1] for r in conn.execute("PRAGMA table_info(samples)")}
    if "mem_demand_gb" not in scols:
        conn.execute("ALTER TABLE samples ADD COLUMN mem_demand_gb REAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_ts TEXT, end_ts TEXT, duration_sec INTEGER,
            category TEXT, reasons TEXT, peak_json TEXT, top_procs_json TEXT,
            network_json TEXT, top_cpu_procs_json TEXT
        )""")
    # Migrate older databases that predate the top_cpu_procs_json column.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(incidents)")}
    if "top_cpu_procs_json" not in cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN top_cpu_procs_json TEXT")
    conn.commit()
    return conn


def insert_sample(conn, ts, sample, in_incident):
    """Append one resource sample."""
    conn.execute(
        "INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (ts, sample["cpu_usage_pct"], sample["psi_cpu_some_avg10"],
         sample["mem_available_pct"], sample["swap_free_pct"],
         sample["psi_mem_full_avg10"], sample["psi_io_full_avg10"],
         sample["disk_worst_used_pct"], sample["load1"], in_incident,
         sample.get("mem_demand_gb")))
    conn.commit()


def insert_incident(conn, inc):
    """Persist a closed incident. Returns the new incident id."""
    cur = conn.execute(
        """INSERT INTO incidents
           (start_ts, end_ts, duration_sec, category, reasons,
            peak_json, top_procs_json, network_json, top_cpu_procs_json)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (inc["start_ts"], inc["end_ts"], inc["duration_sec"], inc["category"],
         json.dumps(inc["reasons"], ensure_ascii=False),
         json.dumps(inc["peak"], ensure_ascii=False),
         json.dumps(inc["top_procs"], ensure_ascii=False),
         json.dumps(inc["net"], ensure_ascii=False),
         json.dumps(inc.get("top_cpu_procs", []), ensure_ascii=False)))
    conn.commit()
    return cur.lastrowid


def clear_all(path):
    """Wipe samples and incidents, resetting the incident id counter."""
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM incidents")
    conn.execute("DELETE FROM samples")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='incidents'")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
