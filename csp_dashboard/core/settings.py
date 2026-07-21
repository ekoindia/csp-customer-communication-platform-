"""
CSP settings — single source of truth for CSP identity used in messages.

Stored in the branches table (seeded from config.py on first run).
config.py provides only the initial defaults; once the CSP edits settings
on the dashboard, the DB value wins. The template engine reads CSP details
through here so messages always reflect the current settings.

Per the Golden Rule: a configuration change is a meaningful event, so it
persists to the DB (branches) and is audit-logged by the route.
"""

import config
from database import queries


def get_csp_settings() -> dict:
    """Return current CSP name / phone / address. DB first, config.py fallback."""
    branch = queries.get_branch()
    if branch:
        keys = branch.keys()
        return {
            "csp_name": branch["csp_name"],
            "csp_phone": branch["csp_phone"],
            "csp_address": branch["csp_address"],
            "branch_code": (branch["branch_code"] if "branch_code" in keys else "") or "",
        }
    return {
        "csp_name": config.CSP_NAME,
        "csp_phone": config.CSP_PHONE,
        "csp_address": config.CSP_ADDRESS,
        "branch_code": "",
    }


def update_csp_settings(csp_name: str, csp_phone: str, csp_address: str,
                        branch_code: str = "") -> dict:
    """Validate and persist CSP settings. Returns {ok, errors}."""
    errors = []
    csp_name = (csp_name or "").strip()
    csp_phone = (csp_phone or "").strip()
    csp_address = (csp_address or "").strip()
    branch_code = (branch_code or "").strip()

    if not csp_name:
        errors.append("CSP name is required")
    if not csp_phone:
        errors.append("CSP phone is required")
    if not csp_address:
        errors.append("CSP address is required")

    if errors:
        return {"ok": False, "errors": errors}

    queries.update_branch(csp_name, csp_phone, csp_address, branch_code or None)
    return {"ok": True, "errors": []}


# ── Mobile-scanner import passphrase ─────────────────────────────────────────
# Shared secret between the CSP phone app and this desktop app: the phone
# encrypts the scanned Excel into a .cspx with it, and the dashboard decrypts on
# upload (see core/import_crypto.py). Stored locally in the config KV table — it
# is operational config, not customer PII, and never leaves the PC. We store the
# passphrase itself (not a hash) because we must re-derive the AES key to decrypt
# the file; that is acceptable since it lives only in the local DB on the CSP's
# own machine, the same trust boundary as pii.key.

_IMPORT_PASSPHRASE_KEY = "mobile_import_passphrase"


def get_import_passphrase() -> str:
    """The .cspx decryption passphrase, or '' if the CSP hasn't set one yet."""
    return (queries.get_config_value(_IMPORT_PASSPHRASE_KEY) or "").strip()


def set_import_passphrase(passphrase: str) -> dict:
    """Persist (or clear) the .cspx import passphrase. Returns {ok, errors}.
    A blank value clears it (disables encrypted mobile import)."""
    passphrase = (passphrase or "").strip()
    if passphrase and len(passphrase) < 6:
        return {"ok": False, "errors": ["Passphrase must be at least 6 characters "
                                        "(must match the one set in the phone app)."]}
    queries.set_config_value(_IMPORT_PASSPHRASE_KEY, passphrase)
    return {"ok": True, "errors": []}


def import_passphrase_is_set() -> bool:
    return bool(get_import_passphrase())
