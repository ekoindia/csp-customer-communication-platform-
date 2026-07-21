import os
import re
import subprocess
import uuid

from flask import (Blueprint, render_template, redirect, url_for,
                   request, session, flash, jsonify)

import config
from database.queries import (get_user, update_last_login, insert_audit_log,
                               list_documents, get_document,
                               list_cases_with_tracking, batch_overview,
                               list_sensitive_pending, update_business_status,
                               list_escalations, list_visit_log,
                               business_status_breakdown, category_breakdown,
                               list_audit_logs, get_config_value, set_config_value,
                               create_operator, update_branch, get_branch)

dashboard_bp = Blueprint("dashboard", __name__)

ALLOWED_EXTENSIONS = {
    ".xlsx", ".xls", ".csv", ".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".bmp",
    ".cspx",   # encrypted Excel from the CSP phone scanner app (decrypted at ingress)
}


def _login_required():
    if not session.get("logged_in"):
        return redirect(url_for("dashboard.login"))
    return None


_ADMIN_SETUP_FLAG = "admin_setup_prompted"
_DEMO_API_KEY = "demo-key-CSP001"


def _admin_setup_needed() -> bool:
    """True until the CSP has connected to the Eko Admin Portal, one way or
    another. Two independent ways to already be "done", checked here so
    neither path nags the CSP a second time:
      1. A real API key is already configured (e.g. entered at INSTALL.bat's
         "Connect to Eko Admin Portal" prompt, or pre-baked into .env by Eko)
         — nothing to ask, the web screen would be redundant.
      2. The CSP has been through the web screen itself (Save OR explicit
         Skip both set this DB flag) — see admin_connect()."""
    if config.ADMIN_API_KEY and config.ADMIN_API_KEY != _DEMO_API_KEY:
        return False
    return get_config_value(_ADMIN_SETUP_FLAG) != "1"


# ── First-run onboarding gate ─────────────────────────────────────────────────
# Before the operator can log in, a fresh install must be set up ONCE: the CSP
# enters their own login ID + password + branch details on the onboarding screen.
# Until that is done, every dashboard route redirects there. Tests/dev seed a
# default operator and set this flag (see config.SEED_DEFAULT_USER), so they skip
# the wizard. The admin server URL is never shown here — it stays masked.
_ONBOARDING_FLAG = "onboarding_complete"


def _onboarding_done() -> bool:
    return get_config_value(_ONBOARDING_FLAG) == "1"


@dashboard_bp.before_request
def _require_onboarding():
    ep = request.endpoint or ""
    # the onboarding screen itself + static assets must stay reachable
    if ep in ("dashboard.onboarding", "static") or ep.endswith(".static"):
        return None
    if not _onboarding_done():
        return redirect(url_for("dashboard.onboarding"))
    return None


@dashboard_bp.route("/onboarding", methods=["GET", "POST"])
def onboarding():
    """One-time setup shown BEFORE login on a fresh install. The CSP sets their
    own login ID + password and enters their branch details (name, branch code,
    address, phone). Replaces the earlier auto-generated-password scheme."""
    if _onboarding_done():
        return redirect(url_for("dashboard.login"))

    form = {"login_id": "", "csp_name": "", "branch_code": "",
            "csp_address": "", "csp_phone": ""}
    if request.method == "POST":
        from core.auth import hash_password
        form["login_id"] = request.form.get("login_id", "").strip()
        password = request.form.get("password", "").strip()
        confirm = request.form.get("confirm_password", "").strip()
        form["csp_name"] = request.form.get("csp_name", "").strip()
        form["branch_code"] = request.form.get("branch_code", "").strip()
        form["csp_address"] = request.form.get("csp_address", "").strip()
        form["csp_phone"] = request.form.get("csp_phone", "").strip()

        errors = []
        if not form["login_id"]:
            errors.append("Login ID is required")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters")
        if password != confirm:
            errors.append("Passwords do not match")
        if not form["csp_name"]:
            errors.append("CSP name is required")
        if not form["csp_address"]:
            errors.append("Branch address is required")
        if not form["csp_phone"]:
            errors.append("CSP phone is required")

        if errors:
            flash(errors[0])
            return render_template("onboarding.html", form=form)

        create_operator(form["login_id"], hash_password(password))
        update_branch(form["csp_name"], form["csp_phone"], form["csp_address"],
                      form["branch_code"] or None)
        set_config_value(_ONBOARDING_FLAG, "1")

        # Save the login the CSP just chose to CSP_Login.txt on their Desktop, as
        # a personal reminder (their own credential, not customer PII).
        from core.credentials_file import write_login_file
        saved = write_login_file(form["login_id"], password)
        if saved:
            flash("Setup complete — your login was saved to CSP_Login.txt on your "
                  "Desktop. Please log in with the ID and password you just set.")
        else:
            flash("Setup complete — please log in with the ID and password you just set.")
        return redirect(url_for("dashboard.login"))

    return render_template("onboarding.html", form=form)


