"""Some bank lists print the account number and the name with NO gap
("50000000001Ramesh Kumar"), so the reader returns them as ONE token. Digit-
cleaning that token used to DROP the first name word — names came out as "KUMAR"
instead of "RAMESH KUMAR". These tests lock the repair."""

import numpy as np
import pytest

from core import ocr_table


def _words(rows):
    """Fake reader output: one joined account+first-name token per row, plus the
    surname and a mobile, laid out in columns like the real page."""
    out = []
    for i, (acct, first, last, mob) in enumerate(rows):
        y = 100 + i * 40
        out.append({"t": f"{acct}{first}", "x": 100.0, "yc": y, "conf": 0.9})
        out.append({"t": last,            "x": 180.0, "yc": y, "conf": 0.9})
        out.append({"t": mob,             "x": 700.0, "yc": y, "conf": 0.9})
    return out


ROWS = [
    ("50000000001", "Ramesh",  "Kumar",  "9990000001"),
    ("50000000002", "SITA",    "KUMARI", "9990000002"),
    ("50000000003", "MOHAN", "DEVI",   "9990000003"),
    ("50000000004", "Rakesh", "Verma", "9990000004"),
    ("50000000005", "Suresh",  "Kumar",  "9990000005"),
    ("50000000006", "SUNITA",  "DEVI", "9990000006"),
]


def test_first_name_is_not_lost_when_joined_to_the_account():
    gray = np.full((600, 900), 255, dtype="uint8")
    recs = ocr_table._extract_content(gray, _words(ROWS))
    assert recs, "no rows extracted"
    got = {r["account_number"]: r["name"] for r in recs}
    for acct, first, last, _ in ROWS:
        assert acct in got, f"account {acct} missing"
        name = got[acct]
        assert first.upper() in name, f"first name lost for {acct}: {name!r}"
        assert last.upper() in name, f"surname lost for {acct}: {name!r}"


def test_account_number_stays_digits_only():
    gray = np.full((600, 900), 255, dtype="uint8")
    recs = ocr_table._extract_content(gray, _words(ROWS))
    for r in recs:
        assert r["account_number"].isdigit(), r["account_number"]


def test_plain_layout_still_works():
    """A normal page where account and name are separate tokens must be unchanged."""
    words = []
    for i, (acct, first, last, mob) in enumerate(ROWS):
        y = 100 + i * 40
        words += [{"t": acct, "x": 100.0, "yc": y, "conf": 0.9},
                  {"t": first, "x": 175.0, "yc": y, "conf": 0.9},
                  {"t": last, "x": 185.0, "yc": y, "conf": 0.9},
                  {"t": mob, "x": 700.0, "yc": y, "conf": 0.9}]
    gray = np.full((600, 900), 255, dtype="uint8")
    recs = ocr_table._extract_content(gray, words)
    got = {r["account_number"]: r["name"] for r in recs}
    for acct, first, last, _ in ROWS:
        assert first.upper() in got.get(acct, ""), got.get(acct)
