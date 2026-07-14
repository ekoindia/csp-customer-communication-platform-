"""
Content-based taluka/village/address splitting.

Regression coverage for a real bug: the grid extractor used to assign fixed
column INDICES for taluka/village/address (mob_col+1, mob_col+2, ncols-1).
On the actual bank scan, the rightmost ruled lines are unreliable, so the
detected column count often falls short — village_col ends up out of range
(blank) and address_col collapses onto the same index as taluka_col
(Address showing a duplicate of Taluka). _split_trailing_fields fixes this
by never depending on how many columns were detected: it merges whatever
text exists after the mobile column and parses taluka/village/address out of
that merged string by content instead of position.

Uses the same example place names already documented in CLAUDE.md's Initial
JSON sample (Tamkuhi Raj / Ahiraule / Kushinagar) — not extracted from any
real customer scan.
"""

from core.ocr_table import _split_trailing_fields, _clean_village


def test_clean_village_strips_taluka_and_address_bleed():
    assert _clean_village("Ahiraule AH") == "Ahiraule"
    assert _clean_village("Ahiraule DU") == "Ahiraule"
    assert _clean_village("Ahiraule") == "Ahiraule"
    assert _clean_village("Raj AHIRAUI").lower() != "raj"        # taluka word dropped
    assert _clean_village("Ahiraule 274302") == "Ahiraule"        # digit fragment dropped
    assert _clean_village("Tamkuhi Raj") == ""                    # pure taluka bleed -> blank
    assert _clean_village("") == ""


def test_relation_prefix_splits_all_three_fields():
    tail = "Tamkuhi Raj Ahiraule S/O NOOR ALAM VILL-AHIRAULI POST-DUDAHI DIST-KUSHINAG 274302"
    r = _split_trailing_fields(tail)
    assert r["taluka"] == "Tamkuhi Raj"
    assert r["village"] == "Ahiraule"
    assert r["address"].startswith("S/O")
    assert "274302" in r["address"]


def test_village_not_blank_when_no_relation_prefix():
    """The exact bug from the review screenshot: Village must not come back
    blank just because there's no S/O/D/O/W/O marker — VILL-/POST-/DIST- is
    a valid fallback address start."""
    tail = "Tamkuhi Raj Ahiraule VILL-AHIRAULI POST-DUDAHI DIST-KUSHINAGAR 274302"
    r = _split_trailing_fields(tail)
    assert r["village"] == "Ahiraule"
    assert r["village"] != ""
    assert r["address"].startswith("VILL-")


def test_address_never_duplicates_taluka():
    """The exact bug from the review screenshot: Address must never come back
    identical to Taluka (that happened when both fields read the same
    out-of-range-collapsed column index)."""
    tail = "Tamkuhi Raj Dudahi W/O NOOR ALAM VILL-AHIRAULI; POST-DUDAHI DIST-KUSHINAG"
    r = _split_trailing_fields(tail)
    assert r["address"] != r["taluka"]
    assert r["taluka"] == "Tamkuhi Raj"
    assert r["village"] == "Dudahi"


def test_no_taluka_match_still_finds_address():
    tail = "S/O SOME FATHER VILL-SOMEWHERE POST-ELSEWHERE 123456"
    r = _split_trailing_fields(tail)
    assert r["taluka"] == ""
    assert r["address"].startswith("S/O")


def test_empty_tail_returns_all_blank():
    r = _split_trailing_fields("")
    assert r == {"taluka": "", "village": "", "address": ""}


def test_no_address_marker_at_all_keeps_text_as_village_not_lost():
    """If nothing recognisable marks where the address starts, the leftover
    text is kept as village rather than silently discarded."""
    tail = "Tamkuhi Raj SomeVillageName"
    r = _split_trailing_fields(tail)
    assert r["taluka"] == "Tamkuhi Raj"
    assert r["village"] == "SomeVillageName"
    assert r["address"] == ""


def test_ocr_mangled_markers_still_split():
    """OCR often reads S/O as S/0 and POST- as P0ST-. The address must still
    be recognised despite those digit-for-letter misreads."""
    tail = "Tamkuhi Raj Ahiraule S/0 NOOR ALAM VILL-AHIRAULI P0ST-DUDAHI 274302"
    r = _split_trailing_fields(tail)
    assert r["village"] == "Ahiraule"
    assert r["address"].startswith("S/0")
    assert "274302" in r["address"]


def test_pin_code_is_last_resort_address_anchor():
    """When OCR eats every S/O and VILL-/POST- marker, the 6-digit PIN code is
    still enough to split the address off from the village."""
    tail = "Tamkuhi Raj Ahiraule AHIRAULI KUSHINAGAR 274302"
    r = _split_trailing_fields(tail)
    assert r["taluka"] == "Tamkuhi Raj"
    assert r["village"] == "Ahiraule"
    assert "274302" in r["address"]


def test_village_never_repeats_taluka():
    """If the taluka text leaks into the village slot, it must be trimmed off
    rather than shown twice."""
    tail = "Tamkuhi Raj Tamkuhi Raj Ahiraule VILL-AHIRAULI 274302"
    r = _split_trailing_fields(tail)
    assert r["taluka"] == "Tamkuhi Raj"
    assert not r["village"].lower().startswith("tamkuhi")
