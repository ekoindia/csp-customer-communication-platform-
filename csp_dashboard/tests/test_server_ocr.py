import base64
import re

import pytest

from core.ocr_envelope import decrypt_json, encrypt_json


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


def _issue_key(client, csp_id="CSP777") -> str:
    r = client.post("/api-keys", data={"action": "issue", "csp_id": csp_id})
    return _issued_key(r.get_data(as_text=True))


def test_admin_ocr_endpoint_returns_encrypted_rows_and_metrics_only(admin_client, monkeypatch):
    key = _issue_key(admin_client)
    rows = [{
        "account_number": "123456789012",
        "name": "RAMESH KUMAR",
        "mobile": "9876543210",
        "balance_band": "100<1000",
    }]

    def fake_extract(file_bytes, file_type, page_from=None, page_to=None):
        assert file_bytes == b"fake-pdf-bytes-with-pii-name-ramesh"
        assert file_type == "pdf"
        assert page_from == 2
        assert page_to == 3
        return rows, 2

    monkeypatch.setattr("admin_dashboard.api._extract_ocr_rows", fake_extract)
    payload = encrypt_json({
        "request_id": "REQ123",
        "file_type": "pdf",
        "file_b64": base64.b64encode(b"fake-pdf-bytes-with-pii-name-ramesh").decode("ascii"),
        "page_from": 2,
        "page_to": 3,
    }, key)

    resp = admin_client.post("/api/v1/ocr/extract",
                             json={"csp_id": "CSP777", "payload": payload},
                             headers={"X-API-Key": key})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    out = decrypt_json(body["payload"], key)
    assert out["request_id"] == "REQ123"
    # Response carries an encrypted .xlsx, never rows in the clear.
    assert "rows" not in out
    from core.ocr_excel import xlsx_bytes_to_rows
    got = xlsx_bytes_to_rows(base64.b64decode(out["xlsx_b64"]))
    assert got == rows
    assert out["page_count"] == 2
    assert out["row_count"] == 1

    from admin_dashboard.db import get_connection
    with get_connection() as conn:
        metric = conn.execute("SELECT * FROM ocr_metrics").fetchone()
    assert metric["csp_id"] == "CSP777"
    assert metric["file_type"] == "pdf"
    assert metric["page_count"] == 2
    assert metric["row_count"] == 1
    assert metric["status"] == "ok"
    joined = " ".join(str(metric[k]) for k in metric.keys())
    assert "RAMESH" not in joined
    assert "9876543210" not in joined
    assert "123456789012" not in joined


