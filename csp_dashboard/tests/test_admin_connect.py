"""First-run 'Connect to Eko Admin Portal' screen: shown once after the first
successful login, Save writes .env + goes live immediately, Skip is equally
valid and never asks again either way.

Builds a minimal Flask harness around dashboard_bp directly (NOT the real
app.py) so tests never trigger app.py's module-level setup()/hardware-probe/
admin_reporter side effects against the real project database."""
import os

import pytest

import config
from database import queries


@pytest.fixture(autouse=True)
def _restore_admin_config():
    """The admin_connect() route mutates config.ADMIN_* live (by design — see
    dashboard/routes.py). config is a real module, not reloaded per test, so
    without this restore a test that saves real values would leak into every
    test that runs after it in the same process (order-dependent flakiness)."""
    orig = (config.ADMIN_CSP_ID, config.ADMIN_API_KEY, config.ADMIN_API_BASE,
            config.ADMIN_REPORT_ENABLED)
    yield
    (config.ADMIN_CSP_ID, config.ADMIN_API_KEY, config.ADMIN_API_BASE,
     config.ADMIN_REPORT_ENABLED) = orig


def _make_client(tmp_path, monkeypatch):
    from flask import Flask
    from dashboard.routes import dashboard_bp

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__, template_folder=os.path.join(root, "dashboard", "templates"),
               static_folder=os.path.join(root, "dashboard", "static"))
    app.secret_key = "test-secret"
    app.register_blueprint(dashboard_bp)

    # point env_writer at an isolated .env for this test
    env_path = str(tmp_path / ".env")
    monkeypatch.setattr("core.env_writer._ENV_PATH", env_path)
    # keep admin_reporter's actual network call from firing during tests
    monkeypatch.setattr("core.admin_reporter.report_once",
                        lambda: {"ok": True, "status": 200})
    monkeypatch.setattr("core.admin_reporter.start_background", lambda: None)

    return app.test_client(), env_path


def _login(client):
    return client.post("/login", data={"csp_id": config.LOGIN_ID,
                                        "password": config.LOGIN_PASSWORD},
                       follow_redirects=False)


def test_first_login_redirects_to_admin_connect(db, tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    r = _login(client)
    assert r.status_code == 302
    assert "/admin-connect" in r.headers["Location"]


def test_second_login_skips_admin_connect_once_resolved(db, tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    _login(client)
    client.post("/admin-connect", data={"action": "skip"})
    client.get("/logout")

    r = _login(client)
    assert r.status_code == 302
    assert "/welcome" in r.headers["Location"]


def test_skip_sets_flag_without_touching_config(db, tmp_path, monkeypatch):
    client, env_path = _make_client(tmp_path, monkeypatch)
    _login(client)
    r = client.post("/admin-connect", data={"action": "skip"}, follow_redirects=True)
    assert r.status_code == 200
    assert queries.get_config_value("admin_setup_prompted") == "1"
    assert not os.path.exists(env_path)  # never created — nothing to write


def test_save_writes_env_and_updates_live_config(db, tmp_path, monkeypatch):
    client, env_path = _make_client(tmp_path, monkeypatch)
    _login(client)

    r = client.post("/admin-connect", data={
        "action": "connect", "csp_id": "CSP777", "api_key": "real-key-abc",
        "api_base": "https://admin.eko.co.in/api/v1",
    }, follow_redirects=True)
    assert r.status_code == 200

    assert queries.get_config_value("admin_setup_prompted") == "1"
    # live in-process update — no restart needed
    assert config.ADMIN_CSP_ID == "CSP777"
    assert config.ADMIN_API_KEY == "real-key-abc"
    assert config.ADMIN_REPORT_ENABLED is True

    # persisted to .env for the NEXT process start too
    assert os.path.exists(env_path)
    content = open(env_path).read()
    assert "ADMIN_CSP_ID=CSP777" in content
    assert "ADMIN_API_KEY=real-key-abc" in content
    assert "ADMIN_REPORT_ENABLED=1" in content


def test_missing_fields_reprompt_without_saving(db, tmp_path, monkeypatch):
    client, env_path = _make_client(tmp_path, monkeypatch)
    _login(client)
    r = client.post("/admin-connect", data={"action": "connect", "csp_id": "",
                                            "api_key": ""})
    assert r.status_code == 200  # re-renders the form, no redirect
    assert queries.get_config_value("admin_setup_prompted") is None
    assert not os.path.exists(env_path)


def test_unauthenticated_cannot_reach_admin_connect(db, tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    r = client.get("/admin-connect")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_preconfigured_api_key_skips_web_screen(db, tmp_path, monkeypatch):
    """If INSTALL.bat's own 'Connect to Eko Admin Portal' prompt already wrote
    a real key to .env (loaded into config.ADMIN_API_KEY before the app ever
    starts), the web screen must not ask again — no DB flag needed at all."""
    config.ADMIN_API_KEY = "install-time-key-xyz"
    client, _ = _make_client(tmp_path, monkeypatch)
    r = _login(client)
    assert r.status_code == 302
    assert "/welcome" in r.headers["Location"]
    # never went through admin_connect(), so the DB flag stays unset
    assert queries.get_config_value("admin_setup_prompted") is None


def test_demo_key_still_triggers_the_prompt(db, tmp_path, monkeypatch):
    """The shipped demo key must NOT count as 'already configured' — only a
    genuinely different (real) key does."""
    config.ADMIN_API_KEY = "demo-key-CSP001"
    client, _ = _make_client(tmp_path, monkeypatch)
    r = _login(client)
    assert r.status_code == 302
    assert "/admin-connect" in r.headers["Location"]
