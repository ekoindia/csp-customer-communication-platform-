"""Auto-update: staging, sha256 verify, apply-with-data-preservation, and the
admin publish/push flow. No network — uses a file:// URL for the package."""
import hashlib
import os
import zipfile

import pytest


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def _make_pkg(tmpdir, version, top_folder=False):
    """Build an update .zip carrying new code + a new VERSION, plus files that
    the applier MUST preserve (config.py, db, wa session) to prove they're skipped."""
    src = tmpdir.mkdir(f"pkg_{version}_{top_folder}")
    src.join("VERSION").write(version + "\n")
    src.join("newfeature.py").write(f"# feature added in {version}\nX = '{version}'\n")
    src.join("config.py").write("SHOULD_NOT_OVERWRITE = True\n")   # preserved
    src.mkdir("database").join("csp_platform.db").write("NEW-DB-SHOULD-BE-SKIPPED")
    wa = src.mkdir("whatsapp"); wa.mkdir(".wa_session").join("creds.json").write("NEW-SESSION-SKIP")
    wa.join("wa_server.js").write(f"// bridge {version}\n")       # normal code -> updated
    zpath = str(tmpdir.join(f"update_{version}_{top_folder}.zip"))
    base = str(src)
    with zipfile.ZipFile(zpath, "w") as z:
        for dp, _d, files in os.walk(base):
            for fn in files:
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, base).replace("\\", "/")
                arc = (f"csp_platform-{version}/" + rel) if top_folder else rel
                z.write(full, arc)
    return zpath


def _fake_app(tmpdir):
    """A throwaway 'installed app' tree the updater will patch."""
    app = tmpdir.mkdir("app")
    app.join("VERSION").write("1.0.0\n")
    app.join("config.py").write("CSP_NAME = 'Dudahi CSP'  # local, must survive\n")
    app.join("newfeature.py").ensure(file=False)  # absent before update
    app.mkdir("database").join("csp_platform.db").write("LOCAL-CUSTOMER-DATA")
    wa = app.mkdir("whatsapp"); wa.mkdir(".wa_session").join("creds.json").write("LOGGED-IN")
    wa.join("wa_server.js").write("// bridge 1.0.0\n")
    return app


def _point_updater_at(monkeypatch, app_dir):
    from core import updater
    root = str(app_dir)
    monkeypatch.setattr(updater, "APP_ROOT", root)
    monkeypatch.setattr(updater, "UPDATE_DIR", os.path.join(root, "update"))
    monkeypatch.setattr(updater, "STAGING", os.path.join(root, "update", "staged"))
    monkeypatch.setattr(updater, "PENDING", os.path.join(root, "update", "pending.json"))
    return updater


@pytest.mark.parametrize("top_folder", [False, True])
def test_stage_and_apply_preserves_data(tmpdir, monkeypatch, top_folder):
    app = _fake_app(tmpdir)
    updater = _point_updater_at(monkeypatch, app)
    pkg = _make_pkg(tmpdir, "1.1.0", top_folder=top_folder)
    url = "file:///" + pkg.replace("\\", "/")

    res = updater.stage_update("1.1.0", url, _sha256(pkg))
    assert res["ok"], res
    assert updater.pending_version() == "1.1.0"

    ap = updater.apply_pending()
    assert ap["ok"] and ap["applied"], ap

    # new code landed + version advanced
    assert os.path.isfile(os.path.join(str(app), "newfeature.py"))
    assert open(os.path.join(str(app), "VERSION")).read().strip() == "1.1.0"
    assert "bridge 1.1.0" in open(os.path.join(str(app), "whatsapp", "wa_server.js")).read()
    # PRESERVED: config.py, local DB, WhatsApp session untouched
    assert "Dudahi CSP" in open(os.path.join(str(app), "config.py")).read()
    assert open(os.path.join(str(app), "database", "csp_platform.db")).read() == "LOCAL-CUSTOMER-DATA"
    assert open(os.path.join(str(app), "whatsapp", ".wa_session", "creds.json")).read() == "LOGGED-IN"
    # pending cleared -> no re-apply loop
    assert updater.pending_version() is None
    assert updater.apply_pending()["applied"] is False


def test_sha256_mismatch_rejected(tmpdir, monkeypatch):
    app = _fake_app(tmpdir)
    updater = _point_updater_at(monkeypatch, app)
    pkg = _make_pkg(tmpdir, "1.2.0")
    url = "file:///" + pkg.replace("\\", "/")
    res = updater.stage_update("1.2.0", url, "deadbeef" * 8)
    assert not res["ok"] and "sha256" in res["error"]
    assert updater.pending_version() is None


def test_version_compare():
    from core.admin_reporter import _is_newer
    assert _is_newer("1.1.0", "1.0.0")
    assert _is_newer("1.0.10", "1.0.9")
    assert not _is_newer("1.0.0", "1.0.0")
    assert not _is_newer("1.0.0", "1.1.0")
    assert not _is_newer("", "1.0.0")


def test_admin_publish_and_sync_flow(tmpdir, monkeypatch):
    # isolate the admin DB to a temp file
    dbp = str(tmpdir.join("admin_test.db"))
    from admin_dashboard import db as adb
    monkeypatch.setattr(adb, "DB_PATH", dbp)
    adb.setup()
    from admin_dashboard.app import app
    app.config.update(TESTING=True)
    monkeypatch.setattr("admin_dashboard.api.get_connection", adb.get_connection)
    monkeypatch.setattr("admin_dashboard.routes.get_connection", adb.get_connection)

    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_in"] = True; s["admin_login"] = "admin"

    # no demo key ships by default (admin_dashboard/db.py::setup()) — issue one
    # for CSP001 via the real "API Keys" page, same as a real deployment would
    import re
    ik = c.post("/api-keys", data={"action": "issue", "csp_id": "CSP001"})
    api_key = re.search(r'id="newKeyValue"[^>]*>([^<]+)<', ik.get_data(as_text=True)).group(1).strip()

    # publish a version
    r = c.post("/updates", data={"action": "publish", "version": "1.1.0",
               "update_url": "http://x/pkg.zip", "update_sha256": "abc123"},
               follow_redirects=True)
    assert r.status_code == 200

    # a CSP sync now sees the published version + package
    s = c.get("/api/v1/sync?csp_id=CSP001", headers={"X-API-Key": api_key})
    j = s.get_json()
    assert j["latest_version"] == "1.1.0"
    assert j["update_url"] == "http://x/pkg.zip"
    assert j["update_sha256"] == "abc123"

    # the CSP checks in (so it's a known member of the fleet)
    c.post("/api/v1/report", json={"csp_id": "CSP001", "name": "Dudahi CSP"},
           headers={"X-API-Key": api_key})

    # push "update now" to all -> next sync delivers the command once
    c.post("/updates", data={"action": "push", "target": "__all__"}, follow_redirects=True)
    s2 = c.get("/api/v1/sync?csp_id=CSP001", headers={"X-API-Key": api_key}).get_json()
    assert any(cmd["command"] == "update_software" for cmd in s2["commands"])
    s3 = c.get("/api/v1/sync?csp_id=CSP001", headers={"X-API-Key": api_key}).get_json()
    assert s3["commands"] == []  # deliver-once
