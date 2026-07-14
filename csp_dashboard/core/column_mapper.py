"""
Maps raw column names from any bank document format to our internal field names.
Case-insensitive, partial-match based — handles OCR typos and formatting variations.
"""

# Order matters: earlier fields win a header. Real bank headers are included as
# aliases (MEMB_CUST_A = account, MOBILE_NBR = mobile).
#
# MVP SCOPE: we extract ONLY the four fields the campaign actually needs —
# account_number, name, mobile, balance_band. Father's name / taluka / village /
# address are intentionally NOT extracted in the MVP: they are never used in the
# message (DPDP keeps the message generic), extracting fewer columns is less
# error-prone on a mobile-photo scan, and dropping father_name + address also
# means less customer PII stored at rest. They stay blank end-to-end (all
# downstream reads default them to "", so nothing breaks). To bring any of them
# back post-MVP, just re-add its line here — no other code change needed.
_FIELD_KEYWORDS = {
    "account_number": ["account", "acct", "acc no", "account no",
                       "memb_cust", "cust_a", "memb cust", "member"],
    "name":           ["name", "customer name", "holder"],
    "mobile":         ["mobile", "phone", "contact", "mo no", "mob", "msisdn"],
    "balance_band":   ["balance band", "bal band", "balance", "band"],
    # ── post-MVP (kept for easy re-enable) ──────────────────────────────────
    # "father_name":  ["father", "fthr", "f/o", "s/o", "d/o"],
    # "taluka":       ["taluka", "tehsil", "block"],
    # "village":      ["village", "vill", "gram"],
    # "address":      ["address", "addr", "with_pin", "with pin"],
}


def map_columns(raw_headers: list) -> dict:
    """
    Returns {internal_field: raw_header} for each field we can match.
    Unmatched fields are absent from the returned dict.
    """
    mapping = {}
    used = set()  # a raw header, once claimed, can't be reused by a later field
    lowered = {h: h.lower().strip() for h in raw_headers if h}

    for field, keywords in _FIELD_KEYWORDS.items():
        for raw, low in lowered.items():
            if raw in used:
                continue
            if any(kw in low for kw in keywords):
                mapping[field] = raw
                used.add(raw)
                break

    return mapping


def extract_row(raw_row: dict, mapping: dict) -> dict:
    """
    Given a raw dict row and the column mapping, return a cleaned dict
    with internal field names.  Missing optional fields become None.
    """
    result = {}
    for field, raw_col in mapping.items():
        val = raw_row.get(raw_col)
        result[field] = str(val).strip() if val is not None else None
    return result
