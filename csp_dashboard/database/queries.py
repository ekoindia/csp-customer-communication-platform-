"""
All database query functions.
Each function is annotated with which module OWNS it (can write).
Read access is unrestricted across modules.
"""

from datetime import datetime, timezone
from typing import Optional
import sqlite3
from database.db import get_connection
from core import crypto


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# customer_cases columns stored ENCRYPTED at rest (see core/crypto.py). Any
# query result carrying these keys must go through _decrypt_row before it
# leaves this module.
_PII_FIELDS = ("name", "mobile", "account_number", "father_name", "address")


def _decrypt_row(row) -> Optional[dict]:
    """sqlite3.Row (or dict) -> plain dict with PII columns decrypted. Only
    touches keys actually present, so it's safe on both full-row and
    partial-column SELECTs. Non-PII keys pass through unchanged."""
    if row is None:
        return None
    d = dict(row)
    for f in _PII_FIELDS:
        if f in d:
            d[f] = crypto.decrypt_field(d[f])
    return d


# ============================================================
# documents  (owner: Document Processing)
# ============================================================

def insert_document(batch_id: str, campaign_id: str, original_name: str,
                    file_format: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO documents
               (batch_id, campaign_id, original_name, file_format, uploaded_at, status)
               VALUES (?, ?, ?, ?, ?, 'uploaded')""",
            (batch_id, campaign_id, original_name, file_format, _now()),
        )
        conn.commit()
        return cur.lastrowid


def update_document_counts(batch_id: str, total: int, valid: int, invalid: int):
    with get_connection() as conn:
        conn.execute(
            """UPDATE documents SET total_rows=?, valid_rows=?, invalid_rows=?,
               status='done', processed_at=? WHERE batch_id=?""",
            (total, valid, invalid, _now(), batch_id),
        )
        conn.commit()


def update_document_status(batch_id: str, status: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET status=? WHERE batch_id=?",
            (status, batch_id),
        )
        conn.commit()


def get_document(batch_id: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM documents WHERE batch_id=?", (batch_id,)
        ).fetchone()


def list_documents() -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM documents ORDER BY uploaded_at DESC"
        ).fetchall()


# ============================================================
# customer_cases  (owner: Document Processing)
# ============================================================

def insert_customer_case(case_id: str, batch_id: str, campaign_id: str,
                         account_number: str, name: str, mobile: str,
                         father_name: Optional[str], balance_band: str,
                         village: Optional[str], taluka: Optional[str],
                         address: Optional[str], band_label: str, tone: str,
                         template_id: str, is_sensitive: bool) -> int:
    """Identifying fields are ENCRYPTED before storage (core/crypto.py) — see
    the schema comment on customer_cases. account_number_hash is a separate,
    deterministic one-way index computed from the PLAIN account_number, used
    only for exact-match dedup (account_exists)."""
    acct_hash = crypto.account_hash(account_number)
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO customer_cases
               (case_id, batch_id, campaign_id, account_number, account_number_hash,
                name, mobile, father_name, balance_band, village, taluka, address,
                band_label, tone, template_id, is_sensitive, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (case_id, batch_id, campaign_id, crypto.encrypt_field(account_number),
             acct_hash, crypto.encrypt_field(name), crypto.encrypt_field(mobile),
             crypto.encrypt_field(father_name), balance_band, village, taluka,
             crypto.encrypt_field(address),
             band_label, tone, template_id, int(is_sensitive), _now()),
        )
        conn.commit()
        return cur.lastrowid


def account_exists(campaign_id: str, account_number: str) -> bool:
    """True if a case with this (campaign, account_number) already exists.
    The account number is the money key: one account = one case = commission
    counted once, so re-uploading the same page / the same account in a later
    bank list never creates a duplicate. Normalise before calling.

    Looked up via account_number_hash (a deterministic one-way index) rather
    than the encrypted account_number column: Fernet encryption is
    non-deterministic (a fresh nonce every call), so two encryptions of the
    same plain value never match with a SQL `=` — the hash is what makes
    exact-match dedup possible while account_number itself stays encrypted."""
    acct_hash = crypto.account_hash(account_number)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM customer_cases WHERE campaign_id=? AND account_number_hash=? LIMIT 1",
            (campaign_id, acct_hash),
        ).fetchone()
        return row is not None


def get_case(case_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM customer_cases WHERE case_id=?", (case_id,)
        ).fetchone()
    return _decrypt_row(row)


def update_case_fields(case_id: str, name: str, mobile: str,
                       father_name: Optional[str], balance_band: str,
                       village: Optional[str], taluka: Optional[str],
                       address: Optional[str], band_label: str, tone: str,
                       template_id: str, is_sensitive: bool) -> None:
    """Update the CSP-editable customer fields on a case (used by the case
    detail edit form). Classification-derived fields (band_label, tone,
    template_id, is_sensitive) are recomputed by the caller and passed in.
    Identifying fields are encrypted before storage (account_number is not
    edited here — it's read-only on the case detail form)."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE customer_cases SET
                 name=?, mobile=?, father_name=?, balance_band=?,
                 village=?, taluka=?, address=?, band_label=?, tone=?,
                 template_id=?, is_sensitive=?
               WHERE case_id=?""",
            (crypto.encrypt_field(name), crypto.encrypt_field(mobile),
             crypto.encrypt_field(father_name), balance_band, village, taluka,
             crypto.encrypt_field(address),
             band_label, tone, template_id, int(is_sensitive), case_id),
        )
        conn.commit()


def purge_case_pii(case_id: str) -> None:
    """Irreversibly clear a case's identifying fields once its business-tracking
    lifecycle reaches the terminal 'case_closed' state (RBI/DPDP: don't retain
    customer PII in local storage beyond operational need). account_number_hash
    is KEPT — it's a one-way blind index used only for future dedup, not
    reversible to the account number, so retaining it doesn't reintroduce PII.
    village/taluka are kept too — not identifying on their own, still useful
    for reporting.

    The rendered message text is ALSO cleared: it embeds the customer's first
    name ("Namaste Ramesh ji…"), which is PII, so leaving it after closure would
    keep a cleartext name in the DB and defeat the purge. wa_message/sms_message
    are NOT NULL, so they're blanked to ''."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE customer_cases SET
                 name=NULL, mobile=NULL, account_number=NULL,
                 father_name=NULL, address=NULL, pii_purged_at=?
               WHERE case_id=?""",
            (_now(), case_id),
        )
        conn.execute(
            "UPDATE messages SET wa_message='', sms_message='' WHERE case_id=?",
            (case_id,),
        )
        conn.commit()


def purge_closed_unpurged_pii() -> int:
    """Reconcile sweep: purge any case that is already 'case_closed' but whose
    PII was never nulled. Closing a case (core.tracking.transition) updates the
    status and purges PII in two separate commits; if the process crashed
    between them, a closed case could keep its PII. This sweep — run at startup —
    closes that window by purging every such straggler. Returns how many it
    purged (0 in the normal case). Safe/idempotent: already-purged closed cases
    (pii_purged_at set) are skipped."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT cc.case_id FROM customer_cases cc
               JOIN business_tracking bt ON bt.case_id = cc.case_id
               WHERE bt.status='case_closed' AND cc.pii_purged_at IS NULL"""
        ).fetchall()
        for r in rows:
            conn.execute(
                """UPDATE customer_cases SET
                     name=NULL, mobile=NULL, account_number=NULL,
                     father_name=NULL, address=NULL, pii_purged_at=?
                   WHERE case_id=?""",
                (_now(), r["case_id"]),
            )
            conn.execute(
                "UPDATE messages SET wa_message='', sms_message='' WHERE case_id=?",
                (r["case_id"],),
            )
        conn.commit()
        return len(rows)


def delete_batch(batch_id: str) -> None:
    """Delete a whole batch and ALL its data — cases, messages, communication
    attempts, business tracking, and the document row. Used by the upload-history
    delete action (frees disk / RAM; DPDP hygiene). Reference/config data and
    other batches are untouched."""
    with get_connection() as conn:
        sub = "(SELECT case_id FROM customer_cases WHERE batch_id=?)"
        for tbl in ("communication_attempts", "business_tracking", "messages"):
            conn.execute(f"DELETE FROM {tbl} WHERE case_id IN {sub}", (batch_id,))
        conn.execute("DELETE FROM customer_cases WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM documents WHERE batch_id=?", (batch_id,))
        conn.commit()


def list_cases_by_batch(batch_id: str) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM customer_cases WHERE batch_id=? ORDER BY id",
            (batch_id,),
        ).fetchall()
    return [_decrypt_row(r) for r in rows]


def list_cases_with_tracking(batch_id: str) -> list:
    """Join customer_cases + messages + communication_attempts + business_tracking."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                cc.*,
                m.wa_message, m.sms_message,
                ca.channel, ca.status AS comm_status, ca.sent_at,
                bt.status AS business_status, bt.is_escalated,
                bt.visited_at, bt.closed_at
            FROM customer_cases cc
            LEFT JOIN messages m ON m.case_id = cc.case_id
            LEFT JOIN (
                SELECT case_id, channel, status, sent_at
                FROM communication_attempts
                WHERE id IN (
                    SELECT MAX(id) FROM communication_attempts GROUP BY case_id
                )
            ) ca ON ca.case_id = cc.case_id
            LEFT JOIN business_tracking bt ON bt.case_id = cc.case_id
            WHERE cc.batch_id = ?
            ORDER BY cc.id
            """,
            (batch_id,),
        ).fetchall()
    return [_decrypt_row(r) for r in rows]


# ============================================================
# messages  (owner: Message Engine)
# ============================================================

def insert_message(case_id: str, wa_message: str, sms_message: str,
                   template_id: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT OR REPLACE INTO messages
               (case_id, wa_message, sms_message, template_id, generated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (case_id, wa_message, sms_message, template_id, _now()),
        )
        conn.commit()
        return cur.lastrowid


def get_message(case_id: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM messages WHERE case_id=?", (case_id,)
        ).fetchone()


def list_unqueued_cases(batch_id: str) -> list:
    """
    Cases with a generated message that have NEVER been queued for sending
    (no communication_attempts row at all yet) — i.e. cases still awaiting a
    CSP decision: send automatically, or review-and-approve manually.

    Includes sensitive cases too (callers that must exclude them — automatic
    batch sends, bulk "approve remaining" — filter on is_sensitive themselves;
    the case-detail "approve one case" action deliberately works for anyone).

    Ordered by urgency first (matches list_dispatch_queue's ordering), so
    whichever N a CSP picks for a custom range are the most urgent first.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT cc.case_id, cc.name, cc.mobile, cc.is_sensitive, cc.tone,
                   cc.band_label, m.wa_message, m.sms_message
            FROM customer_cases cc
            JOIN messages m ON m.case_id = cc.case_id
            WHERE cc.batch_id = ?
              AND cc.case_id NOT IN (
                  SELECT DISTINCT case_id FROM communication_attempts
              )
            ORDER BY CASE cc.tone WHEN 'urgent' THEN 0 ELSE 1 END, cc.id
            """,
            (batch_id,),
        ).fetchall()
    return [_decrypt_row(r) for r in rows]


# ============================================================
# communication_attempts  (owner: Communication Layer)
# ============================================================

def insert_comm_attempt(case_id: str, channel: str, status: str,
                        sent_at: Optional[str] = None,
                        error_detail: Optional[str] = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO communication_attempts
               (case_id, channel, status, sent_at, updated_at, error_detail)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (case_id, channel, status, sent_at, _now(), error_detail),
        )
        conn.commit()
        return cur.lastrowid


def update_comm_status(attempt_id: int, status: str,
                       error_detail: Optional[str] = None):
    with get_connection() as conn:
        conn.execute(
            "UPDATE communication_attempts SET status=?, updated_at=?, error_detail=? WHERE id=?",
            (status, _now(), error_detail, attempt_id),
        )
        conn.commit()


def get_latest_comm_attempt(case_id: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM communication_attempts
               WHERE case_id=? ORDER BY id DESC LIMIT 1""",
            (case_id,),
        ).fetchone()


def delete_pending_comm_attempt(case_id: str) -> int:
    """Remove a case's 'pending' (queued-but-not-sent) communication attempt so
    it is no longer in the dispatch queue — the 'undo approve' action. Only
    touches a pending row; a sent/delivered/failed attempt is left intact.
    Returns the number of rows deleted."""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM communication_attempts WHERE case_id=? AND status='pending'",
            (case_id,),
        )
        conn.commit()
        return cur.rowcount


def get_comm_attempt_by_id(attempt_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM communication_attempts WHERE id=?", (attempt_id,)
        ).fetchone()


def list_comm_attempts_for_case(case_id: str) -> list:
    """Full attempt history for one case (case-detail page), oldest first."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM communication_attempts
               WHERE case_id=? ORDER BY id ASC""",
            (case_id,),
        ).fetchall()


