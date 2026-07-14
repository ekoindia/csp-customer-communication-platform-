"""
Approval workflow — the CSP's decision point before any message is queued
for sending.

After extraction/commit, every case has a generated message but is NOT
queued (see core/message_engine.py — generation never auto-queues). The CSP
then has two paths, freely mixable within the same batch, to build trust in
the software rather than force one rigid flow:

  AUTOMATIC  queue_and_dispatch(batch_id, chunk_size)
             Picks up to chunk_size non-sensitive, reachable, not-yet-queued
             cases (most urgent first), queues them, and starts sending
             immediately — today's fast path, unchanged in spirit.

  MANUAL     approve_case(case_id)        — approve ONE case (from its detail
                                             page), works for any case.
             approve_remaining(batch_id)  — bulk-approve every remaining
                                             non-sensitive case in one click,
                                             after the CSP has manually
                                             reviewed a few individually.
             Approving only QUEUES a case (marks it ready) — it does NOT
             start sending. The CSP still presses Send (queue_and_dispatch,
             or the plain dispatch Send button) to actually transmit.

Sensitive cases (is_sensitive=True, e.g. B>10000 — possible deceased owner)
are EXCLUDED from both the automatic path and bulk approve_remaining. They
always require an individual, explicit approve — never swept into a batch or
bulk action. This rule is enforced here, not just in the UI.
"""

from core import comm_runner
from core.message_engine import queue_for_dispatch, generate_single_message
from database import queries


def queue_and_dispatch(batch_id: str, chunk_size: int = None) -> dict:
    """
    'Send' button — serves BOTH the pure-automatic flow and the final step of
    a manual-review session, with one consistent rule: chunk_size is the total
    number of messages to go out in this round.

    Cases already queued from an earlier manual approval (approve_case /
    approve_remaining) are counted first and always included — approving a
    case is the CSP's explicit decision to send it, so it must not be silently
    skipped just because this button also happens to auto-queue more. Only
    the REMAINING room in chunk_size is filled with fresh, non-sensitive
    candidates (most urgent first). chunk_size=None sends everything: every
    already-queued case plus every remaining candidate.
    """
    already_queued = len(queries.list_dispatch_queue(batch_id))
    candidates = [c for c in queries.list_unqueued_cases(batch_id)
                  if not c["is_sensitive"] and (c["mobile"] or "").strip()]

    if already_queued == 0 and not candidates:
        return {"started": False,
                "reason": "No cases awaiting a send decision for this batch "
                         "(everything is already sent, or needs manual approval)."}

    if chunk_size and chunk_size > 0:
        room_left = max(0, chunk_size - already_queued)
        candidates = candidates[:room_left]
    # else: chunk_size is None/0 -> "send everything" -> queue every candidate too

    for case in candidates:
        queue_for_dispatch(case["case_id"])

    return comm_runner.start(batch_id, chunk_size=chunk_size)


def approve_case(case_id: str) -> dict:
    """
    Manual: queue ONE case for sending without starting the dispatch runner.
    Works for sensitive and non-sensitive cases alike — this is the only path
    a sensitive case can ever be queued through.

    Rejects clearly (does not silently no-op) if the case can't actually be
    queued: no mobile number, or it's already been queued/sent before.
    """
    case = queries.get_case(case_id)
    if not case:
        return {"ok": False, "reason": "case not found"}

    if not (case["mobile"] or "").strip():
        return {"ok": False, "reason": "This case has no mobile number and cannot be sent."}

    if queries.get_latest_comm_attempt(case_id):
        return {"ok": False, "reason": "This case has already been queued or sent."}

    if not queries.get_message(case_id):
        generate_single_message(case_id)

    queue_for_dispatch(case_id)
    return {"ok": True, "case_id": case_id}


def unapprove_case(case_id: str) -> dict:
    """Undo an approval: remove the case's pending (queued, not-yet-sent)
    attempt so it drops out of the dispatch queue. Refused if the case was
    already sent (there's nothing to take back at that point)."""
    attempt = queries.get_latest_comm_attempt(case_id)
    if not attempt:
        return {"ok": False, "reason": "This case is not approved."}
    if attempt["status"] != "pending":
        return {"ok": False, "reason": "This case has already been sent and cannot be un-approved."}
    queries.delete_pending_comm_attempt(case_id)
    return {"ok": True, "case_id": case_id}


def approve_remaining(batch_id: str, limit: int = None) -> dict:
    """
    Manual bulk step: queue the remaining NOT-yet-queued, non-sensitive cases
    in the batch (most urgent first), after the CSP has reviewed a few
    individually and decided to trust the rest. Does not start sending — the
    CSP still presses Send.

    `limit` mirrors the same custom-range number used for the automatic path
    (queue_and_dispatch) — the CSP picks a range ONCE, then chooses a mode;
    if some of that range were already approved individually before this
    call, the remaining urgency-ordered candidates naturally fill the rest of
    the range. None / omitted = approve everything left unqueued.

    Sensitive cases are never included here (see module docstring).
    """
    candidates = [c for c in queries.list_unqueued_cases(batch_id)
                  if not c["is_sensitive"] and (c["mobile"] or "").strip()]
    if limit and limit > 0:
        candidates = candidates[:limit]
    for case in candidates:
        queue_for_dispatch(case["case_id"])
    return {"approved": len(candidates)}