def test_admin_ocr_endpoint_rejects_invalid_key_before_decrypt(admin_client, monkeypatch):
    key = _issue_key(admin_client)
    called = {"ocr": False}

    def fake_extract(*_args, **_kwargs):
        called["ocr"] = True
        return [], 0

    monkeypatch.setattr("admin_dashboard.api._extract_ocr_rows", fake_extract)
    payload = encrypt_json({"file_type": "image", "file_b64": base64.b64encode(b"x").decode("ascii")},
                           key)
    resp = admin_client.post("/api/v1/ocr/extract",
                             json={"csp_id": "CSP777", "payload": payload},
                             headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401
    assert called["ocr"] is False


def test_server_ocr_pins_configured_rapidocr_engine(monkeypatch):
    import config
    from admin_dashboard import api
    from core import ocr_table

    monkeypatch.setattr(config, "SERVER_OCR_ENGINE", "rapidocr")
    monkeypatch.setattr(config, "OCR_ENGINE", "auto")
    monkeypatch.setattr(ocr_table, "_ENGINE_OVERRIDE", None)
    monkeypatch.setattr(ocr_table, "_STRICT_ENGINE", False)

    seen = {}

    def fake_impl(file_bytes, file_type, page_from=None, page_to=None):
        seen["engine"] = config.OCR_ENGINE
        seen["override"] = ocr_table._ENGINE_OVERRIDE
        seen["strict"] = ocr_table._STRICT_ENGINE
        return [{"name": "SERVER"}], 1

    monkeypatch.setattr(api, "_extract_ocr_rows_with_engine", fake_impl)
    rows, pages = api._extract_ocr_rows(b"image", "image")
    assert rows == [{"name": "SERVER"}]
    assert pages == 1
    assert seen == {"engine": "rapidocr", "override": "rapidocr", "strict": True}
    assert config.OCR_ENGINE == "auto"
    assert ocr_table._ENGINE_OVERRIDE is None
    assert ocr_table._STRICT_ENGINE is False


def test_rapidocr_words_adapter(monkeypatch):
    from core import ocr_table

    class FakeRapidOCR:
        def __call__(self, _img):
            return ([
                [[[0, 0], [100, 0], [100, 20], [0, 20]], "123456789012", 0.99],
                [[[120, 0], [240, 0], [240, 20], [120, 20]], "RAMESH", 0.95],
            ], 0.01)

    monkeypatch.setattr(ocr_table, "_RAPIDOCR_MODEL", FakeRapidOCR())
    words = ocr_table._rapidocr_words(__import__("numpy").zeros((40, 260), dtype="uint8"))
    assert words == [
        {"t": "123456789012", "x": 50.0, "yc": 10.0, "conf": 0.99},
        {"t": "RAMESH", "x": 180.0, "yc": 10.0, "conf": 0.95},
    ]


def test_csp_server_ocr_client_encrypts_document_body(tmp_path, monkeypatch):
    import config
    from core import server_ocr_client

    path = tmp_path / "scan.png"
    path.write_bytes(b"customer-name-ramesh-mobile-9876543210")
    monkeypatch.setattr(config, "SERVER_OCR_ENABLED", True)
    monkeypatch.setattr(config, "ADMIN_API_BASE", "https://eko.example/api/v1")
    monkeypatch.setattr(config, "ADMIN_CSP_ID", "CSP777")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "real-secret-key")

    captured = {}

    from core.ocr_excel import rows_to_xlsx_bytes, xlsx_bytes_to_rows

    class Resp:
        status_code = 200

        def json(self):
            xlsx_b64 = base64.b64encode(
                rows_to_xlsx_bytes([{"name": "RAMESH KUMAR"}])).decode("ascii")
            return {"ok": True, "payload": encrypt_json({
                "request_id": captured["request_id"],
                "xlsx_b64": xlsx_b64,
                "page_count": 1,
                "row_count": 1,
            }, config.ADMIN_API_KEY)}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        encrypted_wire = str(json)
        assert "customer-name-ramesh" not in encrypted_wire
        assert "9876543210" not in encrypted_wire
        clear = decrypt_json(json["payload"], config.ADMIN_API_KEY)
        captured["request_id"] = clear["request_id"]
        assert base64.b64decode(clear["file_b64"]) == path.read_bytes()
        return Resp()

    monkeypatch.setattr("core.server_ocr_client.requests.post", fake_post)
    result = server_ocr_client.extract_file(str(path), "image")
    assert captured["url"] == "https://eko.example/api/v1/ocr/extract"
    assert captured["headers"]["X-API-Key"] == "real-secret-key"
    # Client returns .xlsx BYTES (never rows) — parsed in memory downstream.
    assert xlsx_bytes_to_rows(result["xlsx_bytes"]) == [{"name": "RAMESH KUMAR"}]


def test_extraction_uses_server_ocr_before_local_image_ocr(tmp_path, monkeypatch):
    from PIL import Image
    from core import extraction

    upload = tmp_path / "scan.png"
    Image.new("RGB", (20, 20), "white").save(upload)
    ddir = tmp_path / "draft"
    ddir.mkdir()

    from core.ocr_excel import rows_to_xlsx_bytes

    def fake_extract_file(path, file_type, page_from=None, page_to=None, progress=None):
        assert path == str(upload)
        assert file_type == "image"
        return {
            "xlsx_bytes": rows_to_xlsx_bytes([{
                "account_number": "123456789012",
                "name": "SERVER OCR ROW",
                "mobile": "9876543210",
                "balance_band": "100<1000",
            }]),
            "page_count": 1,
            "row_count": 1,
        }

    def local_should_not_run(*_args, **_kwargs):
        raise AssertionError("local OCR should not run when server OCR succeeds")

    monkeypatch.setattr("core.server_ocr_client.enabled", lambda: True)
    monkeypatch.setattr("core.server_ocr_client.extract_file", fake_extract_file)
    monkeypatch.setattr("core.ocr_table.extract_with_image", local_should_not_run)

    rows, images = extraction._ocr_image(str(upload), str(ddir), 0)
    assert rows[0]["name"] == "SERVER OCR ROW"
    # Centralized OCR writes NOTHING to the CSP disk — table-only review.
    assert images == []
    assert not (ddir / "page_000.png").exists()


def test_pdf_is_sent_one_page_per_request_and_combined(tmp_path, monkeypatch):
    """A multi-page PDF must be sent ONE PAGE PER REQUEST (so no single request
    times out) and the per-page rows combined into one result."""
    import io as _io
    from PIL import Image
    import config
    from core import server_ocr_client
    from core.ocr_excel import xlsx_bytes_to_rows

    # a real 3-page PDF
    img = Image.new("RGB", (200, 120), "white")
    buf = _io.BytesIO()
    img.save(buf, "PDF", save_all=True, append_images=[img, img])
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(buf.getvalue())

    monkeypatch.setattr(config, "SERVER_OCR_ENABLED", True)
    monkeypatch.setattr(config, "ADMIN_API_BASE", "https://eko.example/api/v1")
    monkeypatch.setattr(config, "ADMIN_CSP_ID", "CSP777")
    monkeypatch.setattr(config, "ADMIN_API_KEY", "real-secret-key")

    calls = {"n": 0}

    def fake_send_image(img_bytes, timeout, retries):
        calls["n"] += 1
        return [{"name": f"ROW{calls['n']}"}]      # one row per page

    monkeypatch.setattr(server_ocr_client, "_send_image", fake_send_image)
    seen = []
    result = server_ocr_client.extract_file(
        str(pdf), "pdf", progress=lambda d, t, m: seen.append(m))

    assert calls["n"] == 3                          # one request PER PAGE
    assert result["page_count"] == 3
    rows = xlsx_bytes_to_rows(result["xlsx_bytes"])
    assert [r["name"] for r in rows] == ["ROW1", "ROW2", "ROW3"]   # combined, in order
    assert any("page 3 of 3" in m for m in seen)    # per-page progress fired


