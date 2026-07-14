"""Balance-band classification — the REAL bank bands.

Real document bands (low -> high balance):
    0.1<100, 100<1000  -> normal  (template_1)
    1000<10000         -> urgent  (template_3)
    B>10000            -> urgent  (template_3), NOT sensitive (productization
                          decision #15 — no special "verify first" flag)
"""

import pytest
from campaigns.inoperative.classifier import classify


def test_near_zero_band():
    r = classify("0.1<100")
    assert r["band"] == "0.1<100"
    assert r["template_id"] == "template_1"
    assert r["tone"] == "normal"
    assert r["is_sensitive"] is False


def test_low_balance_band():
    r = classify("100<1000")
    assert r["template_id"] == "template_1"
    assert r["tone"] == "normal"
    assert r["is_sensitive"] is False


def test_mid_balance_band_urgent():
    r = classify("1000<10000")
    assert r["band"] == "1000<10000"
    assert r["template_id"] == "template_3"
    assert r["tone"] == "urgent"
    assert r["is_sensitive"] is False


def test_top_band_urgent_not_sensitive():
    """B>10000 = large idle balance: urgent. As of productization #15 it is NOT
    sensitive — no special verify-first gate (data is verified twice already)."""
    r = classify("B>10000")
    assert r["band"] == "B>10000"
    assert r["template_id"] == "template_3"
    assert r["tone"] == "urgent"
    assert r["is_sensitive"] is False


def test_top_band_variants():
    # B>10000 variants all map to the top band. As of productization decision
    # #15 the top band is NO LONGER sensitive — the data is verified twice
    # (extraction review + case-detail check), so no "verify first" flag.
    for variant in (">10000", "B >10000", "B>10000"):
        c = classify(variant)
        assert c["band"] == "B>10000"
        assert c["is_sensitive"] is False


def test_band_string_is_whitespace_tolerant():
    assert classify("  100<1000 ")["template_id"] == "template_1"


def test_unrecognised_band_raises():
    with pytest.raises(ValueError):
        classify("not-a-band")