def set_provider_message_id(attempt_id: int, provider_message_id: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE communication_attempts SET provider_message_id=?, updated_at=? WHERE id=?",
            (provider_message_id, _now(), attempt_id),
        )
        conn.commit()


def get_attempt_by_provider_id(provider_message_id: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM communication_attempts WHERE provider_message_id=? "
            "ORDER BY id DESC LIMIT 1",
            (provider_message_id,),
        ).fetchone()


def update_status_by_provider_id(provider_message_id: str, status: str,
                                 error_detail: Optional[str] = None) -> bool:
    """Update a comm attempt's status using the provider's message id.
    Returns True if a row was updated."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE communication_attempts SET status=?, updated_at=?, error_detail=? "
            "WHERE provider_message_id=?",
            (status, _now(), error_detail, provider_message_id),
        )
        conn.commit()
        return cur.rowcount > 0


def list_dispatch_queue(batch_id: str) -> list:
    """
    Cases ready to be sent: their LATEST communication attempt is 'pending'.
    Joins in the generated message text.

    Ordered by URGENCY first (urgent bands go out before normal ones), then by
    case id. The rate limit + delay below this still spread the batch over time
    so the sending number is not flagged for spam.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT cc.case_id, cc.mobile, cc.name, cc.is_sensitive, cc.tone,
                   cc.band_label,
                   m.wa_message, m.sms_message,
                   ca.id AS attempt_id
            FROM customer_cases cc
            JOIN messages m ON m.case_id = cc.case_id
            JOIN communication_attempts ca ON ca.case_id = cc.case_id
            WHERE cc.batch_id = ?
              AND ca.id IN (
                  SELECT MAX(id) FROM communication_attempts GROUP BY case_id
              )
              AND ca.status = 'pending'
            ORDER BY CASE cc.tone WHEN 'urgent' THEN 0 ELSE 1 END, cc.id
            """,
            (batch_id,),
        ).fetchall()
    return [_decrypt_row(r) for r in rows]


def count_sent_today() -> int:
    """WhatsApp + SMS attempts that left the system today (for daily limit)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_connection() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM communication_attempts
               WHERE sent_at LIKE ?
                 AND status IN ('wa_attempted','wa_delivered','wa_read',
                                'sms_sent','sms_delivered')""",
            (f"{today}%",),
        ).fetchone()[0]


# ============================================================
# business_tracking  (owner: Tracking / Presentation)
# ============================================================

def init_business_tracking(case_id: str):
    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO business_tracking
               (case_id, status, is_escalated, updated_at)
               VALUES (?, 'pending', 0, ?)""",
            (case_id, _now()),
        )
        conn.commit()


