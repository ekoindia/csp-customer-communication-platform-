import requests
import config


def send_whatsapp(mobile: str, message: str) -> dict:
    """
    POST {mobile, message} to the local WhatsApp HTTP server (wa_server.js,
    running on Baileys — no Chromium/browser involved).
    Returns {success: bool, message_id: str|None, error: str|None}.
    message_id is Baileys' message id, used to match delivery ACKs.
    """
    try:
        resp = requests.post(
            f"{config.WA_SERVER_URL}/send",
            json={"mobile": mobile, "message": message},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"success": True, "message_id": data.get("message_id"), "error": None}
    except requests.RequestException as e:
        return {"success": False, "message_id": None, "error": str(e)}


def send_sms(mobile: str, message: str) -> dict:
    """
    Send SMS via MSG91.
    Returns {success: bool, message_id: str|None, error: str|None}.
    message_id is MSG91's request id, used to match delivery webhooks.
    """
    if not config.MSG91_AUTH_KEY:
        return {"success": False, "message_id": None, "error": "MSG91 not configured"}
    try:
        resp = requests.post(
            "https://api.msg91.com/api/v5/flow/",
            json={
                "flow_id": config.MSG91_TEMPLATE_ID,
                "sender": config.MSG91_SENDER_ID,
                "mobiles": f"91{mobile}",
                "VAR1": message,
            },
            headers={"authkey": config.MSG91_AUTH_KEY, "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {"success": True, "message_id": data.get("request_id"), "error": None}
    except requests.RequestException as e:
        return {"success": False, "message_id": None, "error": str(e)}
