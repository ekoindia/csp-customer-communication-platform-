"""Northbound reporting must be FORMAT-INDEPENDENT.

Balance band exists only in some bank lists (Alamgir's has it; Sanjeev's and
Rajan's don't), so a band-only rollup makes those CSPs a blank "?" bar in the
admin view. Contactability and case outcomes hold for every format."""

from database import queries


def _case(idx, mobile="9876500000", band="100<1000"):
    cid = f"C_BIF_{idx}"
    queries.insert_customer_case(
        case_id=cid, batch_id="B_BIF", campaign_id="inoperative_accounts",
        account_number=f"ACC{idx}", name=f"NAME{idx}", mobile=mobile,
        father_name=None, balance_band=band, village="V", taluka="T", address="A",
        band_label=band, tone="normal", template_id="template_1", is_sensitive=False,
    )
    queries.init_business_tracking(cid)
    return cid


def _payload(db):
    from database.db import get_connection
    from core.admin_reporter import _campaign_progress
    with get_connection() as conn:
        return _campaign_progress(conn, "inoperative_accounts")


def test_reports_contactability_split(db):
    queries.insert_document("B_BIF", "inoperative_accounts", "f.csv", "csv")
    _case(1, mobile="9876500001")
    _case(2, mobile="")            # sheet had no number (Sanjeev-Excel case)
    _case(3, mobile="")
    p = _payload(db)
    assert p["with_mobile"] == 1
    assert p["no_mobile"] == 2     # a DATA gap, not "poor reach"


def test_reports_outcome_counts_without_any_band(db):
    """A bandless list still produces a meaningful breakdown."""
    from core import tracking
    queries.insert_document("B_BIF", "inoperative_accounts", "f.csv", "csv")
    c1 = _case(1, band="")         # no balance band at all
    c2 = _case(2, band="")
    tracking.set_outcome(c1, outcome="deceased")
    p = _payload(db)
    counts = {o["outcome"]: o["count"] for o in p["outcomes"]}
    assert counts.get("deceased") == 1
    assert counts.get("not_recorded") == 1     # c2 has no outcome yet


def test_reports_not_on_whatsapp_count(db):
    """Distinguishes "no WhatsApp account" from a network failure — that is what
    tells Eko whether SMS/DLT activation is the real blocker."""
    queries.insert_document("B_BIF", "inoperative_accounts", "f.csv", "csv")
    cid = _case(1)
    aid = queries.insert_comm_attempt(cid, "whatsapp", "wa_failed")
    queries.update_comm_status(aid, "wa_failed",
                               "This number is not on WhatsApp (or the number is wrong)")
    assert _payload(db)["wa_not_on_whatsapp"] == 1


def test_payload_carries_no_identifiers(db):
    """DPDP: the northbound payload is counts only."""
    queries.insert_document("B_BIF", "inoperative_accounts", "f.csv", "csv")
    _case(1, mobile="9876500001")
    blob = repr(_payload(db))
    assert "9876500001" not in blob
    assert "NAME1" not in blob
    assert "ACC1" not in blob


# ── DYNAMIC dimensions: the UI renders whatever the data has ─────────────────

def test_category_breakdown_discovers_only_dimensions_with_data(db):
    """A bandless list must still produce groupings (village/taluka) — and the
    band dimension must be absent entirely, not an empty/"NA" bucket."""
    queries.insert_document("B_BIF", "inoperative_accounts", "f.csv", "csv")
    for i, (village, band) in enumerate([("Ahiraule", ""), ("Ahiraule", ""),
                                         ("Belsand", "")], start=1):
        cid = f"C_DIM_{i}"
        queries.insert_customer_case(
            case_id=cid, batch_id="B_BIF", campaign_id="inoperative_accounts",
            account_number=f"A{i}", name=f"N{i}", mobile="9876500000",
            father_name=None, balance_band=band, village=village, taluka="Tamkuhi",
            address="A", band_label=band, tone="normal", template_id="template_1",
            is_sensitive=False)
        queries.init_business_tracking(cid)

    cats = queries.dimension_breakdown("inoperative_accounts")
    dims = {c["dimension"] for c in cats}
    assert "village" in dims and "taluka" in dims
    assert "band_label" not in dims          # no band in this list at all
    villages = {c["value"]: c["total"] for c in cats if c["dimension"] == "village"}
    assert villages == {"Ahiraule": 2, "Belsand": 1}


def test_category_breakdown_includes_band_when_present(db):
    queries.insert_document("B_BIF", "inoperative_accounts", "f.csv", "csv")
    for i, band in enumerate(["100<1000", "100<1000", "B>10000"], start=1):
        cid = f"C_DIM2_{i}"
        queries.insert_customer_case(
            case_id=cid, batch_id="B_BIF", campaign_id="inoperative_accounts",
            account_number=f"B{i}", name=f"N{i}", mobile="9876500000",
            father_name=None, balance_band=band, village="V", taluka="T",
            address="A", band_label=band, tone="normal", template_id="template_1",
            is_sensitive=False)
        queries.init_business_tracking(cid)

    cats = queries.dimension_breakdown("inoperative_accounts")
    bands = {c["value"]: c["total"] for c in cats if c["dimension"] == "band_label"}
    assert bands == {"100<1000": 2, "B>10000": 1}


def test_categories_are_in_the_northbound_payload(db):
    queries.insert_document("B_BIF", "inoperative_accounts", "f.csv", "csv")
    _case(1)
    p = _payload(db)
    assert isinstance(p["categories"], list)
    assert any(c["dimension"] == "village" for c in p["categories"])
    # counts only — no identifiers
    blob = repr(p["categories"])
    assert "NAME1" not in blob and "9876500000" not in blob


def test_visit_statuses_are_mutually_exclusive_and_sum_to_total(db):
    """The visit panel must reconcile with the case count. It used to omit the
    'no message sent yet' bucket entirely, so 87 cases showed as 11."""
    from core import tracking
    queries.insert_document("B_BIF", "inoperative_accounts", "f.csv", "csv")
    ids = [_case(i) for i in range(1, 6)]
    # leave #1 untouched (pending), advance the others through the state machine
    queries.update_business_status(ids[1], "customer_not_visited")
    tracking.transition(ids[2], "customer_visited_in_progress")
    tracking.transition(ids[3], "customer_visited_in_progress")
    tracking.transition(ids[3], "process_completed")
    tracking.transition(ids[4], "case_closed")

    p = _payload(db)
    five = (p["visit_not_started"] + p["visit_pending"] + p["in_progress"]
            + p["completed"] + p["closed"])
    assert p["total"] == 5
    assert five == 5, f"current statuses must add up to total, got {five}"
    assert p["visit_not_started"] == 1
    # 'visited' is CUMULATIVE (ever visited) and may overlap the five above
    assert p["visited"] >= 1