def update_business_status(case_id: str, status: str,
                           visited_at: Optional[str] = None,
                           closed_at: Optional[str] = None,
                           message_sent_at: Optional[str] = None):
    with get_connection() as conn:
        conn.execute(
            """UPDATE business_tracking
               SET status=?, updated_at=?,
                   visited_at=COALESCE(?, visited_at),
                   closed_at=COALESCE(?, closed_at),
                   message_sent_at=COALESCE(?, message_sent_at)
               WHERE case_id=?""",
            (status, _now(), visited_at, closed_at, message_sent_at, case_id),
        )
        conn.commit()


def set_escalated(case_id: str, is_escalated: bool):
    with get_connection() as conn:
        conn.execute(
            "UPDATE business_tracking SET is_escalated=?, updated_at=? WHERE case_id=?",
            (int(is_escalated), _now(), case_id),
        )
        conn.commit()


def get_business_tracking(case_id: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM business_tracking WHERE case_id=?", (case_id,)
        ).fetchone()


# ============================================================
# Dashboard summary queries  (owner: Presentation — read-only)
# ============================================================

def batch_overview(batch_id: str) -> dict:
    with get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM customer_cases WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]

        reached = conn.execute(
            """SELECT COUNT(DISTINCT cc.case_id)
               FROM customer_cases cc
               JOIN communication_attempts ca ON ca.case_id = cc.case_id
               WHERE cc.batch_id=?
                 AND ca.status IN ('wa_delivered','wa_read','sms_delivered')""",
            (batch_id,),
        ).fetchone()[0]

        failed = conn.execute(
            """SELECT COUNT(DISTINCT cc.case_id)
               FROM customer_cases cc
               WHERE cc.batch_id=?
                 AND cc.case_id IN (
                     SELECT case_id FROM communication_attempts
                     WHERE status='sms_failed'
                 )""",
            (batch_id,),
        ).fetchone()[0]

        # A genuine visit always has a visited_at timestamp (set on the
        # customer_visited_in_progress transition). Cases closed without a
        # visit (e.g. a skipped sensitive case) have visited_at = NULL and
        # must not be counted here.
        visited = conn.execute(
            """SELECT COUNT(*) FROM business_tracking bt
               JOIN customer_cases cc ON cc.case_id = bt.case_id
               WHERE cc.batch_id=? AND bt.visited_at IS NOT NULL""",
            (batch_id,),
        ).fetchone()[0]

        pending = conn.execute(
            """SELECT COUNT(*) FROM business_tracking bt
               JOIN customer_cases cc ON cc.case_id = bt.case_id
               WHERE cc.batch_id=? AND bt.status='customer_not_visited'""",
            (batch_id,),
        ).fetchone()[0]

        wa_delivered = conn.execute(
            """SELECT COUNT(DISTINCT cc.case_id)
               FROM customer_cases cc
               JOIN communication_attempts ca ON ca.case_id = cc.case_id
               WHERE cc.batch_id=? AND ca.channel='whatsapp'
                 AND ca.status IN ('wa_delivered','wa_read')""",
            (batch_id,),
        ).fetchone()[0]

        sms_delivered = conn.execute(
            """SELECT COUNT(DISTINCT cc.case_id)
               FROM customer_cases cc
               JOIN communication_attempts ca ON ca.case_id = cc.case_id
               WHERE cc.batch_id=? AND ca.channel='sms'
                 AND ca.status='sms_delivered'""",
            (batch_id,),
        ).fetchone()[0]

        # Per-channel, per-status breakdown so the dashboard can show WhatsApp
        # and SMS separately, and split delivered vs read. Statuses overwrite in
        # place per attempt (webhook advances wa_attempted -> wa_delivered ->
        # wa_read), so a read message sits in wa_read, not wa_delivered.
        rows = conn.execute(
            """SELECT ca.channel, ca.status, COUNT(DISTINCT ca.case_id) AS n
               FROM communication_attempts ca
               JOIN customer_cases cc ON cc.case_id = ca.case_id
               WHERE cc.batch_id=?
               GROUP BY ca.channel, ca.status""",
            (batch_id,),
        ).fetchall()
    cnt = {(r["channel"], r["status"]): r["n"] for r in rows}

    wa_attempted = cnt.get(("whatsapp", "wa_attempted"), 0)
    wa_deliv_only = cnt.get(("whatsapp", "wa_delivered"), 0)
    wa_read = cnt.get(("whatsapp", "wa_read"), 0)
    wa_failed = cnt.get(("whatsapp", "wa_failed"), 0)
    sms_sent = cnt.get(("sms", "sms_sent"), 0)
    sms_deliv = cnt.get(("sms", "sms_delivered"), 0)
    sms_failed = cnt.get(("sms", "sms_failed"), 0)

    return {
        "total": total,
        "reached": reached,
        "failed": failed,
        "visited": visited,
        "pending": pending,
        "wa_delivered": wa_delivered,   # delivered OR read (kept for existing UI)
        "sms_delivered": sms_delivered,
        "reach_rate": round(reached / total * 100, 1) if total else 0,
        # ── per-channel detail (WhatsApp) ──
        "wa_sent": wa_attempted + wa_deliv_only + wa_read,  # left our server
        "wa_delivered_only": wa_deliv_only,                 # delivered, not read
        "wa_read": wa_read,                                 # read (blue ticks)
        "wa_failed": wa_failed,
        # ── per-channel detail (SMS) ──
        "sms_sent": sms_sent,
        "sms_delivered_only": sms_deliv,
        "sms_failed": sms_failed,
    }