# ── Auth ──────────────────────────────────────────────────────────────────────

@dashboard_bp.route("/", methods=["GET"])
def index():
    return redirect(url_for("dashboard.login"))


@dashboard_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        from core import auth
        csp_id = request.form.get("csp_id", "").strip()
        password = request.form.get("password", "").strip()

        locked = auth.is_locked(csp_id)
        if locked:
            flash(f"Too many attempts. Try again in {int(locked)} seconds.")
            return render_template("login.html")

        user = get_user(csp_id)
        if user and auth.verify_password(password, user["password"]):
            auth.record_success(csp_id)
            session.clear()
            session["logged_in"] = True
            _branch = get_branch()
            session["csp_name"] = _branch["csp_name"] if _branch else config.CSP_NAME
            session["user_id"] = user["id"]
            update_last_login(csp_id)
            insert_audit_log(user["id"], "login")
            # The Eko Admin connection is provisioned by Eko (CSP_ID + API_KEY
            # baked into .env by the per-CSP setup) and controlled ONLY from the
            # admin portal — the CSP is never asked to set it up and cannot
            # disable it, so there is no admin-connect step in the CSP flow.
            return redirect(url_for("dashboard.welcome"))

        auth.record_failure(csp_id)
        if user:
            insert_audit_log(user["id"], "login_failed", f"login_id={csp_id}")
        flash("Invalid credentials")
    return render_template("login.html")


@dashboard_bp.route("/logout")
def logout():
    if session.get("user_id"):
        insert_audit_log(session["user_id"], "logout")
    session.clear()
    return redirect(url_for("dashboard.login"))


# ── Eko Admin connection — provisioned by Eko, NOT configurable by the CSP ────
# The CSP_ID + API key are baked into .env by the admin's per-CSP setup file, and
# the connection is enabled/disabled ONLY from the Eko admin portal (the API Keys
# page). The CSP has no way to set it up, change it, skip it, or disable it — so
# this old self-service screen is retired and simply sends the operator back to
# the dashboard. Reporting is driven purely by the baked .env at startup.

@dashboard_bp.route("/admin-connect", methods=["GET", "POST"])
def admin_connect():
    guard = _login_required()
    if guard:
        return guard
    return redirect(url_for("dashboard.welcome"))


@dashboard_bp.route("/audit", methods=["GET"])
def audit():
    guard = _login_required()
    if guard:
        return guard
    logs = list_audit_logs(200)
    return render_template("audit.html", logs=logs,
                           csp_name=session.get("csp_name"))


# ── Page 2: Welcome — campaign selection only ─────────────────────────────────

CAMPAIGN_NAMES = {"inoperative_accounts": "Inoperative Accounts"}


@dashboard_bp.route("/welcome", methods=["GET"])
def welcome():
    guard = _login_required()
    if guard:
        return guard
    return render_template("welcome.html", csp_name=session.get("csp_name"))


# ── Page 3: Documents — upload + history for one campaign ─────────────────────

@dashboard_bp.route("/campaign/<campaign_id>/documents", methods=["GET"])
def documents(campaign_id: str):
    guard = _login_required()
    if guard:
        return guard
    docs = [d for d in list_documents() if d["campaign_id"] == campaign_id]
    return render_template(
        "documents.html",
        csp_name=session.get("csp_name"),
        campaign_id=campaign_id,
        campaign_name=CAMPAIGN_NAMES.get(campaign_id, campaign_id),
        documents=docs,
    )


