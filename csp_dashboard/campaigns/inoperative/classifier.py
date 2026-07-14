import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

with open(_CONFIG_PATH, encoding="utf-8") as _f:
    _CAMPAIGN_CONFIG = json.load(_f)


def classify(balance_band_str: str) -> dict:
    """
    Map a raw balance band string from the bank document to campaign classification.
    Returns a dict with band, tone, template_id, is_sensitive, reason.
    """
    raw = balance_band_str.strip().upper().replace(" ", "")

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
