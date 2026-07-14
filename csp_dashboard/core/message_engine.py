"""
Message Engine.
Owner of: messages table.

Reads:  customer_cases
Writes: messages

Rules:
- Pure template substitution — no LLM, no external API.
- Generating a message NEVER queues it for sending. Queuing (creating the
  first communication_attempts row) is a separate, explicit CSP decision —
  see core/approval.py — for every case, sensitive or not. This is what lets
  the CSP choose per batch: send automatically, or review-and-approve first.
- Idempotent: calling generate_batch_messages() twice is safe —
  INSERT OR REPLACE on messages.
"""

from core.template_engine import generate_messages
from database import queries


def generate_batch_messages(batch_id: str) -> dict:
    """
    Generate WA + SMS messages for every valid case in the batch.

    Returns:
        {generated: int, skipped_already_done: int, not_reachable: int, errors: [{case_id, reason}]}
    """
    cases = queries.list_cases_by_batch(batch_id)

    generated = 0
    skipped = 0
    errors = []
    not_reachable = 0

    for case in cases:
        case_id = case["case_id"]

        # No mobile number -> cannot message. Flag for manual CSP follow-up
        # (escalated, shown in the not-reachable list), never queue for dispatch.
        if not (case["mobile"] or "").strip():
            if not queries.get_latest_comm_attempt(case_id):
                queries.set_escalated(case_id, True)
                queries.insert_comm_attempt(case_id, "whatsapp", "escalated",
                                            error_detail="no mobile number")
                not_reachable += 1
            continue

        # Skip if message already exists
        existing = queries.get_message(case_id)
        if existing:
            skipped += 1
            continue

        try:
            messages = generate_messages(
                template_id=case["template_id"],
                customer_name=_first_name(case["name"]),
            )
        except Exception as e:
            errors.append({"case_id": case_id, "reason": str(e)})
            continue

        queries.insert_message(
            case_id=case_id,
            wa_message=messages["wa_message"],
            sms_message=messages["sms_message"],
            template_id=case["template_id"],
        )
        generated += 1

    return {"generated": generated, "skipped_already_done": skipped,
            "not_reachable": not_reachable, "errors": errors}


def generate_single_message(case_id: str) -> dict:
    """
    Generate the message for one case if it doesn't already have one.
    Returns the message dict or raises if case not found.
    """
    case = queries.get_case(case_id)
    if not case:
        raise ValueError(f"Case not found: {case_id}")

    messages = generate_messages(
        template_id=case["template_id"],
        customer_name=_first_name(case["name"]),
    )

    queries.insert_message(
        case_id=case_id,
        wa_message=messages["wa_message"],
        sms_message=messages["sms_message"],
        template_id=case["template_id"],
    )
    return messages


def queue_for_dispatch(case_id: str):
    """
    Create the initial 'pending' communication_attempt for a case, so the
    dispatch runner will pick it up. This is the ONE moment a case becomes
    eligible for sending — called only from core/approval.py, in response to
    an explicit CSP action (automatic batch queue, individual approve, or
    bulk approve-remaining). Never called automatically at message-generation
    time.
    """
    existing = queries.get_latest_comm_attempt(case_id)
    if not existing:
        queries.insert_comm_attempt(case_id, "whatsapp", "pending")


def _first_name(full_name: str) -> str:
    """Extract first name for greeting — 'RAMESH KUMAR' → 'Ramesh'."""
    parts = full_name.strip().split()
    if parts:
        return parts[0].capitalize()
    return full_name.capitalize()