@dashboard_bp.route("/upload", methods=["POST"])
def upload():
    guard = _login_required()
    if guard:
        return guard

    campaign_id = request.form.get("campaign_id", "inoperative_accounts")
    docs_url = url_for("dashboard.documents", campaign_id=campaign_id)

    files = [
        f for f in (request.files.getlist("documents") or request.files.getlist("document"))
        if f and f.filename
    ]
    if not files:
        flash("No file selected")
        return redirect(docs_url)

    if len(files) > config.MAX_BATCH_FILES:
        flash(f"Too many files selected (max {config.MAX_BATCH_FILES})")
        return redirect(docs_url)

    total_size = 0
    for file in files:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            flash(f"Unsupported file type: {ext}")
            return redirect(docs_url)

        size_bytes = len(file.read())
        file.seek(0)
        if size_bytes > config.MAX_UPLOAD_MB * 1024 * 1024:
            flash(f"{file.filename} is too large (max {config.MAX_UPLOAD_MB} MB per file)")
            return redirect(docs_url)
        total_size += size_bytes

    max_total = config.MAX_UPLOAD_MB * config.MAX_BATCH_FILES * 1024 * 1024
    if total_size > max_total:
        flash(
            f"Batch too large (max {config.MAX_BATCH_FILES} files, "
            f"{config.MAX_UPLOAD_MB} MB each)"
        )
        return redirect(docs_url)

    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    save_paths = []
    original_names = []
    for file in files:
        ext = os.path.splitext(file.filename)[1].lower()
        safe_name = f"{uuid.uuid4().hex}{ext}"
        save_path = os.path.join(config.UPLOAD_FOLDER, safe_name)
        file.save(save_path)
        save_paths.append(save_path)
        original_names.append(file.filename)

    # Encrypted mobile-scanner packages (.cspx) arrive as an opaque blob — the
    # phone encrypted the scanned Excel before it ever touched WhatsApp (see
    # core/import_crypto.py). Decrypt each one to a plain .xlsx right here, so
    # everything downstream (parser, review gate) treats it as an ordinary Excel
    # with zero changes. A missing/wrong passphrase aborts the WHOLE batch with a
    # clear message; nothing half-decrypted is ever processed.
    if any(p.lower().endswith(".cspx") for p in save_paths):
        from core import import_crypto
        from core.settings import get_import_passphrase
        passphrase = get_import_passphrase()
        for i, path in enumerate(save_paths):
            if not path.lower().endswith(import_crypto.EXT):
                continue
            try:
                with open(path, "rb") as fh:
                    xlsx = import_crypto.decrypt_package(fh.read(), passphrase)
            except import_crypto.DecryptError as e:
                for p in save_paths:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                flash(f"Could not open the encrypted file {original_names[i]}: {e}")
                return redirect(docs_url)
            new_path = path[:-len(import_crypto.EXT)] + ".xlsx"
            with open(new_path, "wb") as fh:
                fh.write(xlsx)
            try:
                os.remove(path)   # encrypted original not needed once decrypted
            except OSError:
                pass
            save_paths[i] = new_path
            if original_names[i].lower().endswith(import_crypto.EXT):
                original_names[i] = original_names[i][:-len(import_crypto.EXT)] + ".xlsx"

    # Optional page range (1-based, inclusive) — the CSP chooses how far into
    # a scanned PDF to run this batch. Blank = whole document.
    def _int_or_none(key):
        raw = (request.form.get(key) or "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None
    page_from = _int_or_none("page_from")
    page_to = _int_or_none("page_to")

    # Extract to a review draft in the BACKGROUND — a 10-15 page scanned PDF can
    # take minutes to OCR on the 4 GB deploy PC, so we don't block the browser.
    # The upload kicks off a job and sends the CSP to a progress screen that
    # polls until the draft is ready, then opens the review page. Nothing is
    # created/sent until the CSP confirms on that review screen.
    from core.extraction import build_draft
    from core import jobs
    job_id = jobs.start(build_draft, save_paths, campaign_id, original_names,
                        page_from=page_from, page_to=page_to)

    insert_audit_log(session["user_id"], "document_extraction_started",
                     f"campaign={campaign_id} files={len(files)}")
    return redirect(url_for("dashboard.extracting",
                            campaign_id=campaign_id, job_id=job_id))


# ── Background extraction progress ────────────────────────────────────────────

@dashboard_bp.route("/campaign/<campaign_id>/extracting/<job_id>", methods=["GET"])
def extracting(campaign_id: str, job_id: str):
    """Progress screen shown while a scanned upload OCRs in the background. It
    polls /extract/status/<job_id> and redirects to the review page when done."""
    guard = _login_required()
    if guard:
        return guard
    return render_template(
        "extracting.html",
        csp_name=session.get("csp_name"),
        campaign_id=campaign_id,
        campaign_name=CAMPAIGN_NAMES.get(campaign_id, campaign_id),
        job_id=job_id,
    )


@dashboard_bp.route("/extract/status/<job_id>", methods=["GET"])
def extract_status(job_id: str):
    """JSON progress for the extracting screen: {status, done, total, message,
    draft_id?, error?}. status is running | done | error | unknown."""
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core import jobs
    job = jobs.get(job_id)
    if not job:
        return jsonify({"status": "unknown"}), 404
    out = {
        "status": job["status"],
        "done": job.get("done", 0),
        "total": job.get("total", 0),
        "message": job.get("message", ""),
    }
    if job["status"] == "done":
        out["draft_id"] = job.get("result")
    elif job["status"] == "error":
        out["error"] = job.get("error", "extraction failed")
    return jsonify(out)


# ── Preview + edit accuracy gate ──────────────────────────────────────────────

@dashboard_bp.route("/campaign/<campaign_id>/review/<draft_id>", methods=["GET"])
def review(campaign_id: str, draft_id: str):
    guard = _login_required()
    if guard:
        return guard
    from core.extraction import load_draft
    try:
        draft = load_draft(draft_id)
    except (OSError, ValueError):
        flash("That review draft is no longer available. Please upload again.")
        return redirect(url_for("dashboard.documents", campaign_id=campaign_id))
    return render_template(
        "review.html",
        csp_name=session.get("csp_name"),
        campaign_id=campaign_id,
        campaign_name=CAMPAIGN_NAMES.get(campaign_id, campaign_id),
        draft_id=draft_id,
        rows=draft["rows"],
        page_images=draft["meta"].get("page_images", []),
        page_span=draft["meta"].get("page_span"),
    )


@dashboard_bp.route("/draft/<draft_id>/page/<page_name>", methods=["GET"])
def draft_page(draft_id: str, page_name: str):
    guard = _login_required()
    if guard:
        return guard
    from flask import send_file
    from core.extraction import draft_page_path
    path = draft_page_path(draft_id, page_name)
    if not os.path.exists(path):
        return ("not found", 404)
    return send_file(path, mimetype="image/png")


@dashboard_bp.route("/campaign/<campaign_id>/review/<draft_id>/confirm", methods=["POST"])
def confirm_review(campaign_id: str, draft_id: str):
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core.extraction import commit_draft
    data = request.get_json(silent=True) or {}
    edited_rows = data.get("rows") or []
    if not edited_rows:
        return jsonify({"ok": False, "error": "no rows to save"}), 400

    batch_id, stats = commit_draft(draft_id, edited_rows, campaign_id)
    if stats["valid"] == 0:
        return jsonify({"ok": False, "error": "No valid rows. Check name and balance band.",
                        "stats": stats}), 400

    insert_audit_log(session["user_id"], "document_committed",
                     f"batch={batch_id} valid={stats['valid']} "
                     f"duplicates={stats.get('duplicates',0)} "
                     f"not_reachable={stats.get('not_reachable',0)}")
    return jsonify({
        "ok": True, "batch_id": batch_id, "stats": stats,
        "redirect": url_for("dashboard.campaign", campaign_id=campaign_id, batch_id=batch_id),
    })


@dashboard_bp.route("/campaign/<campaign_id>/review/<draft_id>/cancel", methods=["POST"])
def cancel_review(campaign_id: str, draft_id: str):
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core.extraction import discard_draft
    discard_draft(draft_id)
    return jsonify({"ok": True,
                    "redirect": url_for("dashboard.documents", campaign_id=campaign_id)})


# ── Campaign Dashboard ────────────────────────────────────────────────────────

@dashboard_bp.route("/campaign/<campaign_id>", methods=["GET"])
def campaign(campaign_id: str):
    guard = _login_required()
    if guard:
        return guard

    batch_id = request.args.get("batch_id")
    if not batch_id:
        # default to most recent batch for this campaign
        docs = list_documents()
        for d in docs:
            if d["campaign_id"] == campaign_id:
                batch_id = d["batch_id"]
                break

    overview = {}
    cases = []
    sensitive = []
    escalations = []
    visit_log = []
    biz_breakdown = {}
    categories = []
    queue_pending = 0
    unqueued_count = 0

    if batch_id:
        overview = batch_overview(batch_id)
        cases = list_cases_with_tracking(batch_id)
        sensitive = list_sensitive_pending(batch_id)
        escalations = list_escalations(batch_id)
        visit_log = list_visit_log(batch_id)
        biz_breakdown = business_status_breakdown(batch_id)
        categories = category_breakdown(batch_id)
        from database.queries import list_dispatch_queue, list_unqueued_cases
        queue_pending = len(list_dispatch_queue(batch_id))
        # awaiting a CSP decision: not yet queued, and not sensitive (those
        # always show separately in the flagged panel below)
        unqueued_count = len([c for c in list_unqueued_cases(batch_id) if not c["is_sensitive"]])

    documents = list_documents()

    from core.settings import get_csp_settings, import_passphrase_is_set
    csp_settings = get_csp_settings()
    # Whether a mobile-scanner import passphrase is set (never send the value
    # itself to the browser — just whether one exists).
    import_passphrase_set = import_passphrase_is_set()

    # An update may already be staged (downloaded + verified by the background
    # sync loop) but not yet applied — that only happens at the NEXT full
    # restart (run.bat calls `core.updater --apply-if-pending` before
    # launching). Surface it here so the CSP knows to restart.
    from core.updater import pending_version
    pending_update = pending_version()

    return render_template(
        "campaign.html",
        campaign_id=campaign_id,
        csp_name=session.get("csp_name"),
        batch_id=batch_id,
        overview=overview,
        cases=cases,
        sensitive=sensitive,
        escalations=escalations,
        visit_log=visit_log,
        biz_breakdown=biz_breakdown,
        categories=categories,
        documents=documents,
        csp_settings=csp_settings,
        import_passphrase_set=import_passphrase_set,
        queue_pending=queue_pending,
        unqueued_count=unqueued_count,
        pending_update=pending_update,
    )


# ── Case detail page — click any case anywhere to open its full record ────────

@dashboard_bp.route("/campaign/<campaign_id>/case/<case_id>", methods=["GET"])
def case_detail(campaign_id: str, case_id: str):
    guard = _login_required()
    if guard:
        return guard

    from database.queries import (get_case, get_message, get_business_tracking,
                                  list_comm_attempts_for_case)

    case = get_case(case_id)
    if not case:
        flash("Case not found.")
        return redirect(url_for("dashboard.campaign", campaign_id=campaign_id))

    message = get_message(case_id)
    tracking = get_business_tracking(case_id)
    attempts = list_comm_attempts_for_case(case_id)

    # Customer data + message are LOCKED once cases are created. All corrections
    # are made at the review step BEFORE "Confirm & Create Cases"; after that the
    # record is immutable — DPDP-safer (no silent message change, and no way to
    # re-add PII to a closed/purged case). To fix a genuinely wrong row, the CSP
    # deletes the batch and re-uploads.
    can_edit = False
    # Approval is a SEND action, not a data edit: still allowed while the case
    # has a mobile and has not been queued/sent yet (mirrors approval.approve_case).
    can_approve = bool((case["mobile"] or "").strip()) and len(attempts) == 0

    # Prev/next in the SAME order as the Cases table, so the arrow keys walk
    # the batch exactly as the CSP sees the list.
    ordered = list_cases_with_tracking(case["batch_id"])
    ids = [c["case_id"] for c in ordered]
    prev_id = next_id = None
    position = total_in_batch = 0
    if case_id in ids:
        i = ids.index(case_id)
        position, total_in_batch = i + 1, len(ids)
        prev_id = ids[i - 1] if i > 0 else None
        next_id = ids[i + 1] if i < len(ids) - 1 else None

    from_sheet = request.args.get("from") == "sheet"

    return render_template(
        "case_detail.html",
        csp_name=session.get("csp_name"),
        campaign_id=campaign_id,
        case=case,
        message=message,
        tracking=tracking,
        attempts=attempts,
        can_approve=can_approve,
        can_edit=can_edit,
        from_sheet=from_sheet,
        prev_id=prev_id,
        next_id=next_id,
        position=position,
        total_in_batch=total_in_batch,
    )


# ── Manual review & approve "tick sheet" ──────────────────────────────────────
# Opened ONLY from the "Check manually, then approve" choice in the Start
# Messaging modal. Lists every case with an approved/tick state; the CSP can
# open any case (view + edit + approve), approve directly from a row, bulk
# "approve remaining", then send the approved ones. Approving anywhere (row or
# case-detail window) is the same DB action, so a tick shows here either way.

@dashboard_bp.route("/campaign/<campaign_id>/approve_sheet/<batch_id>", methods=["GET"])
def approve_sheet(campaign_id: str, batch_id: str):
    guard = _login_required()
    if guard:
        return guard
    # The review-and-approve sheet only lists REACHABLE customers (a valid
    # mobile). Not-reachable cases can never be messaged, so they don't belong
    # on the approval list — they appear in the Escalations panel on the
    # dashboard instead.
    all_cases = list_cases_with_tracking(batch_id)
    cases = [c for c in all_cases if (c["mobile"] or "").strip()]
    # "Approved" = the case is genuinely in the send pipeline (queued or sent) —
    # NOT merely that a communication row exists.
    _QUEUED = {"pending", "wa_attempted", "wa_delivered", "wa_read",
               "sms_sent", "sms_delivered"}
    approved = sum(1 for c in cases if c["comm_status"] in _QUEUED)
    reachable = len(cases)
    not_reachable = len(all_cases) - len(cases)
    return render_template(
        "approve_sheet.html",
        csp_name=session.get("csp_name"),
        campaign_id=campaign_id,
        batch_id=batch_id,
        cases=cases,
        approved_count=approved,
        reachable_count=reachable,
        not_reachable_count=not_reachable,
    )


@dashboard_bp.route("/api/case/<case_id>/update", methods=["POST"])
def update_case(case_id: str):
    """LOCKED. Customer records (and their message) are immutable once cases are
    created by "Confirm & Create Cases". All corrections happen at the review
    step before confirming; after that nothing about a case's customer data or
    message can be edited. This is a deliberate DPDP guarantee: it removes any
    path to silently change an already-generated message, and it closes the hole
    where PII could be re-typed into a case that was already closed and purged.
    To fix a genuinely wrong row, delete the batch and re-upload it.

    The route is kept (rather than deleted) so any stale client still gets a
    clear, explicit 403 instead of a 404."""
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    from database.queries import get_case

    case = get_case(case_id)
    if not case:
        return jsonify({"ok": False, "error": "case not found"}), 404
    return jsonify({"ok": False,
                    "error": "Customer records are locked after confirmation and "
                             "can no longer be edited. To correct a row, delete the "
                             "batch and upload it again."}), 403


@dashboard_bp.route("/api/case/<case_id>/mobile", methods=["POST"])
def update_case_mobile_route(case_id: str):
    """Correct ONLY the mobile number on a case. This is the ONE exception to the
    locked-after-confirmation rule (update_case): OCR misses/mis-reads ~1 in 7
    mobiles on a scanned page, and a wrong number means the customer can never be
    reached. Everything else (name, account, the generated message) stays
    immutable. DPDP-safe: the number is validated, re-encrypted at rest, refused
    on a purged/closed case (never re-introduce PII), and audit-logged. The
    message text is unaffected — it embeds the name, not the mobile — so no
    regeneration is needed; the dispatcher reads the fresh number at send time."""
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from database.queries import get_case, update_case_mobile
    data = request.get_json(silent=True) or {}
    raw = re.sub(r"\D", "", str(data.get("mobile", "")))
    if not (len(raw) == 10 and raw[0] in "6789"):
        return jsonify({"ok": False,
                        "error": "Enter a valid 10-digit mobile number starting 6-9."}), 400
    if not get_case(case_id):
        return jsonify({"ok": False, "error": "case not found"}), 404
    if not update_case_mobile(case_id, raw):
        return jsonify({"ok": False,
                        "error": "This case is closed/purged and can no longer be edited."}), 403
    insert_audit_log(session["user_id"], "case_mobile_edited", f"case={case_id}")
    return jsonify({"ok": True, "mobile": raw})


@dashboard_bp.route("/api/batch/<batch_id>/delete", methods=["POST"])
def delete_batch_route(batch_id: str):
    """Delete an uploaded batch and all its extracted data from the upload
    history. Refused while a dispatch is running (would pull cases out from
    under the sender)."""
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core import comm_runner
    from database.queries import delete_batch
    if comm_runner.is_running():
        return jsonify({"ok": False,
                        "error": "A dispatch is running. Stop it before deleting a batch."}), 400
    delete_batch(batch_id)
    insert_audit_log(session["user_id"], "batch_deleted", f"batch={batch_id}")
    return jsonify({"ok": True})


@dashboard_bp.route("/api/batch/<batch_id>/send_approved", methods=["POST"])
def send_approved(batch_id: str):
    """Send ONLY the cases already approved/queued (does not queue anyone new).
    Used by the 'Send approved now' button on the manual review sheet."""
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core import comm_runner
    result = comm_runner.start(batch_id, chunk_size=None)
    if result.get("started"):
        insert_audit_log(session["user_id"], "dispatch_started",
                         f"batch={batch_id} approved-only")
    return jsonify(result)


# ── Report download (CSV — no financial data, DPDP-safe columns only) ─────────

@dashboard_bp.route("/report/<batch_id>/cases.csv", methods=["GET"])
def download_cases_csv(batch_id: str):
    guard = _login_required()
    if guard:
        return guard

    import csv
    import io
    from flask import Response

    def _mask_name(name: str) -> str:
        # A downloaded CSV lands OUTSIDE the encrypted-at-rest boundary (it sits
        # in the CSP's Downloads folder, where an RBI inspection could read it),
        # so the name is masked to initials here just like on screen — never the
        # full plaintext name. e.g. "RAMESH KUMAR" -> "R. K."
        parts = [p for p in (name or "").split() if p]
        return " ".join(p[0].upper() + "." for p in parts) if parts else ""

    rows = list_cases_with_tracking(batch_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Name(masked)", "Mobile(masked)", "Band", "Village", "Taluka",
        "Channel", "Comm Status", "Business Status",
    ])
    for r in rows:
        masked = "xxxxx" + (r["mobile"][-5:] if r["mobile"] else "")
        writer.writerow([
            (_mask_name(r["name"]) if r["name"] else "[purged]"), masked, r["band_label"],
            r["village"] or "", r["taluka"] or "",
            r["channel"] or "", r["comm_status"] or "pending",
            r["business_status"] or "pending",
        ])

    insert_audit_log(session["user_id"], "report_download", f"batch={batch_id}")
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={batch_id}_cases.csv"},
    )


