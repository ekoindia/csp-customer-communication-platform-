"""Admin portal DB (separate from the CSP DB). SQLite, local to Eko's server."""
import os
import sqlite3
from datetime import datetime, timezone

_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DIR, "admin.db")
SCHEMA = os.path.join(_DIR, "schema.sql")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def setup():
    with get_connection() as conn:
        with open(SCHEMA, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        # Forward-migration: admin.db created before the dxdiag column existed.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(csps)")}
        if "dxdiag" not in cols:
            conn.execute("ALTER TABLE csps ADD COLUMN dxdiag TEXT")
        # Seed a default admin login so the portal is usable out of the box
        # (change this password for a real deployment). Deliberately NO demo
        # API key is seeded — a real, working credential should never ship by
        # default; issue per-CSP keys from the "API Keys" page instead.
        cur = conn.execute("SELECT COUNT(*) c FROM admin_users").fetchone()
        if cur["c"] == 0:
            from core.auth import hash_password
            conn.execute(
                "INSERT INTO admin_users (login_id, password, role, created_at) VALUES (?,?,?,?)",
                ("admin", hash_password("admin123"), "admin", _now()),
            )
        # Latest published app version (CSPs learn about updates via /sync).
        conn.execute(
            "INSERT OR IGNORE INTO server_config (key, value) VALUES ('latest_version','1.0.0')")
        conn.commit()
