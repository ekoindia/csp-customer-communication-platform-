"""Business-status state machine."""

from core import tracking
from database import queries


def _advance_to_sent(case_id):
    queries.update_business_status(case_id, "customer_not_visited")


def test_full_valid_path(seeded_case):
    _advance_to_sent(seeded_case)
    assert tracking.transition(seeded_case, "customer_visited_in_progress")["ok"]
    assert tracking.transition(seeded_case, "process_completed")["ok"]
    assert tracking.transition(seeded_case, "case_closed")["ok"]


def test_terminal_state_blocks_further(seeded_case):
    _advance_to_sent(seeded_case)
    tracking.transition(seeded_case, "customer_visited_in_progress")
    tracking.transition(seeded_case, "process_completed")
    tracking.transition(seeded_case, "case_closed")
    r = tracking.transition(seeded_case, "customer_visited_in_progress")
    assert r["ok"] is False
    assert "illegal" in r["reason"]


def test_skip_transition_blocked(seeded_case):
    _advance_to_sent(seeded_case)
    r = tracking.transition(seeded_case, "process_completed")  # skips in_progress
    assert r["ok"] is False


def test_unknown_status_rejected(seeded_case):
    r = tracking.transition(seeded_case, "banana")
    assert r["ok"] is False
    assert r["reason"] == "unknown status"


def test_visited_at_stamped(seeded_case):
    _advance_to_sent(seeded_case)
    tracking.transition(seeded_case, "customer_visited_in_progress")
    bt = queries.get_business_tracking(seeded_case)
    assert bt["visited_at"] is not None


def test_can_transition_helper():
    assert tracking.can_transition("customer_not_visited", "customer_visited_in_progress")
    # "Close" (case_closed) is now reachable from any active state — the Action
    # column offers just Visited + Close (product change: no separate "Done" step).
    assert tracking.can_transition("pending", "case_closed")
    assert tracking.can_transition("customer_visited_in_progress", "case_closed")
    # A terminal case still can't move anywhere.
    assert not tracking.can_transition("case_closed", "customer_visited_in_progress")


def test_next_action_labels():
    assert tracking.next_action("customer_not_visited")["label"] == "Visited"
    assert tracking.next_action("process_completed")["label"] == "Close"
    assert tracking.next_action("case_closed") is None


def test_skipped_case_not_counted_as_visited(seeded_case):
    """A case closed without a visit (skipped) must not appear as visited."""
    # Simulate the skip route: close without stamping visited_at.
    queries.update_business_status(seeded_case, "case_closed",
                                   closed_at="2026-06-29T10:00:00")
    ov = queries.batch_overview("B_TEST")
    assert ov["visited"] == 0
    assert queries.list_visit_log("B_TEST") == []


def test_genuine_visit_counted(seeded_case):
    queries.update_business_status(seeded_case, "customer_not_visited")
    tracking.transition(seeded_case, "customer_visited_in_progress")
    ov = queries.batch_overview("B_TEST")
    assert ov["visited"] == 1
    assert len(queries.list_visit_log("B_TEST")) == 1


# ── Case outcome / feedback (what the bank needs back) ───────────────────────

def test_close_with_outcome_records_reason(seeded_case):
    """Closing with "account holder has died" must store the reason in the SAME
    action — that is the whole point for the bank."""
    from core import tracking
    case_id = seeded_case
    r = tracking.transition(case_id, "case_closed",
                            outcome="deceased", note="family informed at branch")
    assert r["ok"] is True
    bt = queries.get_business_tracking(case_id)
    assert bt["status"] == "case_closed"
    assert bt["outcome"] == "deceased"
    assert bt["outcome_note"] == "family informed at branch"


def test_outcome_survives_pii_purge(seeded_case):
    """The PII purge on closure must NOT wipe the outcome — a report still has to
    explain what happened."""
    from core import tracking
    case_id = seeded_case
    tracking.transition(case_id, "case_closed", outcome="moved_away")
    case = queries.get_case(case_id)
    assert case["name"] is None                      # PII purged
    assert queries.get_business_tracking(case_id)["outcome"] == "moved_away"


def test_outcome_editable_after_closure(seeded_case):
    """A CSP usually learns the reason later, so it must be correctable even on a
    closed case."""
    from core import tracking
    case_id = seeded_case
    tracking.transition(case_id, "case_closed", outcome="other", note="unclear")
    r = tracking.set_outcome(case_id, outcome="deceased", note="confirmed by family")
    assert r["ok"] is True
    bt = queries.get_business_tracking(case_id)
    assert bt["outcome"] == "deceased" and bt["outcome_note"] == "confirmed by family"


def test_unknown_outcome_rejected_and_status_unchanged(seeded_case):
    from core import tracking
    case_id = seeded_case
    r = tracking.transition(case_id, "case_closed", outcome="not-a-code")
    assert r["ok"] is False
    assert queries.get_business_tracking(case_id)["status"] == "pending"


def test_outcome_note_is_length_capped(seeded_case):
    from core import tracking
    case_id = seeded_case
    tracking.set_outcome(case_id, outcome="other", note="x" * 500)
    assert len(queries.get_business_tracking(case_id)["outcome_note"]) == 200