# ── API: CSP settings ─────────────────────────────────────────────────────────

@dashboard_bp.route("/api/settings", methods=["POST"])
def save_settings():
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    from core.settings import update_csp_settings, set_import_passphrase
    data = request.get_json(silent=True) or {}
    result = update_csp_settings(
        data.get("csp_name"), data.get("csp_phone"), data.get("csp_address"),
        data.get("branch_code", "")
    )
    if not result["ok"]:
        return jsonify({"ok": False, "errors": result["errors"]}), 400

    # Mobile-import passphrase is optional and only touched when the field is
    # present in the payload, so saving plain CSP details never clears it. An
    # empty string explicitly clears it (disables encrypted phone import).
    if "import_passphrase" in data:
        pw_res = set_import_passphrase(data.get("import_passphrase"))
        if not pw_res["ok"]:
            return jsonify({"ok": False, "errors": pw_res["errors"]}), 400

    insert_audit_log(session["user_id"], "settings_updated",
                     f"csp_name={data.get('csp_name')}")
    return jsonify({"ok": True})


# ── API: channel status ───────────────────────────────────────────────────────

@dashboard_bp.route("/api/channel/status", methods=["GET"])
def channel_status():
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    import requests
    # WhatsApp — ping the local wa_server.js
    wa_ready = False
    wa_detail = "offline"
    try:
        resp = requests.get(f"{config.WA_SERVER_URL}/status", timeout=3)
        wa_ready = bool(resp.json().get("ready"))
        wa_detail = "connected" if wa_ready else "starting / awaiting QR scan"
    except requests.RequestException:
        wa_detail = "server not running"

    # SMS — MSG91 considered configured if an auth key is present
    sms_configured = bool(config.MSG91_AUTH_KEY)
    sms_detail = "configured" if sms_configured else "not configured (fallback disabled)"

    return jsonify({
        "whatsapp": {"ready": wa_ready, "detail": wa_detail},
        "sms": {"configured": sms_configured, "detail": sms_detail},
    })


