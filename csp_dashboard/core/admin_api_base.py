"""Helpers for addressing the Eko admin API from a CSP install."""
from urllib.parse import urlsplit, urlunsplit

import config


def normalize(value: str = None) -> str:
    """Return the API v1 base for either an API URL or admin-dashboard URL.

    Eko sometimes has the human dashboard URL handy (for example
    /csp-admin/login) while the client needs /csp-admin/api/v1. Accepting both
    shapes makes a generated .env resilient without exposing the address in the
    CSP flow.
    """
    raw = (value if value is not None else getattr(config, "ADMIN_API_BASE", ""))
    raw = str(raw or "").strip()
    if not raw:
        return ""

    parts = urlsplit(raw)
    path = (parts.path or "").rstrip("/")
    bits = [b for b in path.split("/") if b]

    if "api" in bits:
        i = bits.index("api")
        if len(bits) > i + 1 and bits[i + 1].lower() == "v1":
            bits = bits[:i + 2]
        else:
            bits = bits[:i + 1] + ["v1"]
    else:
        if bits and bits[-1].lower() in {"login", "api-keys", "fleet", "earnings",
                                         "campaigns", "whatsapp", "ocr-log"}:
            bits = bits[:-1]
        bits.extend(["api", "v1"])

    path = "/" + "/".join(bits)
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


def get() -> str:
    return normalize()
