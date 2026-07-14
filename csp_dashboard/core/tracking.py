"""
Tracking & Case Management — business-side tracking.
Owner of: business_tracking table.

Two independent tracking systems exist (never mixed):
  1. Communication tracking — automatic, updated by webhooks (core/webhooks.py).
  2. Business case tracking  — manual, driven by CSP clicks (THIS module).

Business status state machine:

    pending
       │  (automatic — set by comm_runner when a message is sent)
       ▼
    customer_not_visited
       │  (CSP clicks "Visited")
       ▼
    customer_visited_in_progress
       │  (CSP clicks "Done")
       ▼
    process_completed
       │  (CSP clicks "Close")
       ▼
    case_closed   ← terminal

Only transitions in _ALLOWED are permitted. Any other move is rejected,
so the dashboard can never push a case into an illegal state.
"""

from datetime import datetime, timezone
from database import queries

# from_status → set of allowed next statuses.
# A "pending" case may jump straight to "customer_visited_in_progress": the CSP
# can mark someone as visited even if no message was ever sent (e.g. WhatsApp not
# yet linked, or an escalated/unreachable case where the customer walked in
# anyway). Without this the dashboard "Visited" button silently failed on every
# still-pending case.
# The dashboard Action column offers just two buttons — "Visited" and "Close"
# (issue resolved). So "Close" (case_closed) is reachable from any active state,
# not only from process_completed. process_completed stays a valid state (older
# data / other paths) but is no longer a required step in the UI flow.
_ALLOWED = {
    "pending": {"customer_not_visited", "customer_visited_in_progress", "case_closed"},
    "customer_not_visited": {"customer_visited_in_progress", "case_closed"},
    "customer_visited_in_progress": {"process_completed", "case_closed"},
    "process_completed": {"case_closed"},
    "case_closed": set(),  # terminal
}

ALL_STATUSES = set(_ALLOWED.keys())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def can_transition(from_status: str, to_status: str) -> bool:
    return to_status in _ALLOWED.get(from_status, set())


def transition(case_id: str, to_status: str) -> dict:
    """
    Apply a validated business-status transition.
    Returns {ok: bool, from: str|None, to: str, reason: str|None}.
    """
    current = queries.get_business_tracking(case_id)
    if not current:
        return {"ok": False, "from": None, "to": to_status, "reason": "case not found"}

    from_status = current["status"]

    if to_status not in ALL_STATUSES:
        return {"ok": False, "from": from_status, "to": to_status, "reason": "unknown status"}

    if from_status == to_status:
        return {"ok": False, "from": from_status, "to": to_status, "reason": "no change"}

    if not can_transition(from_status, to_status):
        return {
            "ok": False, "from": from_status, "to": to_status,
            "reason": f"illegal transition {from_status} -> {to_status}",
        }

    now = _now()
    visited_at = now if to_status == "customer_visited_in_progress" else None
    closed_at = now if to_status == "case_closed" else None

    queries.update_business_status(
        case_id, to_status, visited_at=visited_at, closed_at=closed_at
    )
    if to_status == "case_closed":
        # RBI/DPDP: don't retain customer PII in local storage beyond
        # operational need — case_closed is terminal, so no further sending
        # or editing will ever touch this case again.
        queries.purge_case_pii(case_id)
    return {"ok": True, "from": from_status, "to": to_status, "reason": None}


def next_action(business_status: str) -> dict | None:
    """
    The single CSP action available from a given status.
    Drives the per-row action button label in the dashboard.
    Returns {label, to} or None if terminal / no action.
    """
    mapping = {
        "pending": None,  # waiting for message dispatch
        "customer_not_visited": {"label": "Visited", "to": "customer_visited_in_progress"},
        "customer_visited_in_progress": {"label": "Done", "to": "process_completed"},
        "process_completed": {"label": "Close", "to": "case_closed"},
        "case_closed": None,
    }
    return mapping.get(business_status)