def list_escalations(batch_id: str) -> list:
    """Cases that failed both channels and need a manual CSP visit."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT cc.case_id, cc.name, cc.mobile, cc.village, cc.taluka,
                      cc.band_label, bt.status AS business_status
               FROM customer_cases cc
               JOIN business_tracking bt ON bt.case_id = cc.case_id
               WHERE cc.batch_id=? AND bt.is_escalated=1
               ORDER BY cc.id""",
            (batch_id,),
        ).fetchall()
    return [_decrypt_row(r) for r in rows]


def list_visit_log(batch_id: str) -> list:
    """Cases the customer has visited (in progress, completed, or closed)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT cc.case_id, cc.name, cc.village, cc.band_label,
                      bt.status AS business_status,
                      bt.visited_at, bt.closed_at
               FROM customer_cases cc
               JOIN business_tracking bt ON bt.case_id = cc.case_id
               WHERE cc.batch_id=?
                 AND bt.visited_at IS NOT NULL
               ORDER BY bt.visited_at DESC""",
            (batch_id,),
        ).fetchall()
    return [_decrypt_row(r) for r in rows]


def business_status_breakdown(batch_id: str) -> dict:
    """Count of cases in each business status, for reports."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT bt.status, COUNT(*) AS n
               FROM customer_cases cc
               JOIN business_tracking bt ON bt.case_id = cc.case_id
               WHERE cc.batch_id=?
               GROUP BY bt.status""",
            (batch_id,),
        ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def category_breakdown(batch_id: str) -> list:
    """Per-band counts: total vs reached (delivered/read). For category bars."""
    with get_connection() as conn:
        return conn.execute(
            """SELECT cc.band_label,
                      COUNT(*) AS total,
                      SUM(CASE WHEN ca.status IN
                          ('wa_delivered','wa_read','sms_delivered')
                          THEN 1 ELSE 0 END) AS reached
               FROM customer_cases cc
               LEFT JOIN (
                   SELECT case_id, status FROM communication_attempts
                   WHERE id IN (SELECT MAX(id) FROM communication_attempts GROUP BY case_id)
               ) ca ON ca.case_id = cc.case_id
               WHERE cc.batch_id=?
               GROUP BY cc.band_label
               ORDER BY cc.band_label""",
            (batch_id,),
        ).fetchall()


