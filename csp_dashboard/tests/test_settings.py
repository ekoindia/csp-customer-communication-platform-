"""CSP settings — persistence and live reflection in messages."""

import config
from core import settings
from core.template_engine import generate_messages


def test_defaults_seeded_from_config(db):
    s = settings.get_csp_settings()
    assert s["csp_name"] == config.CSP_NAME


def test_update_persists(db):
    settings.update_csp_settings("Naya CSP", "9999988888", "Naya Bazaar")
    s = settings.get_csp_settings()
    assert s["csp_name"] == "Naya CSP"
    assert s["csp_phone"] == "9999988888"


def test_update_reflected_in_messages(db):
    settings.update_csp_settings("Naya CSP", "9999988888", "Naya Bazaar")
    m = generate_messages("template_1", "Ramesh")
    assert "Naya CSP" in m["wa_message"]
    assert "Naya Bazaar" in m["wa_message"]          # branch address shown
    # The CSP's phone number is deliberately NOT in the message (CSP safety —
    # customer is asked to visit the branch; number stays hidden).
    assert "9999988888" not in m["wa_message"]
    assert "9999988888" not in m["sms_message"]


def test_blank_fields_rejected(db):
    r = settings.update_csp_settings("", "123", "addr")
    assert r["ok"] is False
    assert any("name" in e.lower() for e in r["errors"])
