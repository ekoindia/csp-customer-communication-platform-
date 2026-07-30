"""Admin-portal publish flow + the command queue that carries it to a CSP.

This is the path that replaces a site visit: publish here, and every install
fetches, verifies and applies it on its own. So what matters is that the
published package is PINNED (its bytes match the advertised hash), that /sync
advertises exactly that, and that a queued command survives a CSP that is busy.
"""
import hashlib
import re
import zipfile

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
    # Releases are written to a temp dir, not into the repo.
    monkeypatch.setattr("admin_dashboard.routes.RELEASES_DIR", str(tmp_path / "rel"))
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_in"] = True
        s["admin_login"] = "admin"
    return c


def _issue_key(client, csp_id="CSP900"):
    r = client.post("/api-keys", data={"action": "issue", "csp_id": csp_id,
                                       "name": "Test CSP"})
    m = re.search(r'id="newKeyValue"[^>]*>([^<]+)<', r.get_data(as_text=True))
    assert m, "issued key not found"
    return m.group(1).strip()


def test_publish_advertises_a_pinned_package_whose_hash_matches(admin_client):
    """A CSP refuses a package whose sha256 doesn't match what /sync advertised,
    so publish must pin the exact bytes it hashed — not a URL that rebuilds."""
    key = _issue_key(admin_client)
    r = admin_client.post("/releases", data={"action": "publish_current",
                                             "version": "9.9.9"},
                          follow_redirects=True)
    assert r.status_code == 200

    s = admin_client.get("/api/v1/sync?csp_id=CSP900",
                         headers={"X-API-Key": key}).get_json()
    assert s["latest_version"] == "9.9.9"
    assert s["update_url"] and s["update_sha256"]

    # follow the advertised URL and check the bytes really hash to that value
    path = s["update_url"].split("/", 3)[-1]
    pkg = admin_client.get("/" + path)
    assert pkg.status_code == 200
    assert hashlib.sha256(pkg.data).hexdigest() == s["update_sha256"]


def test_published_package_carries_the_published_version(admin_client, tmp_path):
    """config.APP_VERSION reads the VERSION file, and the CSP reports it back. If
    the package didn't carry the published number, every install would keep
    re-staging the same 'newer' version forever."""
    key = _issue_key(admin_client)
    admin_client.post("/releases", data={"action": "publish_current",
                                         "version": "7.1.2"}, follow_redirects=True)
    s = admin_client.get("/api/v1/sync?csp_id=CSP900",
                         headers={"X-API-Key": key}).get_json()
    pkg = admin_client.get("/" + s["update_url"].split("/", 3)[-1])
    out = tmp_path / "pkg.zip"
    out.write_bytes(pkg.data)
    with zipfile.ZipFile(out) as z:
        assert z.read("csp_dashboard/VERSION").decode().strip() == "7.1.2"
        names = z.namelist()
    # and it is a real app package, still without the server-only OCR weights
    assert "csp_dashboard/app.py" in names
    assert not [n for n in names if "/core/models/" in n]


def test_publish_rejects_a_non_numeric_version(admin_client):
    """Versions are compared number-by-number; 'latest' would never be newer."""
    key = _issue_key(admin_client)
    admin_client.post("/releases", data={"action": "publish_current",
                                         "version": "latest"}, follow_redirects=True)
    s = admin_client.get("/api/v1/sync?csp_id=CSP900",
                         headers={"X-API-Key": key}).get_json()
    assert s["latest_version"] != "latest"


def test_publish_can_tell_every_active_csp_to_update_now(admin_client):
    k1 = _issue_key(admin_client, "CSP901")
    _issue_key(admin_client, "CSP902")
    admin_client.post("/releases", data={"action": "publish_current",
                                         "version": "2.0.0", "push_now": "1"},
                      follow_redirects=True)
    s = admin_client.get("/api/v1/sync?csp_id=CSP901",
                         headers={"X-API-Key": k1}).get_json()
    assert [c["command"] for c in s["commands"]] == ["update_now"]
    # delivered once — a second poll must not run it again
    s2 = admin_client.get("/api/v1/sync?csp_id=CSP901",
                          headers={"X-API-Key": k1}).get_json()
    assert s2["commands"] == []


def test_command_for_one_csp_is_not_delivered_to_another(admin_client):
    k1 = _issue_key(admin_client, "CSP903")
    k2 = _issue_key(admin_client, "CSP904")
    admin_client.post("/releases", data={"action": "command", "command": "selfheal",
                                         "csp_id": "CSP903"}, follow_redirects=True)
    assert admin_client.get("/api/v1/sync?csp_id=CSP904",
                            headers={"X-API-Key": k2}).get_json()["commands"] == []
    assert admin_client.get("/api/v1/sync?csp_id=CSP903",
                            headers={"X-API-Key": k1}).get_json()["commands"]


