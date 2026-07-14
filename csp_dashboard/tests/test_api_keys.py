"""Admin-portal API-key issue/rotate/revoke/reactivate UI. This is the piece
that lets 'hum denge uss CSP ko' actually work for a real (non-demo) CSP —
before this, only the single seeded demo-key-CSP001 existed."""
import re

import pytest


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    from admin_dashboard import db as adb
    monkeypatch.setattr(adb, "DB_PATH", str(tmp_path / "admin_test.db"))
    adb.setup()
    from admin_dashboard.app import app
    app.config.update(TESTING=True)
    monkeypatch.setattr("admin_dashboard.api.get_connection", adb.get_connection)
    monkeypatch.setattr("admin_dashboard.routes.get_connection", adb.get_connection)
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_in"] = True
        s["admin_login"] = "admin"
    return c


def _issued_key(html: str) -> str:
    m = re.search(r'id="newKeyValue"[^>]*>([^<]+)<', html)
    assert m, "issued key not found in response"
    return m.group(1).strip()


def test_issue_key_then_report_succeeds(admin_client):
    r = admin_client.post("/api-keys", data={"action": "issue", "csp_id": "CSP555",
                                             "name": "Test CSP"})
    key = _issued_key(r.get_data(as_text=True))

    rr = admin_client.post("/api/v1/report", json={"csp_id": "CSP555"},
                           headers={"X-API-Key": key})
    assert rr.status_code == 200


def test_rotate_invalidates_old_key(admin_client):
    r1 = admin_client.post("/api-keys", data={"action": "issue", "csp_id": "CSP555"})
    old_key = _issued_key(r1.get_data(as_text=True))

    r2 = admin_client.post("/api-keys", data={"action": "issue", "csp_id": "CSP555"})
    new_key = _issued_key(r2.get_data(as_text=True))
    assert new_key != old_key

    stale = admin_client.post("/api/v1/report", json={"csp_id": "CSP555"},
                              headers={"X-API-Key": old_key})
    assert stale.status_code == 401
    fresh = admin_client.post("/api/v1/report", json={"csp_id": "CSP555"},
                              headers={"X-API-Key": new_key})
    assert fresh.status_code == 200


def test_revoke_and_reactivate(admin_client):
    r = admin_client.post("/api-keys", data={"action": "issue", "csp_id": "CSP555"})
    key = _issued_key(r.get_data(as_text=True))

    admin_client.post("/api-keys", data={"action": "toggle", "csp_id": "CSP555"})
    revoked = admin_client.post("/api/v1/report", json={"csp_id": "CSP555"},
                                headers={"X-API-Key": key})
    assert revoked.status_code == 401

    admin_client.post("/api-keys", data={"action": "toggle", "csp_id": "CSP555"})
    reactivated = admin_client.post("/api/v1/report", json={"csp_id": "CSP555"},
                                    headers={"X-API-Key": key})
    assert reactivated.status_code == 200


def test_list_page_masks_key(admin_client):
    r = admin_client.post("/api-keys", data={"action": "issue", "csp_id": "CSP555"})
    key = _issued_key(r.get_data(as_text=True))

    listing = admin_client.get("/api-keys").get_data(as_text=True)
    assert key not in listing
    assert f"••••{key[-4:]}" in listing


def test_missing_csp_id_rejected(admin_client):
    r = admin_client.post("/api-keys", data={"action": "issue", "csp_id": ""},
                          follow_redirects=True)
    assert r.status_code == 200
    listing = admin_client.get("/api-keys").get_data(as_text=True)
    assert "No keys issued yet." in listing


def test_no_demo_key_seeded_by_default(admin_client):
    """A real, working credential must never ship by default — see
    admin_dashboard/db.py::setup()."""
    listing = admin_client.get("/api-keys").get_data(as_text=True)
    assert "No keys issued yet." in listing
    r = admin_client.post("/api/v1/report", json={"csp_id": "CSP001"},
                          headers={"X-API-Key": "demo-key-CSP001"})
    assert r.status_code == 401
