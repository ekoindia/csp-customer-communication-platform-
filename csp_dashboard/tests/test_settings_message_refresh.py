"""Editing the CSP's details in Settings must reach the MESSAGES.

The CSP name / address / phone are baked into each case's message when it is
generated, so changing them used to leave every existing message carrying the OLD
address — the CSP fixes their address and customers still get the wrong one."""

from core import approval, message_engine
from core.settings import update_csp_settings
from database import queries


def test_address_change_updates_unsent_messages(seeded_case):
    update_csp_settings("Dudahi CSP", "9876500000", "OLD ADDRESS WARD 1", "1332")
    message_engine.generate_single_message(seeded_case)
    assert "OLD ADDRESS WARD 1" in queries.get_message(seeded_case)["wa_message"]

    update_csp_settings("Dudahi CSP", "9876500000", "NEW ADDRESS WARD 9", "1332")
    n = message_engine.regenerate_unsent_messages()

    assert n == 1
    wa = queries.get_message(seeded_case)["wa_message"]
    assert "NEW ADDRESS WARD 9" in wa
    assert "OLD ADDRESS WARD 1" not in wa


def test_name_change_reaches_both_channels(seeded_case):
    """The CSP name appears in BOTH the WhatsApp and the SMS text, so a rename must
    update both. (The current templates embed name + address only — not the phone
    — see campaigns/inoperative/templates.py.)"""
    update_csp_settings("Old CSP Name", "9111100000", "Addr", "1332")
    message_engine.generate_single_message(seeded_case)

    update_csp_settings("New CSP Name", "9222200000", "Addr", "1332")
    message_engine.regenerate_unsent_messages()

    msg = queries.get_message(seeded_case)
    for text in (msg["wa_message"], msg["sms_message"]):
        assert "New CSP Name" in text
        assert "Old CSP Name" not in text


def test_already_queued_message_is_left_alone(seeded_case):
    """A queued/sent message is the record of what the customer got — rewriting it
    would falsify history (and it can't be un-sent)."""
    update_csp_settings("Dudahi CSP", "9876500000", "OLD ADDRESS", "1332")
    message_engine.generate_single_message(seeded_case)
    approval.approve_case(seeded_case)          # now queued

    update_csp_settings("Dudahi CSP", "9876500000", "NEW ADDRESS", "1332")
    n = message_engine.regenerate_unsent_messages()

    assert n == 0
    assert "OLD ADDRESS" in queries.get_message(seeded_case)["wa_message"]
