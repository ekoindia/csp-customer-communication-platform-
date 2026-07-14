"""End-to-end document processing pipeline (CSV path)."""

import os
from core.processor import process_upload, process_upload_batch
from database import queries


def _write_csv(tmp_path, rows_text):
    p = tmp_path / "bank.csv"
    p.write_text(rows_text, encoding="utf-8")
    return str(p)


def test_csv_pipeline_creates_cases_and_messages(db, tmp_path):
    csv_text = (
        "Account Number,Name,Mobile,Balance Band,Village\n"
        "3577864748,RAMESH KUMAR,9876543210,100<1000,Ahiraule\n"
        "3577864749,SITA DEVI,9876500000,1000<10000,Tamkuhi\n"
    )
    path = _write_csv(tmp_path, csv_text)
    batch_id, stats = process_upload(path, "inoperative_accounts")

    assert stats["valid"] == 2
    assert stats["invalid"] == 0
    assert stats["messages_generated"] == 2

    cases = queries.list_cases_by_batch(batch_id)
    assert len(cases) == 2

    # every valid case has a message
    for c in cases:
        assert queries.get_message(c["case_id"]) is not None


def test_uploaded_file_deleted_after_processing(db, tmp_path):
    """DPDP: raw uploaded file must not be kept."""
    path = _write_csv(
        tmp_path,
        "Account Number,Name,Mobile,Balance Band\n1,RAMESH,9876543210,100<1000\n",
    )
    process_upload(path, "inoperative_accounts")
    assert not os.path.exists(path)


def test_batch_upload_merges_multiple_files_into_one_batch(db, tmp_path):
    first = tmp_path / "first.csv"
    first.write_text(
        "Account Number,Name,Mobile,Balance Band,Village\n"
        "1,RAMESH,9876543210,100<1000,Ahiraule\n",
        encoding="utf-8",
    )
    second = tmp_path / "second.csv"
    second.write_text(
        "Account Number,Name,Mobile,Balance Band,Village\n"
        "2,SITA,9876500000,1000<10000,Tamkuhi\n",
        encoding="utf-8",
    )

    batch_id, stats = process_upload_batch(
        [str(first), str(second)],
        "inoperative_accounts",
        ["first.csv", "second.csv"],
    )

    assert stats["valid"] == 2
    assert stats["invalid"] == 0
    assert stats["messages_generated"] == 2
    assert not first.exists()
    assert not second.exists()

    doc = queries.get_document(batch_id)
    assert doc["original_name"].startswith("2 files:")
    assert len(queries.list_cases_by_batch(batch_id)) == 2
    # Messages are generated, but nothing is queued for dispatch automatically
    # anymore — that's a separate CSP decision (core/approval.py).
    assert len(queries.list_dispatch_queue(batch_id)) == 0
    assert len(queries.list_unqueued_cases(batch_id)) == 2


def test_bad_band_skipped_but_blank_mobile_kept(db, tmp_path):
    """A bad balance band drops the row; a blank/unusable mobile keeps the row
    as a 'not reachable' case (it must still be tracked for manual follow-up)."""
    csv_text = (
        "Account Number,Name,Mobile,Balance Band\n"
        "1,RAMESH,9876543210,100<1000\n"      # valid + reachable
        "2,SITA,12345,100<1000\n"             # unusable mobile -> not reachable
        "3,GEETA,9876500000,garbage-band\n"   # bad band -> dropped
    )
    path = _write_csv(tmp_path, csv_text)
    batch_id, stats = process_upload(path, "inoperative_accounts")
    assert stats["valid"] == 2          # RAMESH + SITA (kept, SITA not reachable)
    assert stats["invalid"] == 1        # GEETA dropped (bad band)
    # Nothing auto-queues; only RAMESH (reachable) is a send candidate.
    assert len(queries.list_dispatch_queue(batch_id)) == 0
    candidates = queries.list_unqueued_cases(batch_id)
    assert len(candidates) == 1
    assert candidates[0]["name"] == "RAMESH"


def test_blank_mobile_marked_not_reachable(db, tmp_path):
    path = _write_csv(
        tmp_path,
        "Account Number,Name,Mobile,Balance Band\n1,RAMESH,,100<1000\n",
    )
    batch_id, stats = process_upload(path, "inoperative_accounts")
    cases = queries.list_cases_by_batch(batch_id)
    assert len(cases) == 1
    cid = cases[0]["case_id"]
    assert queries.list_dispatch_queue(batch_id) == []     # never queued
    bt = queries.get_business_tracking(cid)
    assert bt["is_escalated"] == 1                          # flagged for manual


def test_top_band_message_generated_but_not_auto_queued(db, tmp_path):
    """B>10000 → message generated, but NOT queued. Nothing is auto-queued at
    generation time for ANY band — queuing is always an explicit CSP action.
    B>10000 is no longer sensitive (productization #15); it's a normal
    high-balance case that simply isn't queued until the CSP acts."""
    path = _write_csv(
        tmp_path,
        "Account Number,Name,Mobile,Balance Band\n1,RAMESH,9876543210,B>10000\n",
    )
    batch_id, stats = process_upload(path, "inoperative_accounts")
    cases = queries.list_cases_by_batch(batch_id)
    cid = cases[0]["case_id"]

    assert queries.get_message(cid) is not None       # message exists
    assert cases[0]["is_sensitive"] == 0              # top band no longer sensitive
    # nothing queued until the CSP explicitly approves/sends
    assert queries.list_dispatch_queue(batch_id) == []


def test_missing_required_column_fails_cleanly(db, tmp_path):
    path = _write_csv(tmp_path, "Name,Village\nRAMESH,Ahiraule\n")
    batch_id, stats = process_upload(path, "inoperative_accounts")
    assert stats["valid"] == 0
    assert "Required columns" in stats["errors"][0]["reason"]
