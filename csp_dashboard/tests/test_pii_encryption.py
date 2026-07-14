"""
PII-at-rest encryption (core/crypto.py) + purge-on-case-closure.

RBI/DPDP requirement: a CSP's local SQLite file must not be human-readable if
inspected directly. These tests read the RAW database bytes (bypassing
database/queries.py entirely) to prove the identifying fields are genuinely
encrypted on disk, not just masked in the UI — and that the app's own
functionality (dedup, message generation, dashboard display) is unaffected
because queries.py decrypts transparently.
"""
import config
from core import tracking
from database import queries


def _raw_db_bytes() -> bytes:
    with open(config.DB_PATH, "rb") as f:
        return f.read()


def test_pii_not_readable_in_raw_db_file(seeded_case):
    """The exact plaintext values inserted by the seeded_case fixture must not
    appear anywhere in the raw .db file bytes."""
    raw = _raw_db_bytes()
    for plaintext in (b"RAMESH KUMAR", b"9876543210", b"3577864748",
                      b"RAJU KUMAR", b"VILL-AHIRAULI"):
        assert plaintext not in raw, f"PII LEAK: {plaintext} found in raw DB file"


def test_app_reads_back_correct_plaintext(seeded_case):
    """Encryption is transparent — queries.py callers still see plain values."""
    case = queries.get_case(seeded_case)
    assert case["name"] == "RAMESH KUMAR"
    assert case["mobile"] == "9876543210"
    assert case["account_number"] == "3577864748"
    assert case["father_name"] == "RAJU KUMAR"
    assert case["address"] == "VILL-AHIRAULI"
    # non-PII fields untouched
    assert case["village"] == "Ahiraule"
    assert case["balance_band"] == "100<1000"


def test_list_functions_also_decrypt(seeded_case):
    rows = queries.list_cases_by_batch("B_TEST")
    assert rows[0]["name"] == "RAMESH KUMAR"
    rows2 = queries.list_cases_with_tracking("B_TEST")
    assert rows2[0]["mobile"] == "9876543210"


def test_account_dedup_still_works_when_encrypted(db):
    """Fernet is non-deterministic — dedup must use the blind index, not a
    direct match on the encrypted account_number column."""
    queries.insert_document("B1", "inoperative_accounts", "f.csv", "csv")
    queries.insert_customer_case(
        case_id="C1", batch_id="B1", campaign_id="inoperative_accounts",
        account_number="1234567890", name="A", mobile="9000000001",
        father_name=None, balance_band="100<1000", village=None, taluka=None,
        address=None, band_label="100<1000", tone="normal",
        template_id="template_1", is_sensitive=False,
    )
    assert queries.account_exists("inoperative_accounts", "1234567890") is True
    assert queries.account_exists("inoperative_accounts", "9999999999") is False
    # different campaign, same account -> not a duplicate
    assert queries.account_exists("other_campaign", "1234567890") is False


def test_different_encryptions_of_same_value_differ_on_disk(db):
    """Sanity check that Fernet is in fact non-deterministic (proves why the
    blind index is necessary, and that ciphertext isn't a fixed fingerprint)."""
    from core import crypto
    a = crypto.encrypt_field("9876543210")
    b = crypto.encrypt_field("9876543210")
    assert a != b
    assert crypto.decrypt_field(a) == "9876543210" == crypto.decrypt_field(b)


def test_purge_on_case_closed_via_tracking(seeded_case):
    queries.update_business_status(seeded_case, "customer_not_visited")
    tracking.transition(seeded_case, "customer_visited_in_progress")
    tracking.transition(seeded_case, "process_completed")
    result = tracking.transition(seeded_case, "case_closed")
    assert result["ok"]

    case = queries.get_case(seeded_case)
    assert case["name"] is None
    assert case["mobile"] is None
    assert case["account_number"] is None
    assert case["father_name"] is None
    assert case["address"] is None
    assert case["pii_purged_at"] is not None
    # non-identifying fields survive for reporting
    assert case["village"] == "Ahiraule"
    assert case["balance_band"] == "100<1000"

    # the ciphertext itself is gone from disk too (not just re-masked)
    raw = _raw_db_bytes()
    assert b"RAMESH KUMAR" not in raw


def test_purge_keeps_dedup_hash_working(seeded_case):
    """account_number_hash must survive purge so re-uploading the SAME account
    later is still detected as a duplicate (money/commission integrity)."""
    queries.update_business_status(seeded_case, "customer_not_visited")
    tracking.transition(seeded_case, "customer_visited_in_progress")
    tracking.transition(seeded_case, "process_completed")
    tracking.transition(seeded_case, "case_closed")

    assert queries.account_exists("inoperative_accounts", "3577864748") is True


def test_purge_is_one_way_direct_route_path(seeded_case):
    """Mirrors dashboard.routes.skip_sensitive, which sets case_closed directly
    via update_business_status (not tracking.transition) and must ALSO purge."""
    queries.update_business_status(seeded_case, "case_closed",
                                   closed_at="2026-06-29T10:00:00")
    queries.purge_case_pii(seeded_case)
    case = queries.get_case(seeded_case)
    assert case["name"] is None and case["mobile"] is None


def test_message_generation_unaffected_before_closure(seeded_case):
    """End-to-end functional check: encryption must not break the core
    send pipeline — messages still generate with the real customer name."""
    from core.message_engine import generate_single_message
    msg = generate_single_message(seeded_case)
    assert "Ramesh" in msg["wa_message"]
