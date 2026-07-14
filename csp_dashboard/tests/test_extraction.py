"""Extraction + review-gate: build draft -> commit reviewed rows."""

import os
import config
from core import extraction
from database import queries


def _csv(tmp_path, text):
    p = tmp_path / "bank.csv"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_build_draft_flags_rows(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path / "up"))
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    path = _csv(tmp_path,
        "Account Number,Name,Mobile,Balance Band,Village\n"
        "1,RAMESH,9876543210,100<1000,Ahiraule\n"
        "2,SITA,,B>10000,Tamkuhi\n")          # no mobile (unreachable)
    draft_id = extraction.build_draft([path], "inoperative_accounts", ["bank.csv"])
    rows = extraction.load_draft(draft_id)["rows"]
    assert len(rows) == 2
    assert rows[0]["reachable"] is True
    assert rows[1]["reachable"] is False        # blank mobile
    # B>10000 is no longer sensitive (productization decision #15).
    assert rows[1]["is_sensitive"] is False
    extraction.discard_draft(draft_id)


def test_commit_creates_cases_and_excludes_unreachable(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path / "up"))
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    path = _csv(tmp_path,
        "Account Number,Name,Mobile,Balance Band\n"
        "1,RAMESH,9876543210,100<1000\n"
        "2,SITA,,100<1000\n")
    draft_id = extraction.build_draft([path], "inoperative_accounts", ["bank.csv"])
    rows = extraction.load_draft(draft_id)["rows"]
    edited = [{"account_number": r["account_number"], "name": r["name"],
               "mobile": r["mobile"], "balance_band": r["balance_band"],
               "village": r.get("village", "")} for r in rows]

    batch_id, stats = extraction.commit_draft(draft_id, edited, "inoperative_accounts")
    assert stats["valid"] == 2
    assert stats["not_reachable"] == 1
    # Nothing is queued for dispatch automatically — that's now a separate CSP
    # decision (core/approval.py). RAMESH (reachable) is a send candidate;
    # SITA (no mobile) was already escalated and never becomes a candidate.
    assert len(queries.list_dispatch_queue(batch_id)) == 0
    candidates = queries.list_unqueued_cases(batch_id)
    assert len(candidates) == 1
    assert candidates[0]["name"] == "RAMESH"
    assert not os.path.exists(extraction._draft_dir(draft_id))  # cleaned up


def test_norm_account_strips_ocr_noise():
    assert extraction._norm_account("3577 864748") == "3577864748"
    assert extraction._norm_account("  3577864748 ") == "3577864748"
    assert extraction._norm_account("(none)") == "(none)"   # placeholder kept


def test_commit_dedups_duplicate_account_within_upload(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path / "up"))
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    path = _csv(tmp_path,
        "Account Number,Name,Mobile,Balance Band\n"
        "3577864748,RAMESH,9876543210,100<1000\n"
        "3577864748,SITA,9876500000,100<1000\n")   # same account twice
    draft_id = extraction.build_draft([path], "inoperative_accounts", ["bank.csv"])
    rows = extraction.load_draft(draft_id)["rows"]
    edited = [{"account_number": r["account_number"], "name": r["name"],
               "mobile": r["mobile"], "balance_band": r["balance_band"]} for r in rows]
    batch_id, stats = extraction.commit_draft(draft_id, edited, "inoperative_accounts")
    assert stats["valid"] == 1
    assert stats["duplicates"] == 1
    assert len(queries.list_cases_by_batch(batch_id)) == 1   # one case, not two


def test_commit_dedups_account_across_reuploads(db, tmp_path, monkeypatch):
    """Re-uploading the same page in a later batch must NOT create a 2nd case
    (money key = account number), so commission is never double-counted."""
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path / "up"))
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    csv_text = ("Account Number,Name,Mobile,Balance Band\n"
                "3577864748,RAMESH,9876543210,100<1000\n")

    d1 = extraction.build_draft([_csv(tmp_path, csv_text)], "inoperative_accounts", ["bank.csv"])
    r1 = extraction.load_draft(d1)["rows"]
    e1 = [{"account_number": r["account_number"], "name": r["name"],
           "mobile": r["mobile"], "balance_band": r["balance_band"]} for r in r1]
    _, s1 = extraction.commit_draft(d1, e1, "inoperative_accounts")
    assert s1["valid"] == 1 and s1["duplicates"] == 0

    # same account uploaded again (a second page-run / re-upload)
    d2 = extraction.build_draft([_csv(tmp_path, csv_text)], "inoperative_accounts", ["bank.csv"])
    r2 = extraction.load_draft(d2)["rows"]
    e2 = [{"account_number": r["account_number"], "name": r["name"],
           "mobile": r["mobile"], "balance_band": r["balance_band"]} for r in r2]
    _, s2 = extraction.commit_draft(d2, e2, "inoperative_accounts")
    assert s2["valid"] == 0
    assert s2["duplicates"] == 1


def test_commit_keeps_unreadable_band_flagged(db, tmp_path, monkeypatch):
    """Faithful capture: the sheet is the source of truth, so a row with an
    unreadable balance band is NOT dropped — it is KEPT (raw band preserved,
    normal template defaulted) and FLAGGED for review. (Was previously rejected;
    dropping real sheet data violates 'N stays N' / data-fidelity.)"""
    monkeypatch.setattr(config, "UPLOAD_FOLDER", str(tmp_path / "up"))
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    path = _csv(tmp_path, "Account Number,Name,Mobile,Balance Band\n1,RAMESH,9876543210,100<1000\n")
    draft_id = extraction.build_draft([path], "inoperative_accounts", ["bank.csv"])
    edited = [{"account_number": "1", "name": "RAMESH", "mobile": "9876543210",
               "balance_band": "garbage", "village": ""}]
    batch_id, stats = extraction.commit_draft(draft_id, edited, "inoperative_accounts")
    assert stats["valid"] == 1          # kept, not dropped
    assert stats["flagged"] >= 1        # flagged for review
    cases = queries.list_cases_by_batch(batch_id)
    assert len(cases) == 1
    assert cases[0]["balance_band"] == "garbage"   # raw sheet value preserved
