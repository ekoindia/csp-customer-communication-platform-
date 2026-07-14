"""
Shared pytest fixtures.

Each test gets a fresh, isolated SQLite database in a temp dir, so tests
never touch the real database/csp_platform.db and never interfere with
each other. config.DB_PATH is read at call-time by db.get_connection(),
so pointing it at a temp file per-test fully isolates state.
"""

import os
import sys

import pytest

# Make the CSP app importable when running pytest from anywhere: tests/ lives
# under csp_dashboard/, so its parent is the CSP app root (config, core,
# database, dashboard). Also add the repo root (one level up) so the sibling
# `admin_dashboard` package resolves for the admin-portal tests.
_CSP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _CSP_ROOT)
sys.path.insert(0, os.path.dirname(_CSP_ROOT))

import config
from database.db import setup


@pytest.fixture()
def db(tmp_path):
    """Fresh seeded DB for one test. Seeds the default CSP001/changeme operator
    and marks onboarding complete (SEED_DEFAULT_USER) so tests log in directly,
    bypassing the first-run onboarding wizard that real installs go through."""
    original = config.DB_PATH
    original_seed = config.SEED_DEFAULT_USER
    config.DB_PATH = str(tmp_path / "test.db")
    config.SEED_DEFAULT_USER = True
    setup()
    yield
    config.DB_PATH = original
    config.SEED_DEFAULT_USER = original_seed


def make_dashboard_client():
    """A minimal Flask app around dashboard_bp — NOT the real app.py, which
    runs setup()/hardware-probe/admin_reporter.start_background() at MODULE
    IMPORT time against whatever config.DB_PATH stood at that moment (unsafe
    to trigger inside a test process — would target the real project DB
    instead of an isolated tmp one). Registers the same IST template filters
    app.py defines, since several templates (e.g. campaign.html's visit log)
    use them and would otherwise raise TemplateAssertionError."""
    from datetime import datetime, timedelta, timezone

    from flask import Flask

    from dashboard.routes import dashboard_bp

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__, template_folder=os.path.join(root, "dashboard", "templates"),
               static_folder=os.path.join(root, "dashboard", "static"))
    app.secret_key = "test-secret"
    app.register_blueprint(dashboard_bp)

    ist = timezone(timedelta(hours=5, minutes=30))

    def _to_ist(value):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ist)

    app.template_filter("ist_date")(lambda v: (_to_ist(v).strftime("%d %b %Y") if _to_ist(v) else "—"))
    app.template_filter("ist_time")(lambda v: (_to_ist(v).strftime("%I:%M %p") + " IST" if _to_ist(v) else ""))
    return app.test_client()


def dashboard_login(client):
    client.post("/login", data={"csp_id": config.LOGIN_ID, "password": config.LOGIN_PASSWORD})


@pytest.fixture()
def seeded_case(db):
    """Insert one non-sensitive case ready for downstream tests."""
    from database import queries
    queries.insert_document("B_TEST", "inoperative_accounts", "f.csv", "csv")
    queries.insert_customer_case(
        case_id="C_TEST", batch_id="B_TEST", campaign_id="inoperative_accounts",
        account_number="3577864748", name="RAMESH KUMAR", mobile="9876543210",
        father_name="RAJU KUMAR", balance_band="100<1000",
        village="Ahiraule", taluka="Tamkuhi Raj", address="VILL-AHIRAULI",
        band_label="100<1000", tone="normal", template_id="template_1",
        is_sensitive=False,
    )
    queries.init_business_tracking("C_TEST")
    return "C_TEST"
