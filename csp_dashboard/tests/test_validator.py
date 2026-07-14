"""Row validation — Pydantic CustomerRow."""

import pytest
from pydantic import ValidationError
from core.validator import CustomerRow


def test_valid_row():
    row = CustomerRow(
        account_number="3577864748", name="ramesh kumar",
        mobile="9876543210", balance_band="100<1000",
    )
    assert row.name == "RAMESH KUMAR"  # upper-cased
    assert row.mobile == "9876543210"


def test_mobile_strips_country_code():
    row = CustomerRow(account_number="1", name="x", mobile="919876543210",
                      balance_band="100<1000")
    assert row.mobile == "9876543210"


def test_mobile_strips_formatting():
    row = CustomerRow(account_number="1", name="x", mobile="+91 98765-43210",
                      balance_band="100<1000")
    assert row.mobile == "9876543210"


def test_unusable_mobile_becomes_blank_not_reachable():
    """An unusable mobile is normalised to '' (not reachable), not rejected,
    so the row is still kept for manual follow-up."""
    row = CustomerRow(account_number="1", name="x", mobile="12345",
                      balance_band="100<1000")
    assert row.mobile == ""


def test_blank_mobile_allowed():
    row = CustomerRow(account_number="1", name="x", mobile="",
                      balance_band="100<1000")
    assert row.mobile == ""


def test_empty_name_rejected():
    with pytest.raises(ValidationError):
        CustomerRow(account_number="1", name="   ", mobile="9876543210",
                    balance_band="100<1000")


def test_optional_fields_default_none():
    row = CustomerRow(account_number="1", name="x", mobile="9876543210",
                      balance_band="100<1000")
    assert row.father_name is None
    assert row.village is None