@dashboard_bp.route("/api/channel/whatsapp/start", methods=["POST"])
def start_whatsapp_server():
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    import requests
    try:
        resp = requests.get(f"{config.WA_SERVER_URL}/status", timeout=2)
        data = resp.json()
        return jsonify({
            "ok": True,
            "already_running": True,
            "ready": bool(data.get("ready")),
            "has_qr": bool(data.get("has_qr")),
        })
    except requests.RequestException:
        pass

    whatsapp_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "whatsapp")
    )
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [npm_cmd, "start"],
            cwd=whatsapp_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    insert_audit_log(session["user_id"], "whatsapp_server_started")
    return jsonify({"ok": True, "already_running": False})


@dashboard_bp.route("/api/channel/whatsapp/reset", methods=["POST"])
def whatsapp_reset():
    """Force the WhatsApp server to drop any stale/logged-out session and
    generate a fresh QR. Fixes the case where, after a logout/re-login, the QR
    would not load and linking became impossible."""
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    import requests
    try:
        resp = requests.post(f"{config.WA_SERVER_URL}/reset", timeout=5)
        resp.raise_for_status()
        insert_audit_log(session["user_id"], "whatsapp_reset")
        return jsonify(resp.json())
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": str(e)}), 503


@dashboard_bp.route("/api/channel/whatsapp/qr", methods=["GET"])
def whatsapp_qr():
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    import requests
    try:
        resp = requests.get(f"{config.WA_SERVER_URL}/qr", timeout=3)
        resp.raise_for_status()
        return jsonify(resp.json())
    except requests.RequestException as e:
        return jsonify({
            "ready": False,
            "qr": None,
            "error": str(e),
        }), 503