def test_unknown_command_is_never_queued(admin_client):
    k = _issue_key(admin_client, "CSP905")
    admin_client.post("/releases", data={"action": "command",
                                         "command": "rm -rf /"}, follow_redirects=True)
    s = admin_client.get("/api/v1/sync?csp_id=CSP905",
                         headers={"X-API-Key": k}).get_json()
    assert s["commands"] == []


def test_ack_records_the_outcome(admin_client):
    k = _issue_key(admin_client, "CSP906")
    admin_client.post("/releases", data={"action": "command", "command": "selfheal",
                                         "csp_id": "CSP906"}, follow_redirects=True)
    cid = admin_client.get("/api/v1/sync?csp_id=CSP906",
                           headers={"X-API-Key": k}).get_json()["commands"][0]["id"]
    r = admin_client.post("/api/v1/command_ack",
                          json={"csp_id": "CSP906", "id": cid, "result": "ok",
                                "detail": "everything healthy"},
                          headers={"X-API-Key": k})
    assert r.get_json()["updated"] == 1
    page = admin_client.get("/releases").get_data(as_text=True)
    assert "everything healthy" in page


def test_a_deferred_command_is_requeued_for_the_next_poll(admin_client):
    """A CSP mid-send defers a restart. If the ack closed the command, that CSP
    would never receive the update — so 'deferred' puts it back in the queue."""
    k = _issue_key(admin_client, "CSP907")
    admin_client.post("/releases", data={"action": "command", "command": "restart_app",
                                         "csp_id": "CSP907"}, follow_redirects=True)
    cid = admin_client.get("/api/v1/sync?csp_id=CSP907",
                           headers={"X-API-Key": k}).get_json()["commands"][0]["id"]
    admin_client.post("/api/v1/command_ack",
                      json={"csp_id": "CSP907", "id": cid, "result": "deferred",
                            "detail": "a batch is sending"},
                      headers={"X-API-Key": k})
    again = admin_client.get("/api/v1/sync?csp_id=CSP907",
                             headers={"X-API-Key": k}).get_json()["commands"]
    assert [c["id"] for c in again] == [cid], "deferred command must come back"


def test_a_csp_cannot_ack_another_csps_command(admin_client):
    k1 = _issue_key(admin_client, "CSP908")
    k2 = _issue_key(admin_client, "CSP909")
    admin_client.post("/releases", data={"action": "command", "command": "selfheal",
                                         "csp_id": "CSP908"}, follow_redirects=True)
    cid = admin_client.get("/api/v1/sync?csp_id=CSP908",
                           headers={"X-API-Key": k1}).get_json()["commands"][0]["id"]
    r = admin_client.post("/api/v1/command_ack",
                          json={"csp_id": "CSP909", "id": cid, "result": "ok",
                                "detail": "not mine"},
                          headers={"X-API-Key": k2})
    assert r.get_json()["updated"] == 0


def test_sync_and_ack_require_a_valid_key(admin_client):
    _issue_key(admin_client, "CSP910")
    assert admin_client.get("/api/v1/sync?csp_id=CSP910",
                            headers={"X-API-Key": "wrong"}).status_code == 401
    assert admin_client.post("/api/v1/command_ack",
                             json={"csp_id": "CSP910", "id": 1, "result": "ok"},
                             headers={"X-API-Key": "wrong"}).status_code == 401


def test_suggested_version_is_always_an_increment(admin_client):
    """The page must never pre-fill a version a CSP would ignore as 'not newer'."""
    admin_client.post("/releases", data={"action": "publish_current",
                                         "version": "3.4.5"}, follow_redirects=True)
    page = admin_client.get("/releases").get_data(as_text=True)
    m = re.search(r'id="version"[^>]*value="([^"]+)"', page)
    assert m
    from admin_dashboard.routes import _vtuple
    assert _vtuple(m.group(1)) > _vtuple("3.4.5")


def test_publishing_from_localhost_warns_about_the_url(admin_client):
    """The package URL is built from the host the admin is browsing. If that is
    localhost, no CSP can fetch it — the portal must say so at publish time."""
    r = admin_client.post("/releases", data={"action": "publish_current",
                                             "version": "5.0.0"},
                          follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "cannot" in body and "reach" in body


def test_republishing_an_older_release_is_the_rollback(admin_client):
    key = _issue_key(admin_client, "CSP911")
    admin_client.post("/releases", data={"action": "publish_current",
                                         "version": "1.0.1"}, follow_redirects=True)
    admin_client.post("/releases", data={"action": "publish_current",
                                         "version": "1.0.2"}, follow_redirects=True)
    page = admin_client.get("/releases").get_data(as_text=True)
    # the older row offers a "Publish this" button; find its release id
    ids = re.findall(r'name="release_id" value="(\d+)"', page)
    assert ids, "no rollback target offered"
    admin_client.post("/releases", data={"action": "republish", "release_id": ids[0]},
                      follow_redirects=True)
    s = admin_client.get("/api/v1/sync?csp_id=CSP911",
                         headers={"X-API-Key": key}).get_json()
    assert s["latest_version"] == "1.0.1"
