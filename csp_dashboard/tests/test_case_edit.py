"""Per-case corrections: extraction is OCR-based, so any field read off the
document can be wrong and only the CSP can fix it against the source."""

import pytest

from database import queries


def test_edit_all_fields_reencrypts_and_reads_back(seeded_case):
    r = queries.update_case_fields(seeded_case, {
        "name": "RAMESH KUMAAR", "mobile": "9876500001",
        "father_name": "RAJU KUMAAR", "village": "Testpuri",
        "taluka": "Sample Block", "address": "VILL-TESTPUR NEW",
    })
    assert r["ok"] is True
    case = queries.get_case(seeded_case)
    assert case["name"] == "RAMESH KUMAAR"          # decrypts back
    assert case["mobile"] == "9876500001"
    assert case["village"] == "Testpuri"
    assert case["address"] == "VILL-TESTPUR NEW"


def test_account_number_edit_updates_dedup_index(seeded_case):
    r = queries.update_case_fields(seeded_case, {"account_number": "3577864799"})
    assert r["ok"] is True
    assert queries.get_case(seeded_case)["account_number"] == "3577864799"
    # the blind index moved with it, so dedup still works on the NEW number
    assert queries.account_exists("inoperative_accounts", "3577864799")
    assert not queries.account_exists("inoperative_accounts", "3577864748")


def test_duplicate_account_number_refused(seeded_case):
    """One account = one case: an edit must not create a second case for an
    account that already exists."""
    queries.insert_customer_case(
        case_id="C_TEST2", batch_id="B_TEST", campaign_id="inoperative_accounts",
        account_number="9999000011", name="SITA", mobile="9876500002",
        father_name=None, balance_band="100<1000", village="V", taluka="T",
        address="A", band_label="100<1000", tone="normal",
        template_id="template_1", is_sensitive=False,
    )
    r = queries.update_case_fields(seeded_case, {"account_number": "9999000011"})
    assert r["ok"] is False
    assert "already" in r["error"].lower()
    assert queries.get_case(seeded_case)["account_number"] == "3577864748"


def test_blank_name_or_account_refused(seeded_case):
    assert queries.update_case_fields(seeded_case, {"name": "   "})["ok"] is False
    assert queries.update_case_fields(seeded_case, {"account_number": ""})["ok"] is False


def test_edit_refused_after_closure_purge(seeded_case):
    """PII must never be re-introduced after the DPDP purge."""
    from core import tracking
    tracking.transition(seeded_case, "case_closed")
    r = queries.update_case_fields(seeded_case, {"name": "SOMEONE ELSE"})
    assert r["ok"] is False
    assert "purged" in r["error"].lower() or "closed" in r["error"].lower()
    assert queries.get_case(seeded_case)["name"] is None


def test_unknown_fields_ignored(seeded_case):
    r = queries.update_case_fields(seeded_case, {"balance_band": "B>10000",
                                                "name": "NEW NAME"})
    assert r["ok"] is True
    assert r["changed"] == ["name"]                  # band is NOT editable here
    assert queries.get_case(seeded_case)["balance_band"] == "100<1000"
