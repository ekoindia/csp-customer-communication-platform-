"""The campaign dashboard shows a banner when an update has been staged
(downloaded + verified) but not yet applied — applying only happens at the
next full restart, so the CSP needs to be told to restart."""
import json
import os

from tests.conftest import make_dashboard_client, dashboard_login


def _stage(tmp_path, monkeypatch, version=None):
    from core import updater
    monkeypatch.setattr(updater, "APP_ROOT", str(tmp_path))
    monkeypatch.setattr(updater, "UPDATE_DIR", str(tmp_path / "update"))
    monkeypatch.setattr(updater, "STAGING", str(tmp_path / "update" / "staged"))
    monkeypatch.setattr(updater, "PENDING", str(tmp_path / "update" / "pending.json"))
    if version:
        os.makedirs(str(tmp_path / "update"), exist_ok=True)
        with open(str(tmp_path / "update" / "pending.json"), "w") as f:
            json.dump({"version": version, "sha256": "abc"}, f)


def test_no_banner_when_nothing_staged(db, tmp_path, monkeypatch):
    _stage(tmp_path, monkeypatch)
    client = make_dashboard_client()
    dashboard_login(client)
    client.post("/admin-connect", data={"action": "skip"})
    r = client.get("/campaign/inoperative_accounts")
    assert r.status_code == 200
    assert "Update ready" not in r.get_data(as_text=True)


def test_banner_shown_when_update_staged(db, tmp_path, monkeypatch):
    _stage(tmp_path, monkeypatch, version="1.2.0")
    client = make_dashboard_client()
    dashboard_login(client)
    client.post("/admin-connect", data={"action": "skip"})
    r = client.get("/campaign/inoperative_accounts")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Update ready" in html
    assert "v1.2.0" in html
