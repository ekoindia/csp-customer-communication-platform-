"""Message generation — bilingual templates + DPDP content rules."""

import config
from core.template_engine import generate_messages
from campaigns.inoperative.templates import render_sms, render_wa, WABA_TEMPLATES


def test_wa_message_contains_name_and_csp(db):
    m = generate_messages("template_1", "Ramesh")
    assert "Ramesh" in m["wa_message"]
    assert config.CSP_NAME in m["wa_message"]  # seeded default (branch name)


def test_no_financial_data_in_messages(db):
    """DPDP: no balance, no account number, no band in any message."""
    m = generate_messages("template_1", "Ramesh")
    for forbidden in ("100<1000", "3577864748", "balance", "rupee", "₹"):
        assert forbidden.lower() not in m["wa_message"].lower()
        assert forbidden.lower() not in m["sms_message"].lower()


def test_no_eko_bharat_anywhere(db):
    m = generate_messages("template_3", "Sita")
    assert "eko" not in m["wa_message"].lower()
    assert "eko" not in m["sms_message"].lower()


def test_phone_never_in_message(db):
    """CSP phone must never appear (Mukesh safety) — in either language."""
    for lang in ("hi", "en"):
        config.MESSAGE_LANGUAGE = lang
        wa = render_wa("template_1", "Ramesh", "Dudahi CSP", "Main Rd", "9876543210")
        sms = render_sms("Ramesh", "Dudahi CSP", "9876543210", "Main Rd")
        assert "9876543210" not in wa and "9876543210" not in sms
    config.MESSAGE_LANGUAGE = "hi"


def test_both_languages_render():
    for lang in ("hi", "en"):
        wa = render_wa("template_3", "Ramesh", "Dudahi CSP", "Main Road", lang=lang)
        sms = render_sms("Ramesh", "Dudahi CSP", branch_address="Main Road", lang=lang)
        assert "Ramesh" in wa and "Dudahi CSP" in wa and "Main Road" in wa
        assert "Ramesh" in sms and "Main Road" in sms
    # english is the readable GSM-7 SMS; sanity-check it stays a short message
    en_sms = render_sms("Ramesh", "Dudahi CSP", branch_address="Main Road", lang="en")
    assert len(en_sms) < 200


def test_active_templates_render(db):
    for tid in ("template_1", "template_3"):
        m = generate_messages(tid, "Ramesh")
        assert "Ramesh" in m["wa_message"]
        assert m["wa_message"].strip()


def test_waba_registration_templates_wellformed():
    """The official-Cloud-API templates must be complete + PII/label correct."""
    assert len(WABA_TEMPLATES) == 4
    for t in WABA_TEMPLATES:
        assert t["category"] == "UTILITY"
        assert t["language"] in ("en", "hi")
        for ph in ("{{1}}", "{{2}}", "{{3}}"):
            assert ph in t["body"]
        assert "eko" not in t["body"].lower()
