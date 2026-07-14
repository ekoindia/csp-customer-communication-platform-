"""
Seed reference data on first run.
Idempotent — safe to call every startup.
"""

from datetime import datetime, timezone
from database.db import get_connection
from core.auth import hash_password as _hash_password
import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seed_all():
    _seed_campaign()
    _seed_branch()
    _seed_user()


def _seed_campaign():
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM campaigns WHERE id='inoperative_accounts'"
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO campaigns (id, name, is_active, created_at) VALUES (?,?,1,?)",
                ("inoperative_accounts", "Inoperative Accounts", _now()),
            )
            conn.commit()


def _seed_branch():
    # On a real install the branch is set by the CSP during first-run onboarding,
    # not seeded from config placeholders. Only dev/test wants the seeded branch.
    if not config.SEED_DEFAULT_USER:
        return
    with get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM branches LIMIT 1").fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO branches (csp_name, csp_phone, csp_address) VALUES (?,?,?)",
                (config.CSP_NAME, config.CSP_PHONE, config.CSP_ADDRESS),
            )
            conn.commit()


def _seed_user():
    # Real installs seed NO default login: the CSP sets their own login ID +
    # password on the onboarding screen (before login). Only dev/test seeds the
    # CSP001/changeme operator and marks onboarding complete so the suite can log
    # in directly. See config.SEED_DEFAULT_USER and dashboard onboarding gate.
    if not config.SEED_DEFAULT_USER:
        return
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE login_id=?", (config.LOGIN_ID,)
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO users (login_id, password, role, created_at)
                   VALUES (?, ?, 'csp_operator', ?)""",
                (config.LOGIN_ID, _hash_password(config.LOGIN_PASSWORD), _now()),
            )
            conn.commit()
    from database.queries import set_config_value
    set_config_value("onboarding_complete", "1")
