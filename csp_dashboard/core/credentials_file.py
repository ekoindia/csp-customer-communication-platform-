"""
Write the CSP's own login — the login ID and password THEY chose during
first-run onboarding — to a plain-text `CSP_Login.txt` on their Desktop, as a
personal reminder/record so they never get locked out.

This is the operator's OWN credential on their OWN PC — NOT customer data — so
it carries no DPDP customer-PII concern (unlike case data, which is encrypted
and purged). The file advises keeping it safe and notes the password can be
changed later in Settings.

Never raises: a failure here (e.g. read-only Desktop) must not block onboarding
from completing — the account is already created in the DB regardless.
"""
import os

_CRED_FILENAME = "CSP_Login.txt"


def _desktop_dir() -> str:
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    return desktop if os.path.isdir(desktop) else home


def write_login_file(login_id: str, password: str) -> str:
    """Save the chosen credentials to CSP_Login.txt on the Desktop.
    Returns the file path, or None on any failure."""
    try:
        path = os.path.join(_desktop_dir(), _CRED_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "CSP Platform - your login\n"
                "==========================\n\n"
                f"Login ID  : {login_id}\n"
                f"Password  : {password}\n\n"
                "You chose these during first-time setup. Keep this file safe\n"
                "and do not share it. You can change your password later in\n"
                "Settings.\n"
            )
        return path
    except Exception:
        return None
