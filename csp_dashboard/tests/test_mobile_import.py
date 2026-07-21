"""Tests for the encrypted mobile-scanner import (.cspx) path:
  - core/import_crypto.py round-trip + failure modes + on-wire format
  - core/settings.py passphrase get/set/clear + validation
  - the /upload route decrypting a .cspx at ingress (right vs wrong passphrase)
"""
import io
import os

import pytest

import config
from core import import_crypto
from tests.conftest import make_dashboard_client, dashboard_login


def _xlsx_bytes():
    """A minimal, valid .xlsx with the 4 MVP columns + one row."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["account_number", "name", "mobile", "balance_band"])
    ws.append(["3577864748", "RAMESH KUMAR", "9876543210", "100<1000"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── import_crypto unit tests ────────────────────────────────────────────────

def test_roundtrip():
    blob = import_crypto.encrypt_package(b"hello-xlsx", "pass1234")
    assert import_crypto.decrypt_package(blob, "pass1234") == b"hello-xlsx"


def test_wrong_passphrase_fails_cleanly():
    blob = import_crypto.encrypt_package(b"secret", "correct-pass")
    with pytest.raises(import_crypto.DecryptError):
        import_crypto.decrypt_package(blob, "wrong-pass")


def test_empty_passphrase_rejected():
    blob = import_crypto.encrypt_package(b"secret", "correct-pass")
    with pytest.raises(import_crypto.DecryptError):
        import_crypto.decrypt_package(blob, "")


def test_bad_header_rejected():
    with pytest.raises(import_crypto.DecryptError):
        import_crypto.decrypt_package(b"NOTA-CSPX-FILE-abcdefghijklmnop", "pass1234")


def test_truncated_rejected():
    blob = import_crypto.encrypt_package(b"secret", "correct-pass")
    with pytest.raises(import_crypto.DecryptError):
        import_crypto.decrypt_package(blob[:10], "correct-pass")


def test_tamper_is_detected():
    blob = bytearray(import_crypto.encrypt_package(b"important data here", "correct-pass"))
    blob[-1] ^= 0x01                      # flip a ciphertext/tag bit
    with pytest.raises(import_crypto.DecryptError):
        import_crypto.decrypt_package(bytes(blob), "correct-pass")


def test_on_wire_format_matches_spec():
    """Header layout must exactly match the APK spec (magic|ver|salt|nonce|body)."""
    blob = import_crypto.encrypt_package(b"x", "pass1234",
                                         salt=b"\x00" * 16, nonce=b"\x11" * 12)
    assert blob[:4] == b"CSPX"
    assert blob[4] == 1
    assert blob[5:21] == b"\x00" * 16
    assert blob[21:33] == b"\x11" * 12
    assert len(blob) >= 4 + 1 + 16 + 12 + 16    # + at least the GCM tag


# ── settings passphrase storage ─────────────────────────────────────────────

def test_settings_passphrase_set_get_clear(db):
    from core import settings
    assert settings.get_import_passphrase() == ""
    assert settings.import_passphrase_is_set() is False

    assert settings.set_import_passphrase("12345")["ok"] is False   # too short
    assert settings.get_import_passphrase() == ""

    assert settings.set_import_passphrase("good-pass")["ok"] is True
    assert settings.get_import_passphrase() == "good-pass"
    assert settings.import_passphrase_is_set() is True

    assert settings.set_import_passphrase("")["ok"] is True          # explicit clear
    assert settings.import_passphrase_is_set() is False


# ── /upload route integration ───────────────────────────────────────────────

def test_upload_cspx_wrong_passphrase_rejected(db, tmp_path, monkeypatch):
    from core import settings
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    settings.set_import_passphrase("right-pass")
    blob = import_crypto.encrypt_package(_xlsx_bytes(), "WRONG-pass")

    client = make_dashboard_client()
    dashboard_login(client)
    resp = client.post("/upload", data={
        "campaign_id": "inoperative_accounts",
        "documents": (io.BytesIO(blob), "scan.cspx"),
    }, content_type="multipart/form-data")

    assert resp.status_code in (302, 303)
    # rejected -> back to documents, NOT the extracting/progress screen
    assert "extracting" not in resp.headers["Location"]
    # nothing left behind (the batch was cleaned up on the decrypt failure)
    left = os.listdir(str(tmp_path / "uploads")) if os.path.isdir(str(tmp_path / "uploads")) else []
    assert left == []


def test_upload_cspx_correct_passphrase_decrypts(db, tmp_path, monkeypatch):
    from core import settings
    import core.jobs as jobs
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(jobs, "start", lambda *a, **k: "JOB123")   # no bg thread
    settings.set_import_passphrase("right-pass")
    blob = import_crypto.encrypt_package(_xlsx_bytes(), "right-pass")

    client = make_dashboard_client()
    dashboard_login(client)
    resp = client.post("/upload", data={
        "campaign_id": "inoperative_accounts",
        "documents": (io.BytesIO(blob), "scan.cspx"),
    }, content_type="multipart/form-data")

    assert resp.status_code in (302, 303)
    assert "extracting" in resp.headers["Location"]     # proceeded = decrypt worked
    files = os.listdir(str(tmp_path / "uploads"))
    assert any(f.endswith(".xlsx") for f in files)      # decrypted Excel written
    assert not any(f.endswith(".cspx") for f in files)  # encrypted original removed
