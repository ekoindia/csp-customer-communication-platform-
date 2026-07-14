"""Approval workflow — automatic vs manual review, freely mixable."""

import time

import pytest

from core import approval, comm_runner
from database import queries


@pytest.fixture(autouse=True)
def _fake_channel(monkeypatch):
    """Simulate a connected, always-succeeding WhatsApp channel so
    comm_runner.start() actually runs instead of short-circuiting on
    channels_ready() (which pings the real WA server / MSG91 config)."""
    monkeypatch.setattr(comm_runner, "channels_ready",
                        lambda: {"ok": True, "whatsapp": True, "sms": False, "reason": None})
    monkeypatch.setattr("core.dispatcher.send_whatsapp",
                        lambda mobile, message: {"success": True, "message_id": "FAKE1", "error": None})
    import config
    monkeypatch.setattr(config, "WA_DELAY_SECONDS", 0)


def _wait_for_dispatch():
    for _ in range(100):
        if not comm_runner.is_running():
            return
        time.sleep(0.05)


def _make_case(batch_id, idx, name, mobile="9876500000", band="100<1000",
               tone="normal", template_id="template_1", is_sensitive=False):
    case_id = f"{batch_id}_C{idx}"
    queries.insert_customer_case(
        case_id=case_id, batch_id=batch_id, campaign_id="inoperative_accounts",
        account_number=f"ACC{idx}", name=name, mobile=mobile, father_name=None,
        balance_band=band, village="V", taluka="T", address="A",
        band_label=band, tone=tone, template_id=template_id, is_sensitive=is_sensitive,
    )
    queries.init_business_tracking(case_id)
    return case_id


def _setup_batch(db, n=5, sensitive_idx=None):
    batch_id = "BATCH_APPROVAL_TEST"
    queries.insert_document(batch_id, "inoperative_accounts", "f.csv", "csv")
    case_ids = []
    for i in range(1, n + 1):
        sensitive = sensitive_idx is not None and i == sensitive_idx
        band = "B>10000" if sensitive else "100<1000"
        tone = "urgent" if sensitive else "normal"
        cid = _make_case(batch_id, i, f"NAME{i}", band=band, tone=tone,
                         template_id="template_3" if sensitive else "template_1",
                         is_sensitive=sensitive)
        case_ids.append(cid)
    from core.message_engine import generate_batch_messages
    generate_batch_messages(batch_id)
    return batch_id, case_ids


def test_nothing_queued_right_after_generation(db):
    batch_id, _ = _setup_batch(db, n=3)
    assert queries.list_dispatch_queue(batch_id) == []
    assert len(queries.list_unqueued_cases(batch_id)) == 3


def test_queue_and_dispatch_respects_chunk_size(db):
    batch_id, _ = _setup_batch(db, n=5)
    result = approval.queue_and_dispatch(batch_id, chunk_size=2)
    assert result["started"] is True
    _wait_for_dispatch()
    # 2 got queued+sent; 3 remain as candidates
    assert len(queries.list_unqueued_cases(batch_id)) == 3


def test_queue_and_dispatch_excludes_sensitive(db):
    batch_id, case_ids = _setup_batch(db, n=3, sensitive_idx=2)
    result = approval.queue_and_dispatch(batch_id, chunk_size=None)
    assert result["started"] is True
    _wait_for_dispatch()
    # sensitive case must never be auto-queued
    sensitive_case = case_ids[1]
    assert queries.get_latest_comm_attempt(sensitive_case) is None
    remaining = queries.list_unqueued_cases(batch_id)
    assert len(remaining) == 1
    assert remaining[0]["case_id"] == sensitive_case


def test_approve_case_queues_without_dispatching(db):
    batch_id, case_ids = _setup_batch(db, n=2)
    result = approval.approve_case(case_ids[0])
    assert result["ok"] is True
    # queued (has a pending attempt) but NOT sent — no thread started
    attempt = queries.get_latest_comm_attempt(case_ids[0])
    assert attempt["status"] == "pending"
    assert comm_runner.is_running() is False


