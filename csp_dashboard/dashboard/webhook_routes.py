"""
Inbound webhook endpoints.

- /webhook/whatsapp  ← posted by wa_server.js on every ACK event (localhost only)
- /webhook/sms       ← posted by MSG91 delivery callbacks

These are machine-to-machine endpoints (no login session). They accept
delivery status only — never message content — per the no-reply policy.
"""

from flask import Blueprint, request, jsonify
import config
from core import webhooks

webhook_bp = Blueprint("webhook", __name__)


def _token_ok() -> bool:
    """If a webhook token is configured, require it. Otherwise allow (localhost)."""
    if not config.WEBHOOK_TOKEN:
        return True
    return request.headers.get("X-Webhook-Token") == config.WEBHOOK_TOKEN


@webhook_bp.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_ack():
    if not _token_ok():
        return jsonify({"ok": False, "reason": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    message_id = data.get("message_id")
    ack = data.get("ack")
    engine = data.get("engine", "baileys")
    try:
        ack = int(ack)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "reason": "invalid ack"}), 400
    result = webhooks.handle_whatsapp_ack(message_id, ack, engine=engine)
    return jsonify(result)


@webhook_bp.route("/webhook/sms", methods=["POST", "GET"])
def sms_status():
    if not _token_ok():
        return jsonify({"ok": False, "reason": "unauthorized"}), 401
    # MSG91 may send GET or POST depending on configuration.
    src = request.values if request.method == "GET" else (request.get_json(silent=True) or request.form)
    request_id = src.get("request_id") or src.get("requestId")
    status_str = src.get("status") or src.get("delivery_status") or src.get("report")
    result = webhooks.handle_sms_status(request_id, status_str)
    return jsonify(result)
