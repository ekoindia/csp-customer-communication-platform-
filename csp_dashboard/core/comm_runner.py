"""
Communication Layer — batch dispatch runner.
Owner of: communication_attempts table.

On a successful send it also performs the single AUTOMATIC business-tracking
transition pending -> customer_not_visited and stamps message_sent_at. This is
the one machine-driven step in the business state machine ("message sent, customer
hasn't come yet"); every later transition is manual and CSP-owned. is_escalated
is set here only when both channels fail.

Per-case logic (graceful fallback):
    WhatsApp  → success  → wa_attempted   (delivery/read confirmed later by webhook)
              → fail     → wa_failed      → try SMS
    SMS       → success  → sms_sent       (delivery confirmed later by webhook)
              → fail     → sms_failed     → escalate (CSP must visit)

Controls:
    - One runner at a time (threading.Lock).
    - Pause / stop checked BETWEEN messages, never mid-send.
    - Daily limit (config.WA_DAILY_LIMIT) and inter-message delay
      (config.WA_DELAY_SECONDS) enforced.
"""

import threading
import time
from datetime import datetime, timezone

import config
from core import dispatcher
from database import queries

# ── Runner state (in-memory, single process) ──────────────────────────────────

_lock = threading.Lock()
_state = {
    "running": False,
    "paused": False,
    "stop": False,
    "batch_id": None,
    "total": 0,          # size of THIS chunk (what CSP chose to send now)
    "done": 0,
    "wa_ok": 0,
    "sms_ok": 0,
    "failed": 0,
    "remaining_in_queue": 0,   # cases still pending AFTER this chunk finishes
    "started_at": None,
    "message": "",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_status() -> dict:
    """Snapshot of the runner state for the dashboard to poll."""
    return dict(_state)


def is_running() -> bool:
    return _state["running"]


def pause():
    _state["paused"] = True
    _state["message"] = "Paused by CSP"


def resume():
    _state["paused"] = False
    _state["message"] = "Resumed"


def stop():
    _state["stop"] = True
    _state["message"] = "Stopping after current message…"


def channels_ready() -> dict:
    """Is at least one send channel usable? WhatsApp must be connected, or
    MSG91 SMS must be configured. Returns {ok, whatsapp, sms, reason}."""
    import requests
    wa = False
    try:
        resp = requests.get(f"{config.WA_SERVER_URL}/status", timeout=3)
        wa = bool(resp.json().get("ready"))
    except requests.RequestException:
        wa = False
    sms = bool(config.MSG91_AUTH_KEY)
    if wa or sms:
        reason = None
    elif not wa:
        reason = ("WhatsApp is not connected. Open Settings and scan the QR code "
                  "with the sending phone, then try again.")
    else:
        reason = "No send channel is available."
    return {"ok": wa or sms, "whatsapp": wa, "sms": sms, "reason": reason}


def start(batch_id: str, chunk_size: int = None) -> dict:
    """
    Launch dispatch for a batch in a background thread.

    chunk_size: how many messages to send in THIS run (CSP-chosen custom
    range). None / >= queue length sends everything remaining. Urgency
    ordering (from list_dispatch_queue) is preserved within the chunk — the
    most urgent pending cases always go first, whatever size is picked.

    After the chunk finishes, the run stops on its own (does not continue
    into the rest of the queue). The CSP reviews the results, then explicitly
    starts the next chunk — this is the mandatory decision point.

    Returns immediately with {started: bool, reason: str}.
    """
    ready = channels_ready()
    if not ready["ok"]:
        return {"started": False, "reason": ready["reason"]}

    if not _lock.acquire(blocking=False):
        return {"started": False, "reason": "A dispatch run is already in progress"}

    full_queue = queries.list_dispatch_queue(batch_id)
    if not full_queue:
        _lock.release()
        return {"started": False, "reason": "No cases pending dispatch for this batch"}

    n = len(full_queue) if not chunk_size or chunk_size <= 0 else min(chunk_size, len(full_queue))
    chunk = full_queue[:n]
    remaining_after = len(full_queue) - n

    _reset_state(batch_id, n, remaining_after)
    thread = threading.Thread(target=_run, args=(batch_id, chunk), daemon=True)
    thread.start()
    return {"started": True, "reason": f"Dispatching {n} messages "
                                       f"({remaining_after} will remain in the queue)"}


def _reset_state(batch_id: str, total: int, remaining_in_queue: int):
    _state.update({
        "running": True, "paused": False, "stop": False,
        "batch_id": batch_id, "total": total, "done": 0,
        "wa_ok": 0, "sms_ok": 0, "failed": 0,
        "remaining_in_queue": remaining_in_queue,
        "started_at": _now(), "message": "Starting…",
    })


def _run(batch_id: str, chunk: list):
    try:
        for row in chunk:
            # ── control checks (between messages only) ──
            if _state["stop"]:
                _state["message"] = "Stopped by CSP"
                break
            while _state["paused"] and not _state["stop"]:
                time.sleep(1)
            if _state["stop"]:
                break

            # ── daily limit ──
            if queries.count_sent_today() >= config.WA_DAILY_LIMIT:
                _state["message"] = f"Daily limit ({config.WA_DAILY_LIMIT}) reached. Stopping."
                break

            _dispatch_one(row)
            _state["done"] += 1
            _state["message"] = f"Sent {_state['done']} / {_state['total']}"

            # ── inter-message delay (skip after last) ──
            # Sleep in 1-second slices so Pause and Stop take effect within a
            # second instead of only after the full gap — otherwise the buttons
            # look dead for up to WA_DELAY_SECONDS. A pause here holds the delay
            # open (doesn't count down) until the CSP resumes.
            if _state["done"] < _state["total"]:
                waited = 0
                while waited < config.WA_DELAY_SECONDS:
                    if _state["stop"]:
                        break
                    if _state["paused"]:
                        time.sleep(1)      # hold without advancing the delay
                        continue
                    time.sleep(1)
                    waited += 1

    finally:
        _state["running"] = False
        if not _state["message"].startswith(("Stopped", "Paused", "Daily")):
            if _state["remaining_in_queue"] > 0:
                _state["message"] = (f"Batch done: {_state['done']} sent. "
                                     f"{_state['remaining_in_queue']} still in queue — "
                                     f"choose a range and send the next batch.")
            else:
                _state["message"] = f"Done. {_state['done']} processed. Queue is empty."
        _lock.release()


def _dispatch_one(row):
    """Send a single case: WhatsApp first, SMS fallback, escalate on total failure."""
    case_id = row["case_id"]
    mobile = row["mobile"]
    attempt_id = row["attempt_id"]

    # ── 1. WhatsApp ──
    wa = dispatcher.send_whatsapp(mobile, row["wa_message"])
    if wa["success"]:
        queries.update_comm_status(attempt_id, "wa_attempted")
        _stamp_sent(attempt_id, "wa_attempted")
        if wa["message_id"]:
            queries.set_provider_message_id(attempt_id, wa["message_id"])
        queries.update_business_status(case_id, "customer_not_visited",
                                       message_sent_at=_now())
        _state["wa_ok"] += 1
        return

    # WhatsApp failed → record and fall through to SMS
    queries.update_comm_status(attempt_id, "wa_failed", error_detail=wa["error"])

    # ── 2. SMS fallback ──
    sms = dispatcher.send_sms(mobile, row["sms_message"])
    if sms["success"]:
        sms_id = queries.insert_comm_attempt(case_id, "sms", "sms_sent", sent_at=_now())
        if sms["message_id"]:
            queries.set_provider_message_id(sms_id, sms["message_id"])
        queries.update_business_status(case_id, "customer_not_visited",
                                       message_sent_at=_now())
        _state["sms_ok"] += 1
        return

    # ── 3. Both failed → escalate ──
    queries.insert_comm_attempt(case_id, "sms", "sms_failed", error_detail=sms["error"])
    queries.insert_comm_attempt(case_id, "whatsapp", "escalated",
                                error_detail="WA + SMS failed")
    queries.set_escalated(case_id, True)
    _state["failed"] += 1


def _stamp_sent(attempt_id: int, status: str):
    """Set sent_at on an existing attempt (update_comm_status doesn't touch sent_at)."""
    from database.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "UPDATE communication_attempts SET sent_at=? WHERE id=?",
            (_now(), attempt_id),
        )
        conn.commit()