# ── API: communication dispatch control ──────────────────────────────────────

@dashboard_bp.route("/api/batch/<batch_id>/send", methods=["POST"])
def start_dispatch(batch_id: str):
    """
    'Send Automatically' — the CSP's custom-range picker feeds chunk_size here.
    Queues + dispatches up to chunk_size not-yet-decided, non-sensitive cases
    in one action. Cases already queued via the manual approve path (see
    core/approval.py) are picked up too, since comm_runner just sends whatever
    is 'pending' — so this button also works as the final "Send" step after a
    manual review-and-approve session.
    """
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core import approval
    data = request.get_json(silent=True) or {}
    chunk_size = data.get("chunk_size")  # None = send everything left in queue
    try:
        chunk_size = int(chunk_size) if chunk_size not in (None, "", "all") else None
    except (TypeError, ValueError):
        chunk_size = None
    result = approval.queue_and_dispatch(batch_id, chunk_size=chunk_size)
    if result["started"]:
        insert_audit_log(session["user_id"], "dispatch_started",
                         f"batch={batch_id} chunk_size={chunk_size or 'all'}")
    return jsonify(result)


@dashboard_bp.route("/api/dispatch/pause", methods=["POST"])
def pause_dispatch():
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core import comm_runner
    comm_runner.pause()
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dispatch/resume", methods=["POST"])
def resume_dispatch():
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core import comm_runner
    comm_runner.resume()
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dispatch/stop", methods=["POST"])
def stop_dispatch():
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core import comm_runner
    comm_runner.stop()
    insert_audit_log(session["user_id"], "dispatch_stopped")
    return jsonify({"ok": True})


