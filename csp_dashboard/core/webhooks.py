"""
Webhook ingestion — Communication Layer inbound.
Owner of: communication_attempts table (status updates in place).

STRICT no-reply policy:
- These handlers process DELIVERY STATUS only.
- Customer message content is never read, stored, or acted upon.
- MSG91 delivery statuses: delivered / failed (+ provider-specific codes)

WhatsApp engine note: wa_server.js runs on Baileys (direct WebSocket protocol,
no Chromium — see whatsapp/wa_server.js for why). Baileys' WAMessageStatus enum
differs from the older whatsapp-web.js ACK numbering, so both are mapped below
(the older map stays only in case an old wa_server.js build is still running):

    Baileys WAMessageStatus:        whatsapp-web.js ACK (legacy):
      1 = PENDING                     1 = sent
      2 = SERVER_ACK  (sent)          2 = delivered  (double grey tick)
      3 = DELIVERY_ACK (delivered)    3 = read       (blue tick)
      4 = READ        (blue tick)
      5 = PLAYED      (voice note)

"Reached" (see database/queries.py batch_overview) counts wa_delivered OR
wa_read as success — a customer who has disabled WhatsApp read receipts will
never produce a READ event for us (WhatsApp doesn't send it), so delivered
alone is treated as sufficient proof the message reached them.

Updates are applied IN PLACE on the matching communication_attempts row,
located via provider_message_id. No new file/row is created for a status change.
"""

from database import queries

# Baileys WAMessageStatus → our internal status (current engine, see above)
_BAILEYS_STATUS_MAP = {
    1: "wa_attempted",   # PENDING
    2: "wa_attempted",   # SERVER_ACK — sent to WhatsApp's server
    3: "wa_delivered",   # DELIVERY_ACK — delivered to device (double grey tick)
    4: "wa_read",        # READ (blue tick) — never arrives if the recipient
                         # has disabled read receipts; that's expected, not an error
    5: "wa_read",        # PLAYED (voice note) — implies read
}

# whatsapp-web.js ACK code → our internal status (legacy fallback)
_LEGACY_WA_ACK_MAP = {
    1: "wa_attempted",
    2: "wa_delivered",
    3: "wa_read",
}

# MSG91 delivery status string → our internal status
_SMS_STATUS_MAP = {
    "delivered": "sms_delivered",
    "delivery": "sms_delivered",
    "sent": "sms_sent",
    "failed": "sms_failed",
    "rejected": "sms_failed",
    "ndnc": "sms_failed",
    "blocked": "sms_failed",
}


def handle_whatsapp_ack(message_id: str, ack: int, engine: str = "baileys") -> dict:
    """
    Process a WhatsApp delivery-status event from wa_server.js.
    `engine` selects which status-code table to use (see module docstring) —
    defaults to "baileys" (the current engine); pass "whatsapp-web.js" only if
    an old build of wa_server.js is still forwarding events.
    Returns {ok: bool, status: str|None, reason: str|None}.
    """
    if not message_id:
        return {"ok": False, "status": None, "reason": "missing message_id"}

    status_map = _LEGACY_WA_ACK_MAP if engine == "whatsapp-web.js" else _BAILEYS_STATUS_MAP
    status = status_map.get(ack)
    if status is None:
        # ack 0 (ERROR) or whatsapp-web.js's -1 → mark failed; anything else ignored
        if ack in (0, -1):
            updated = queries.update_status_by_provider_id(
                message_id, "wa_failed", error_detail=f"WhatsApp reported ERROR (ack={ack})")
            return {"ok": updated, "status": "wa_failed",
                    "reason": None if updated else "unknown message_id"}
        return {"ok": True, "status": None, "reason": f"ignored ack {ack}"}

    # Never downgrade: don't overwrite 'read' with 'delivered', etc.
    existing = queries.get_attempt_by_provider_id(message_id)
    if existing and _wa_rank(existing["status"]) >= _wa_rank(status):
        return {"ok": True, "status": existing["status"], "reason": "no-downgrade"}

    updated = queries.update_status_by_provider_id(message_id, status)
    return {"ok": updated, "status": status,
            "reason": None if updated else "unknown message_id"}


def handle_sms_status(request_id: str, status_str: str) -> dict:
    """
    Process an MSG91 delivery webhook.
    Returns {ok: bool, status: str|None, reason: str|None}.
    """
    if not request_id:
        return {"ok": False, "status": None, "reason": "missing request_id"}

    mapped = _SMS_STATUS_MAP.get(str(status_str).strip().lower())
    if mapped is None:
        return {"ok": True, "status": None, "reason": f"ignored status '{status_str}'"}

    error = "MSG91 reported failure" if mapped == "sms_failed" else None
    updated = queries.update_status_by_provider_id(request_id, mapped, error_detail=error)

    # If SMS ultimately failed, escalate the case.
    if updated and mapped == "sms_failed":
        attempt = queries.get_attempt_by_provider_id(request_id)
        if attempt:
            queries.set_escalated(attempt["case_id"], True)
            queries.insert_comm_attempt(attempt["case_id"], "whatsapp", "escalated",
                                        error_detail="WA + SMS failed (webhook)")

    return {"ok": updated, "status": mapped,
            "reason": None if updated else "unknown request_id"}


def _wa_rank(status: str) -> int:
    """Ordering so a later event can't regress to an earlier state."""
    order = {
        "pending": 0, "wa_attempted": 1, "wa_delivered": 2, "wa_read": 3,
        "wa_failed": 1,  # failure can replace 'attempted' but not delivered/read
    }
    return order.get(status, 0)