def test_ocr_excel_roundtrip_is_lossless_including_leading_zeros():
    """The .xlsx transport must not silently drop leading zeros from account /
    mobile numbers (a plain numeric cell would). All cells are written as text."""
    from core.ocr_excel import rows_to_xlsx_bytes, xlsx_bytes_to_rows

    rows = [
        {"account_number": "00123456789", "name": "RAMESH KUMAR",
         "mobile": "09876543210", "balance_band": "100<1000"},
        {"account_number": "34000111222", "name": "SITA DEVI",
         "mobile": "9812345678", "balance_band": "B>10000"},
    ]
    blob = rows_to_xlsx_bytes(rows)
    assert blob[:2] == b"PK"                       # a real .xlsx (zip) container
    assert xlsx_bytes_to_rows(blob) == rows        # exact, leading zeros intact
    assert xlsx_bytes_to_rows(rows_to_xlsx_bytes([])) == []


def test_ocr_extract_returns_503_when_ocr_stack_unavailable(admin_client, monkeypatch):
    """If an OCR dependency is missing/broken on the server, the endpoint must
    degrade to a clean 503 — it must NOT raise at import and take the portal
    down. Simulated by making the lazy OCR import fail."""
    import sys
    key = _issue_key(admin_client)
    monkeypatch.setitem(sys.modules, "core.ocr_excel", None)  # -> ImportError on import

    resp = admin_client.post("/api/v1/ocr/extract",
                             json={"csp_id": "CSP777", "payload": {}},
                             headers={"X-API-Key": key})
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "ocr_unavailable"


def test_ocr_extract_returns_503_busy_when_at_capacity(admin_client, monkeypatch):
    """When all OCR slots are taken, the endpoint backs the client off with 503
    ocr_busy instead of piling on more work — protecting the shared box."""
    import threading
    from admin_dashboard import api

    key = _issue_key(admin_client)
    # A full semaphore: acquire its only permit so the request can't get one.
    sem = threading.BoundedSemaphore(1)
    assert sem.acquire(blocking=False)
    monkeypatch.setattr(api, "_OCR_SEMAPHORE", sem)

    called = {"ocr": False}

    def fake_extract(*_a, **_k):
        called["ocr"] = True
        return [], 0

    monkeypatch.setattr(api, "_extract_ocr_rows", fake_extract)
    payload = encrypt_json({"request_id": "BUSY1", "file_type": "image",
                            "file_b64": base64.b64encode(b"x").decode("ascii")}, key)
    resp = admin_client.post("/api/v1/ocr/extract",
                             json={"csp_id": "CSP777", "payload": payload},
                             headers={"X-API-Key": key})
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "ocr_busy"
    assert called["ocr"] is False        # never ran OCR while at capacity

    from admin_dashboard.db import get_connection
    with get_connection() as conn:
        m = conn.execute("SELECT status FROM ocr_metrics WHERE request_id='BUSY1'").fetchone()
    assert m["status"] == "busy"


def test_ocr_log_page_renders_and_shows_metrics_only(admin_client, monkeypatch):
    """The admin OCR sharing-log page renders from ocr_metrics and shows counts
    only — never any customer identifier."""
    key = _issue_key(admin_client)
    rows = [{"account_number": "123456789012", "name": "RAMESH KUMAR",
             "mobile": "9876543210", "balance_band": "100<1000"}]
    monkeypatch.setattr("admin_dashboard.api._extract_ocr_rows",
                        lambda *_a, **_k: (rows, 1))
    payload = encrypt_json({"request_id": "LOG1", "file_type": "pdf",
                            "file_b64": base64.b64encode(b"x").decode("ascii")}, key)
    admin_client.post("/api/v1/ocr/extract",
                      json={"csp_id": "CSP777", "payload": payload},
                      headers={"X-API-Key": key})

    page = admin_client.get("/ocr-log")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "CSP777" in html            # operational identity is fine
    assert "RAMESH" not in html        # customer PII must never appear
    assert "9876543210" not in html
    assert "123456789012" not in html