@dashboard_bp.route("/api/dispatch/status", methods=["GET"])
def dispatch_status():
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401
    from core import comm_runner
    return jsonify(comm_runner.get_status())


# ── API: business status update ───────────────────────────────────────────────

@dashboard_bp.route("/api/case/<case_id>/status", methods=["POST"])
def update_case_status(case_id: str):
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    from core import tracking
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")

    result = tracking.transition(case_id, new_status)
    if not result["ok"]:
        return jsonify({"ok": False, "error": result["reason"]}), 400

    insert_audit_log(session["user_id"], "status_update",
                     f"case={case_id} {result['from']} -> {result['to']}")
    return jsonify(result)


# ── API: manual per-case / bulk approval (optional review path) ───────────────
# Any case — sensitive or not — can be approved individually here, e.g. from
# the case detail page when the CSP wants to double-check a message before it
# sends. This is the ONLY path a sensitive case can be queued through.

@dashboard_bp.route("/api/case/<case_id>/approve", methods=["POST"])
def approve_case(case_id: str):
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    from core import approval
    result = approval.approve_case(case_id)
    if not result["ok"]:
        return jsonify(result), 404

    insert_audit_log(session["user_id"], "case_approved", f"case={case_id}")
    return jsonify(result)


@dashboard_bp.route("/api/case/<case_id>/unapprove", methods=["POST"])
def unapprove_case(case_id: str):
    """Undo a case's approval (remove it from the queue) before sending."""
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    from core import approval
    result = approval.unapprove_case(case_id)
    if not result["ok"]:
        return jsonify(result), 400

    insert_audit_log(session["user_id"], "case_unapproved", f"case={case_id}")
    return jsonify(result)


