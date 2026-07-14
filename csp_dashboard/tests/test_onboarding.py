"""First-run onboarding: the CSP sets their OWN login ID + password + branch,
and the chosen login is saved to CSP_Login.txt on the Desktop."""
import pytest

import config
from database.db import setup
from tests.conftest import make_dashboard_client


@pytest.fixture()
def fresh_db(tmp_path):
    """A NOT-yet-onboarded install (no default operator, onboarding gate active)."""
    orig_db, orig_seed = config.DB_PATH, config.SEED_DEFAULT_USER
    config.DB_PATH = str(tmp_path / "fresh.db")
    config.SEED_DEFAULT_USER = False
    setup()
    yield
    config.DB_PATH, config.SEED_DEFAULT_USER = orig_db, orig_seed


def test_write_login_file(tmp_path, monkeypatch):
    from core import credentials_file
    monkeypatch.setattr(credentials_file, "_desktop_dir", lambda: str(tmp_path))
    path = credentials_file.write_login_file("1AB50895", "secret9")
    assert path and path.endswith("CSP_Login.txt")
    content = open(path, encoding="utf-8").read()
    assert "1AB50895" in content and "secret9" in content


def test_gate_redirects_before_onboarding(fresh_db):
    c = make_dashboard_client()
    for path in ("/login", "/welcome"):
        r = c.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/onboarding")


def test_onboarding_creates_credentials_branch_and_file(fresh_db, tmp_path, monkeypatch):
    from core import credentials_file
    from database.queries import get_user, get_branch
    monkeypatch.setattr(credentials_file, "_desktop_dir", lambda: str(tmp_path))

    c = make_dashboard_client()
    r = c.post("/onboarding", data={
        "login_id": "1AB50895", "password": "secret9", "confirm_password": "secret9",
        "csp_name": "Dudahi CSP", "branch_code": "1332",
        "csp_address": "Dudahi, Tamkuhi Raj, Kushinagar", "csp_phone": "9800000000",
    })
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")

    # account + branch persisted
    assert get_user("1AB50895") is not None
    b = get_branch()
    assert b["csp_name"] == "Dudahi CSP" and b["branch_code"] == "1332"

    # CSP_Login.txt written with the chosen credentials
    written = open(str(tmp_path / "CSP_Login.txt"), encoding="utf-8").read()
    assert "1AB50895" in written and "secret9" in written

    # login with the chosen credentials works; wrong password rejected
    assert c.post("/login", data={"csp_id": "1AB50895", "password": "secret9"}).status_code == 302
    assert c.post("/login", data={"csp_id": "1AB50895", "password": "nope"}).status_code == 200


def test_password_mismatch_rejected(fresh_db, tmp_path, monkeypatch):
    from core import credentials_file
    from database.queries import get_user
    monkeypatch.setattr(credentials_file, "_desktop_dir", lambda: str(tmp_path))
    c = make_dashboard_client()
    r = c.post("/onboarding", data={
        "login_id": "X1", "password": "secret9", "confirm_password": "different",
        "csp_name": "A CSP", "branch_code": "1", "csp_address": "addr", "csp_phone": "9",
    })
    assert r.status_code == 200          # re-renders form, no redirect
    assert get_user("X1") is None        # nothing created
