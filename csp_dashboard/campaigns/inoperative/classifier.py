import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

with open(_CONFIG_PATH, encoding="utf-8") as _f:
    _CAMPAIGN_CONFIG = json.load(_f)


def classify(balance_band_str: str) -> dict:
    """
    Map a raw balance band string from the bank document to campaign classification.
    Returns a dict with band, tone, template_id, is_sensitive, reason.

    Some CSP bank lists have NO balance-band column (e.g. the Khusrupur format),
    or OCR mangles the band beyond recognition. Rather than crash the whole
    commit, an empty/unrecognised band falls back to the safe DEFAULT: normal
    tone + template_1, category label "NA". This is DPDP-safe because the message
    never contains the balance (§9) — a missing band only affects tone/category,
    not what the customer is told.
    """
    raw = (balance_band_str or "").strip().upper().replace(" ", "")

    # NO band at all (a bandless bank list, e.g. the Khusrupur format whose
    # columns are A/C No | A/C Name | Address | Mobile | INOPERATIVE) -> safe
    # default, processed normally. This is distinct from a NON-EMPTY but
    # unreadable band below: an empty band is EXPECTED for a whole bandless list
    # (don't flag every row), whereas a garbled band on a band-carrying list is a
    # per-row misread the caller still wants surfaced (commit flags it, the
    # direct processor drops it) — so that case still raises.
    if not raw:
        return _default_classification()

    # Real bank bands: 0.1<100, 100<1000, 1000<10000, B>10000.
    # "B>10000" (the top band) appears as B>10000 / >10000 and OCR sometimes
    # mangles the symbols, so match it by the 10000 ceiling marker first.
    if "B>" in raw or raw.startswith(">") or (">10000" in raw) or \
            ("10000" in raw and ("B" in raw or ">" in raw)):
        band_cfg = _get_band("band_4")
    elif "<" in raw:
        try:
            value = float(raw.split("<")[0])
        except ValueError:
            raise ValueError(f"Unrecognised balance band: '{balance_band_str}'")
        band_cfg = _band_for_value(value)
    else:
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"Unrecognised balance band: '{balance_band_str}'")
        band_cfg = _band_for_value(value)

    return {
        "band": band_cfg["label"],
        "tone": band_cfg["tone"],
        "template_id": band_cfg["template_id"],
        "is_sensitive": band_cfg["is_sensitive"],
    }


def _default_classification() -> dict:
    """Fallback for a missing/unrecognised balance band: normal tone, the normal
    WhatsApp template, not sensitive, category label "NA" (so it groups on its
    own in reporting without pretending to be a real balance band)."""
    return {
        "band": "NA",
        "tone": "normal",
        "template_id": "template_1",
        "is_sensitive": False,
    }


def _band_for_value(value: float) -> dict:
    """Map a numeric balance (or band lower-bound) to the real campaign bands."""
    if 0.1 <= value < 100:
        return _get_band("band_1")
    if 100 <= value < 1000:
        return _get_band("band_2")
    if 1000 <= value < 10000:
        return _get_band("band_3")
    return _get_band("band_4")


def _get_band(band_id: str) -> dict:
    for b in _CAMPAIGN_CONFIG["bands"]:
        if b["id"] == band_id:
            return b
    raise KeyError(f"Band id not found: {band_id}")
