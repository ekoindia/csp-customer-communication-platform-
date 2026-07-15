"""Eko Admin connection is provisioned by Eko (CSP_ID + API key baked into .env)
and controlled ONLY from the admin portal. The CSP cannot set it up, change it,
skip it, or disable it — so:
  - login always lands on /welcome (no admin-connect step), and
  - the old /admin-connect screen is retired: it just redirects back.

Minimal Flask harness around dashboard_bp (not the real app.py) so tests never
trigger app.py's module-level side effects against the real project database."""
import os

import config


def _make_client():
    from flask import Flask
    from dashboard.routes import dashboard_bp

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(__name__, template_folder=os.path.join(root, "dashboard", "templates"),
                static_folder=os.path.join(root, "dashboard", "static"))
    app.secret_key = "test-secret"
    app.register_blueprint(dashboard_bp)
    return app.test_client()


def _login(client):
    return client.post("/login", data={"csp_id": config.LOGIN_ID,
                                        "password": config.LOGIN_PASSWORD},
                       follow_redirects=False)


def test_login_always_goes_to_welcome(db):
    """No admin-connect step in the CSP flow — even with the shipped demo key."""
    config.ADMIN_API_KEY = "demo-key-CSP001"
    r = _login(_make_client())
    assert r.status_code == 302
    assert "/welcome" in r.headers["Location"]


def test_admin_connect_is_retired_redirects_to_welcome(db):
    """The old self-service screen no longer lets the CSP configure/skip/disable
    anything — it just sends them back to the dashboard."""
    client = _make_client()
    _login(client)
    for method in ("get", "post"):
        r = getattr(client, method)("/admin-connect", data={"action": "skip"})
        assert r.status_code == 302
        assert "/welcome" in r.headers["Location"]


def test_unauthenticated_cannot_reach_admin_connect(db):
    r = _make_client().get("/admin-connect")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
