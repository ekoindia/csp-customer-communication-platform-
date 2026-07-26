import requests
import config


def send_whatsapp(mobile: str, message: str) -> dict:
    """
    POST {mobile, message} to the local WhatsApp HTTP server (wa_server.js,
    running on Baileys — no Chromium/browser involved).
    Returns {success: bool, message_id: str|None, error: str|None, reason: str|None}.
    message_id is Baileys' message id, used to match delivery ACKs.

    IMPORTANT: the bridge answers HTTP 200 with {"success": false, ...} for a
    real refusal (e.g. the number is not on WhatsApp). This used to be read as a
    SUCCESS because only the HTTP status was checked — so a case that never got a
    message was recorded as sent. The JSON body is now the source of truth.
    """
    try:
        resp = requests.post(
            f"{config.WA_SERVER_URL}/send",
            json={"mobile": mobile, "message": message},
            timeout=30,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or not data.get("success"):
            return {"success": False, "message_id": None,
                    "error": data.get("error") or f"WhatsApp bridge HTTP {resp.status_code}",
                    "reason": data.get("reason")}
        return {"success": True, "message_id": data.get("message_id"),
                "error": None, "reason": None}
    except requests.RequestException as e:
        return {"success": False, "message_id": None,
                "error": f"WhatsApp bridge unreachable: {e}",
                "reason": "bridge_unreachable"}


def check_whatsapp(mobile: str) -> dict:
    """Is this number on WhatsApp? Lets the CSP verify a CORRECTED number before
    re-approving a failed case, instead of send-and-hope.
    Returns {ok: bool, on_whatsapp: bool|None, error: str|None}."""
    try:
        resp = requests.get(f"{config.WA_SERVER_URL}/check",
                            params={"mobile": mobile}, timeout=15)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or not data.get("ok"):
            return {"ok": False, "on_whatsapp": None,
                    "error": data.get("error") or f"HTTP {resp.status_code}"}
        return {"ok": True, "on_whatsapp": bool(data.get("on_whatsapp")), "error": None}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "on_whatsapp": None,
                "error": f"WhatsApp bridge unreachable: {e}"}


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
        try:
            data = resp.json()
        except ValueError:
            data = {}
        # Same trap as WhatsApp: MSG91 answers 200 with {"type": "error", ...} for
        # a rejected send (bad DLT template, unregistered sender, blocked number).
        # Treat the BODY as the source of truth, not just the HTTP status.
        if resp.status_code >= 400 or str(data.get("type", "")).lower() == "error":
            return {"success": False, "message_id": None,
                    "error": str(data.get("message") or data.get("error")
                                 or f"MSG91 HTTP {resp.status_code}")}
        return {"success": True, "message_id": data.get("request_id"), "error": None}
    except requests.RequestException as e:
        return {"success": False, "message_id": None, "error": str(e)}