def test_approve_case_works_for_sensitive(db):
    batch_id, case_ids = _setup_batch(db, n=1, sensitive_idx=1)
    result = approval.approve_case(case_ids[0])
    assert result["ok"] is True
    assert queries.get_latest_comm_attempt(case_ids[0])["status"] == "pending"


def test_approve_remaining_excludes_sensitive(db):
    batch_id, case_ids = _setup_batch(db, n=3, sensitive_idx=2)
    result = approval.approve_remaining(batch_id)
    assert result["approved"] == 2  # the 2 non-sensitive cases
    assert queries.get_latest_comm_attempt(case_ids[1]) is None  # sensitive untouched
    assert queries.get_latest_comm_attempt(case_ids[0])["status"] == "pending"
    assert queries.get_latest_comm_attempt(case_ids[2])["status"] == "pending"


def test_approve_remaining_respects_limit(db):
    batch_id, _ = _setup_batch(db, n=5)
    result = approval.approve_remaining(batch_id, limit=2)
    assert result["approved"] == 2
    assert len(queries.list_unqueued_cases(batch_id)) == 3


def test_individual_approval_then_automatic_send_includes_it(db):
    """Regression: previously, queue_and_dispatch only looked at fresh
    candidates and would report 'no cases' even when manually-approved cases
    were already pending. Approving must never be silently skipped."""
    batch_id, case_ids = _setup_batch(db, n=3)

    # CSP manually approves case 1 first.
    approval.approve_case(case_ids[0])
    assert len(queries.list_unqueued_cases(batch_id)) == 2

    # Then clicks "Send Automatically" for a total of 3.
    result = approval.queue_and_dispatch(batch_id, chunk_size=3)
    assert result["started"] is True
    assert result["reason"].startswith("Dispatching 3")
    _wait_for_dispatch()
    # The manually-approved case must be included, not skipped.
    assert queries.get_latest_comm_attempt(case_ids[0]) is not None
    assert len(queries.list_unqueued_cases(batch_id)) == 0


def test_automatic_send_of_fully_preapproved_batch_still_starts(db):
    """If everything was already approved manually, clicking Send must still
    dispatch it — it must not claim 'no cases awaiting a decision'."""
    batch_id, _ = _setup_batch(db, n=2)
    approval.approve_remaining(batch_id)
    assert queries.list_unqueued_cases(batch_id) == []

    result = approval.queue_and_dispatch(batch_id, chunk_size=None)
    assert result["started"] is True
    _wait_for_dispatch()


def test_approve_case_rejects_unreachable_case(db):
    """A blank-mobile case must be rejected clearly, not silently 'succeed'
    while actually doing nothing."""
    batch_id = "BATCH_APPROVAL_TEST"
    queries.insert_document(batch_id, "inoperative_accounts", "f.csv", "csv")
    case_id = _make_case(batch_id, 1, "NOMOBILE", mobile="")
    from core.message_engine import generate_batch_messages
    generate_batch_messages(batch_id)  # marks it escalated (not_reachable)

    result = approval.approve_case(case_id)
    assert result["ok"] is False
    assert "no mobile" in result["reason"].lower()
    # still just the one escalation attempt — approve_case must not touch it
    attempt = queries.get_latest_comm_attempt(case_id)
    assert attempt["status"] == "escalated"


def test_approve_case_rejects_already_queued(db):
    batch_id, case_ids = _setup_batch(db, n=1)
    approval.approve_case(case_ids[0])
    result = approval.approve_case(case_ids[0])  # approve again
    assert result["ok"] is False
    assert "already" in result["reason"].lower()


def test_queue_and_dispatch_no_candidates_and_none_queued_fails_clearly(db):
    batch_id, case_ids = _setup_batch(db, n=1, sensitive_idx=1)
    # Only a sensitive case exists, nothing approved yet -> nothing to send.
    result = approval.queue_and_dispatch(batch_id, chunk_size=None)
    assert result["started"] is False
    assert "awaiting a send decision" in result["reason"]
