from campaigns.inoperative.templates import render_wa, render_sms
from core.settings import get_csp_settings


def generate_messages(template_id: str, customer_name: str) -> dict:
    """
    Given a template_id and customer name, returns wa_message and sms_message.
    CSP details come from current settings (DB-backed) — never hardcoded here.
    """
    csp = get_csp_settings()
    wa = render_wa(
        template_id=template_id,
        name=customer_name,
        csp_name=csp["csp_name"],
        branch_address=csp["csp_address"],
        csp_phone=csp["csp_phone"],
    )
    sms = render_sms(
        name=customer_name,
        csp_name=csp["csp_name"],
        csp_phone=csp["csp_phone"],
        branch_address=csp["csp_address"],
    )
    return {"wa_message": wa, "sms_message": sms}
