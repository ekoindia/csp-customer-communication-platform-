# Inoperative-accounts message templates.
#
# WORDING (finalised): "Branch: <name> / Address: <addr>" layout, bilingual
# (Hindi + English), normal + urgent. The CSP's PHONE NUMBER is deliberately NOT
# shown in any message (CSP Mukesh's safety request) — the customer is asked to
# VISIT the SBI CSP branch (name + address only). {csp_phone} is still accepted
# by the render functions for signature compatibility but never appears in text.
#
# LANGUAGE: config.MESSAGE_LANGUAGE selects the outgoing language ("hi" default,
# best for rural SBI customers; set "en" for English). WhatsApp + SMS both follow
# it. The same wording is registered with Meta as official Utility templates —
# see WABA_TEMPLATES below (the {{1}}/{{2}}/{{3}} form the Cloud API needs).

import config


def _lang(lang=None) -> str:
    l = (lang or getattr(config, "MESSAGE_LANGUAGE", "hi") or "hi").lower()
    return l if l in ("hi", "en") else "hi"


# ── WhatsApp (plain text sent by the local bridge; {..} filled in Python) ─────
WA_TEMPLATES = {
    "en": {
        # normal: bands 0.1<100, 100<1000
        "template_1": (
            "Hello {name},\n\n"
            "Our records indicate that your SBI bank account has remained "
            "inactive for a long time. To reactivate your account, kindly visit "
            "your nearest SBI CSP branch.\n\n"
            "Branch:\n{csp_name}\n\n"
            "Address:\n{branch_address}\n\n"
            "Thank you,\n{csp_name}"
        ),
        # urgent: bands 1000<10000, B>10000
        "template_3": (
            "Hello {name},\n\n"
            "Our records indicate that your SBI bank account has remained "
            "inactive for a long time. Kindly visit your SBI CSP branch at the "
            "earliest to complete the required process.\n\n"
            "Branch:\n{csp_name}\n\n"
            "Address:\n{branch_address}\n\n"
            "Thank you,\n{csp_name}"
        ),
    },
    "hi": {
        "template_1": (
            "नमस्ते {name} जी,\n\n"
            "हमारे रिकॉर्ड के अनुसार आपके SBI बैंक खाते में काफी समय से कोई "
            "लेन-देन नहीं हुआ है। अपना खाता पुनः सक्रिय करवाने के लिए कृपया अपने "
            "नज़दीकी SBI CSP शाखा पर आएँ।\n\n"
            "शाखा:\n{csp_name}\n\n"
            "पता:\n{branch_address}\n\n"
            "धन्यवाद,\n{csp_name}"
        ),
        "template_3": (
            "नमस्ते {name} जी,\n\n"
            "हमारे रिकॉर्ड के अनुसार आपका SBI बैंक खाता काफी समय से निष्क्रिय है। "
            "आवश्यक प्रक्रिया पूरी करने के लिए कृपया जल्द से जल्द अपने SBI CSP "
            "शाखा पर आएँ।\n\n"
            "शाखा:\n{csp_name}\n\n"
            "पता:\n{branch_address}\n\n"
            "धन्यवाद,\n{csp_name}"
        ),
    },
}

# ── SMS fallback (all bands) — ENGLISH ONLY.
# Kept English (GSM-7, ~160 chars/segment = cheap, single segment) rather than
# Devanagari (UCS-2, ~70 chars/segment = multi-segment + costlier + needs a
# Unicode DLT template). WhatsApp can be Hindi; SMS is a fallback only (MSG91,
# triggered when WhatsApp fails) so it stays lean and Roman-script.
SMS_TEMPLATE = (
    "Hello {name}, our records indicate that your SBI account is inactive. "
    "Kindly visit {csp_name} (SBI CSP).\n\nAddress: {branch_address}"
)


def render_wa(template_id: str, name: str, csp_name: str, branch_address: str,
              csp_phone: str = None, lang: str = None) -> str:
    """Fill a WhatsApp template. csp_phone is accepted but never rendered."""
    tpl = WA_TEMPLATES[_lang(lang)][template_id]
    return tpl.format(name=name, csp_name=csp_name, branch_address=branch_address)


def render_sms(name: str, csp_name: str, csp_phone: str = None,
               branch_address: str = "", lang: str = None) -> str:
    """Fill the SMS template. SMS is English-only regardless of lang; csp_phone
    accepted but never rendered."""
    return SMS_TEMPLATE.format(
        name=name, csp_name=csp_name, branch_address=branch_address)


# ── Official WhatsApp Business Cloud API registration source-of-truth ─────────
# When Eko moves each CSP to the official Cloud API (per the WhatsApp route
# docs), these are the exact templates to submit at Meta (Utility category). The
# {{1}}/{{2}}/{{3}} placeholders map, in order, to the send-time parameters:
#   {{1}} = customer name   {{2}} = CSP branch name   {{3}} = branch address
# The plain-text WA_TEMPLATES above are the SAME wording for the current bridge.
WABA_PARAM_ORDER = ["customer_name", "csp_branch_name", "branch_address"]
WABA_TEMPLATES = [
    {"name": "account_inoperative_normal_en", "category": "UTILITY", "language": "en",
     "template_id": "template_1",
     "body": ("Hello {{1}},\n\nOur records indicate that your SBI bank account has "
              "remained inactive for a long time. To reactivate your account, kindly "
              "visit your nearest SBI CSP branch.\n\nBranch:\n{{2}}\n\nAddress:\n{{3}}"
              "\n\nThank you,\n{{2}}")},
    {"name": "account_inoperative_urgent_en", "category": "UTILITY", "language": "en",
     "template_id": "template_3",
     "body": ("Hello {{1}},\n\nOur records indicate that your SBI bank account has "
              "remained inactive for a long time. Kindly visit your SBI CSP branch at "
              "the earliest to complete the required process.\n\nBranch:\n{{2}}\n\n"
              "Address:\n{{3}}\n\nThank you,\n{{2}}")},
    {"name": "account_inoperative_normal_hi", "category": "UTILITY", "language": "hi",
     "template_id": "template_1",
     "body": ("नमस्ते {{1}} जी,\n\nहमारे रिकॉर्ड के अनुसार आपके SBI बैंक खाते में काफी समय "
              "से कोई लेन-देन नहीं हुआ है। अपना खाता पुनः सक्रिय करवाने के लिए कृपया अपने "
              "नज़दीकी SBI CSP शाखा पर आएँ।\n\nशाखा:\n{{2}}\n\nपता:\n{{3}}\n\nधन्यवाद,\n{{2}}")},
    {"name": "account_inoperative_urgent_hi", "category": "UTILITY", "language": "hi",
     "template_id": "template_3",
     "body": ("नमस्ते {{1}} जी,\n\nहमारे रिकॉर्ड के अनुसार आपका SBI बैंक खाता काफी समय से "
              "निष्क्रिय है। आवश्यक प्रक्रिया पूरी करने के लिए कृपया जल्द से जल्द अपने SBI CSP "
              "शाखा पर आएँ।\n\nशाखा:\n{{2}}\n\nपता:\n{{3}}\n\nधन्यवाद,\n{{2}}")},
]
