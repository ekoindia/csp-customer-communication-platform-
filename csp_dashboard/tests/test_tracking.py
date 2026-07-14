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
