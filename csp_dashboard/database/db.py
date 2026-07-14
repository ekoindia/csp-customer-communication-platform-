import sqlite3
import os
import config

_schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    with open(_schema_path, encoding="utf-8") as f:
        schema = f.read()
    conn = get_connection()
    conn.executescript(schema)
    conn.commit()
    _run_migrations(conn)
    conn.close()


def _run_migrations(conn):
    """Lightweight forward migrations for databases created before a column existed."""
    # branch_code — added when onboarding started collecting the SBI branch code
    # alongside CSP name/phone/address. Older DBs get the column forward.
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(branches)")}
    if "branch_code" not in cols:
        conn.execute("ALTER TABLE branches ADD COLUMN branch_code TEXT")
        conn.commit()

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(communication_attempts)")}
    if "provider_message_id" not in cols:
        conn.execute("ALTER TABLE communication_attempts ADD COLUMN provider_message_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comm_provider_msg "
            "ON communication_attempts(provider_message_id)"
        )
        conn.commit()

    # PII-at-rest columns (see core/crypto.py). Existing rows created before
    # this migration still hold PLAIN TEXT in name/mobile/account_number/
    # father_name/address — they are not retroactively encrypted here (a
    # fresh install always starts from an empty DB per the "N stays N"
    # design); this only adds the columns forward so new writes/reads work.
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(customer_cases)")}
    if "account_number_hash" not in cols:
        conn.execute("ALTER TABLE customer_cases ADD COLUMN account_number_hash TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cases_account_hash "
            "ON customer_cases(account_number_hash)"
        )
        conn.commit()
    if "pii_purged_at" not in cols:
        conn.execute("ALTER TABLE customer_cases ADD COLUMN pii_purged_at TEXT")
        conn.commit()


def setup():
    """Create schema then seed reference data. Call once at app startup."""
    init_db()
    from database.seed import seed_all
    seed_all()
