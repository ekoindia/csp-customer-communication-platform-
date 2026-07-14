"""Delivery-status webhook ingestion."""

from core import webhooks
from database import queries


def _attempt_with_provider_id(case_id, pid, status="wa_attempted"):
    aid = queries.insert_comm_attempt(case_id, "whatsapp", status,
                                      sent_at="2026-06-29T10:00:00")
    queries.set_provider_message_id(aid, pid)
    return aid


def test_wa_ack_delivered_then_read(seeded_case):
    """Baileys WAMessageStatus: 3 = DELIVERY_ACK, 4 = READ."""
    _attempt_with_provider_id(seeded_case, "WA1")
    assert webhooks.handle_whatsapp_ack("WA1", 3)["status"] == "wa_delivered"
    assert webhooks.handle_whatsapp_ack("WA1", 4)["status"] == "wa_read"


def test_wa_ack_no_downgrade(seeded_case):
    """A late 'delivered' must not overwrite 'read'."""
    _attempt_with_provider_id(seeded_case, "WA2")
    webhooks.handle_whatsapp_ack("WA2", 4)        # read
    r = webhooks.handle_whatsapp_ack("WA2", 3)    # delivered (late)
    assert r["reason"] == "no-downgrade"
    assert queries.get_attempt_by_provider_id("WA2")["status"] == "wa_read"


def test_wa_ack_error_marks_failed(seeded_case):
    """Baileys ack 0 = ERROR."""
    _attempt_with_provider_id(seeded_case, "WA3")
    r = webhooks.handle_whatsapp_ack("WA3", 0)
    assert r["status"] == "wa_failed"


def test_wa_ack_legacy_engine_minus_one_marks_failed(seeded_case):
    """Old whatsapp-web.js builds (if still running) send ack=-1 for errors."""
    _attempt_with_provider_id(seeded_case, "WA3B")
    r = webhooks.handle_whatsapp_ack("WA3B", -1, engine="whatsapp-web.js")
    assert r["status"] == "wa_failed"


def test_wa_ack_legacy_engine_mapping(seeded_case):
    """Old whatsapp-web.js builds: 2 = delivered, 3 = read (different from Baileys)."""
    _attempt_with_provider_id(seeded_case, "WA1B")
    r = webhooks.handle_whatsapp_ack("WA1B", 2, engine="whatsapp-web.js")
    assert r["status"] == "wa_delivered"


def test_wa_ack_unknown_message_id(seeded_case):
    r = webhooks.handle_whatsapp_ack("DOES_NOT_EXIST", 3)
    assert r["ok"] is False


def test_wa_ack_read_receipts_disabled_caps_at_delivered(seeded_case):
    """If the recipient disabled WhatsApp read receipts, WhatsApp never sends a
    READ event for them — the message correctly stays at 'wa_delivered' forever,
    and that alone still counts as 'reached' for the dashboard (batch_overview)."""
    _attempt_with_provider_id(seeded_case, "WA4")
    webhooks.handle_whatsapp_ack("WA4", 3)  # DELIVERY_ACK — the ceiling for such users
    assert queries.get_attempt_by_provider_id("WA4")["status"] == "wa_delivered"

    ov = queries.batch_overview("B_TEST")
    assert ov["reached"] == 1  # delivered-only counts as reached, no READ needed


def test_sms_delivered(seeded_case):
    aid = queries.insert_comm_attempt(seeded_case, "sms", "sms_sent")
    queries.set_provider_message_id(aid, "SMS1")
    r = webhooks.handle_sms_status("SMS1", "delivered")
    assert r["status"] == "sms_delivered"


def test_sms_failure_escalates(seeded_case):
    aid = queries.insert_comm_attempt(seeded_case, "sms", "sms_sent")
    queries.set_provider_message_id(aid, "SMS2")
    webhooks.handle_sms_status("SMS2", "failed")
    bt = queries.get_business_tracking(seeded_case)
    assert bt["is_escalated"] == 1


def test_sms_unknown_status_ignored(seeded_case):
    aid = queries.insert_comm_attempt(seeded_case, "sms", "sms_sent")
    queries.set_provider_message_id(aid, "SMS3")
    r = webhooks.handle_sms_status("SMS3", "weird-status")
    assert r["status"] is None
