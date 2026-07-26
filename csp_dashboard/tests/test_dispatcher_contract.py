"""The bridge/provider answer is the source of truth — not the HTTP status.

A 200 response carrying {"success": false} (e.g. the number is not on WhatsApp)
used to be recorded as a SUCCESSFUL send, so a customer who never got a message
showed as reached. These tests lock that contract."""

import pytest

from core import dispatcher


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.content = b"x"

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_wa_200_with_success_false_is_a_failure(monkeypatch):
    monkeypatch.setattr(dispatcher.requests, "post",
                        lambda *a, **k: _Resp(200, {"success": False,
                                                    "reason": "not_on_whatsapp",
                                                    "error": "not on WhatsApp"}))
    r = dispatcher.send_whatsapp("9876543210", "hi")
    assert r["success"] is False
    assert r["reason"] == "not_on_whatsapp"
    assert "whatsapp" in r["error"].lower()


def test_wa_success_true_passes_message_id(monkeypatch):
    monkeypatch.setattr(dispatcher.requests, "post",
                        lambda *a, **k: _Resp(200, {"success": True, "message_id": "M1"}))
    r = dispatcher.send_whatsapp("9876543210", "hi")
    assert r["success"] is True and r["message_id"] == "M1"


def test_wa_non_json_or_5xx_is_a_failure(monkeypatch):
    monkeypatch.setattr(dispatcher.requests, "post", lambda *a, **k: _Resp(500, None))
    r = dispatcher.send_whatsapp("9876543210", "hi")
    assert r["success"] is False and r["error"]


def test_wa_bridge_unreachable_is_flagged(monkeypatch):
    def _boom(*a, **k):
        raise dispatcher.requests.RequestException("connection refused")
    monkeypatch.setattr(dispatcher.requests, "post", _boom)
    r = dispatcher.send_whatsapp("9876543210", "hi")
    assert r["success"] is False and r["reason"] == "bridge_unreachable"


def test_sms_200_with_error_type_is_a_failure(monkeypatch):
    import config
    monkeypatch.setattr(config, "MSG91_AUTH_KEY", "k")
    monkeypatch.setattr(dispatcher.requests, "post",
                        lambda *a, **k: _Resp(200, {"type": "error",
                                                    "message": "DLT template not approved"}))
    r = dispatcher.send_sms("9876543210", "hi")
    assert r["success"] is False
    assert "DLT" in r["error"]


def test_check_whatsapp_reports_registration(monkeypatch):
    monkeypatch.setattr(dispatcher.requests, "get",
                        lambda *a, **k: _Resp(200, {"ok": True, "on_whatsapp": False}))
    r = dispatcher.check_whatsapp("9876543210")
    assert r["ok"] is True and r["on_whatsapp"] is False