@dashboard_bp.route("/api/batch/<batch_id>/approve_remaining", methods=["POST"])
def approve_remaining_route(batch_id: str):
    """Bulk step for the manual-review path: queue the remaining
    non-sensitive, not-yet-queued cases (up to the same custom-range limit
    the CSP chose) in one click, after reviewing a few individually and
    deciding to trust the rest."""
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    from core import approval
    data = request.get_json(silent=True) or {}
    limit = data.get("limit")
    try:
        limit = int(limit) if limit not in (None, "", "all") else None
    except (TypeError, ValueError):
        limit = None

    result = approval.approve_remaining(batch_id, limit=limit)
    insert_audit_log(session["user_id"], "batch_approve_remaining",
                     f"batch={batch_id} approved={result['approved']}")
    return jsonify(result)


@dashboard_bp.route("/api/batch/<batch_id>/generate_messages", methods=["POST"])
def generate_messages_for_batch(batch_id: str):
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    from core.message_engine import generate_batch_messages
    result = generate_batch_messages(batch_id)
    insert_audit_log(session["user_id"], "messages_generated",
                     f"batch={batch_id} generated={result['generated']}")
    return jsonify(result)


@dashboard_bp.route("/api/case/<case_id>/skip", methods=["POST"])
def skip_sensitive(case_id: str):
    guard = _login_required()
    if guard:
        return jsonify({"error": "not logged in"}), 401

    from datetime import datetime, timezone
    from database.queries import insert_comm_attempt, purge_case_pii
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    insert_comm_attempt(case_id, "whatsapp", "wa_failed",
                        error_detail="skipped by CSP (sensitive case)")
    # Closed without a visit: stamp closed_at, leave visited_at NULL so the
    # case is correctly excluded from the Visited metric and visit log.
    update_business_status(case_id, "case_closed", closed_at=now)
    # This route sets case_closed directly (not via core.tracking.transition),
    # so it must purge PII itself too — RBI/DPDP: no customer PII retained
    # once a case is terminal.
    purge_case_pii(case_id)
    insert_audit_log(session["user_id"], "sensitive_skipped", f"case={case_id}")
    return jsonify({"ok": True})