def list_sensitive_pending(batch_id: str) -> list:
    """Cases flagged is_sensitive=1 (currently no band is sensitive by default —
    see campaigns/inoperative/config.json) that haven't been approved/skipped."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT cc.case_id, cc.name, cc.village, cc.mobile
               FROM customer_cases cc
               WHERE cc.batch_id=? AND cc.is_sensitive=1
                 AND cc.case_id NOT IN (
                     SELECT DISTINCT case_id FROM communication_attempts
                 )""",
            (batch_id,),
        ).fetchall()
    return [_decrypt_row(r) for r in rows]


# ============================================================
# configuration  (owner: Presentation — generic install-wide flags)
# ============================================================

def get_config_value(key: str) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM configuration WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_config_value(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO configuration (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, _now()),
        )
        conn.commit()


# ============================================================
# users / audit_logs  (owner: Security)
# ============================================================

def get_branch() -> Optional[sqlite3.Row]:
    """The single CSP branch row (CSP name / phone / address)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM branches ORDER BY id LIMIT 1"
        ).fetchone()


def update_branch(csp_name: str, csp_phone: str, csp_address: str,
                  branch_code: Optional[str] = None):
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM branches ORDER BY id LIMIT 1").fetchone()
        if row:
            conn.execute(
                "UPDATE branches SET csp_name=?, csp_phone=?, csp_address=?, branch_code=? WHERE id=?",
                (csp_name, csp_phone, csp_address, branch_code, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO branches (csp_name, csp_phone, csp_address, branch_code) VALUES (?,?,?,?)",
                (csp_name, csp_phone, csp_address, branch_code),
            )
        conn.commit()


def get_user(login_id: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE login_id=?", (login_id,)
        ).fetchone()


def create_operator(login_id: str, password_hash: str) -> None:
    """Set the single CSP operator's credentials during first-run onboarding.
    Single-operator desktop app: reuse the existing user row if one is present
    (keeps the id stable for audit_logs FK), otherwise insert a fresh one."""
    with get_connection() as conn:
        row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if row:
            conn.execute(
                "UPDATE users SET login_id=?, password=?, role='csp_operator' WHERE id=?",
                (login_id, password_hash, row["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO users (login_id, password, role, created_at)
                   VALUES (?, ?, 'csp_operator', ?)""",
                (login_id, password_hash, _now()),
            )
        conn.commit()


def update_last_login(login_id: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET last_login=? WHERE login_id=?",
            (_now(), login_id),
        )
        conn.commit()


def set_user_password(login_id: str, password_hash: str) -> None:
    """Replace a user's stored password hash (used by the first-run credential
    generator and the Settings 'change password' action)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET password=? WHERE login_id=?",
            (password_hash, login_id),
        )
        conn.commit()


def insert_audit_log(user_id: int, action: str, detail: Optional[str] = None):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_logs (user_id, action, detail, created_at) VALUES (?,?,?,?)",
            (user_id, action, detail, _now()),
        )
        conn.commit()


def list_audit_logs(limit: int = 200) -> list:
    with get_connection() as conn:
        return conn.execute(
            """SELECT a.created_at, a.action, a.detail, u.login_id
               FROM audit_logs a
               LEFT JOIN users u ON u.id = a.user_id
               ORDER BY a.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
