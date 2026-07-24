"""Column-header mapping across the real bank-list formats we've seen."""
from core.column_mapper import map_columns, extract_row


def test_ac_no_header_maps_to_account():
    # The Khusrupur CSP list heads its account column "A/C No" (not "Account No").
    # It must still map to account_number — and NOT get stolen by, nor steal, the
    # adjacent "A/C Name" column.
    headers = ["A/C No", "A/C Name", "Address", "Mobile No", "INOPERATIVE", "Agent Co"]
    m = map_columns(headers)
    assert m["account_number"] == "A/C No"
    assert m["name"] == "A/C Name"
    assert m["mobile"] == "Mobile No"
    # This format has no balance-band column at all.
    assert "balance_band" not in m


def test_ac_no_dotted_and_spaced_variants():
    for h in ("A/C NO.", "AC No", "A/C  No"):
        m = map_columns([h, "A/C Name"])
        assert m.get("account_number") == h


def test_extract_row_bandless_format():
    headers = ["A/C No", "A/C Name", "Address", "Mobile No"]
    m = map_columns(headers)
    raw = {"A/C No": "35880060911", "A/C Name": "MONU KUMAR",
           "Address": "PAPPU SINGH LODIPUR ... PATNA", "Mobile No": "7644097341"}
    row = extract_row(raw, m)
    assert row["account_number"] == "35880060911"
    assert row["name"] == "MONU KUMAR"
    assert row["mobile"] == "7644097341"
    # balance_band absent from the mapping -> not in the extracted row.
    assert "balance_band" not in row


def test_standard_account_no_header_still_maps():
    # Regression: the original "Account No" header must keep working.
    m = map_columns(["Account No", "Name", "Balance Band", "Mobile"])
    assert m["account_number"] == "Account No"
    assert m["name"] == "Name"
    assert m["balance_band"] == "Balance Band"
    assert m["mobile"] == "Mobile"
