"""Admin portal UI — multi-page, each page one clear job. Read-only monitoring
of the CSP fleet using only the allow-listed, PII-free data."""
import hashlib
import json
import os
import re
import secrets
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, send_from_directory, Response)

from admin_dashboard.db import get_connection

ui_bp = Blueprint("admin_ui", __name__)
ONLINE_WINDOW_MIN = 15

_DIR = os.path.dirname(os.path.abspath(__file__))
RELEASES_DIR = os.path.join(_DIR, "releases")


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _version_from_zip(path: str):
    """Read the VERSION file bundled inside an uploaded package (at the zip root,
    or under a single wrapping folder). This is the version the package will
    ACTUALLY report once it is applied on a CSP PC (config.APP_VERSION reads that
    same VERSION file), so it — not a hand-typed field — is the authoritative
    version. Deriving it here stops a typo from causing either a silent
    no-update (typed < real) or an endless 5-minutely re-stage loop (typed >
    real, so the CSP forever sees 'newer available'). Returns None if absent."""
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith("/"):
                    continue
                parts = n.replace("\\", "/").split("/")
                if parts[-1] == "VERSION" and len(parts) <= 2:
                    return z.read(n).decode("utf-8", "replace").strip() or None
    except Exception:
        return None
    return None


def _save_release(file_storage, kind: str, version: str = None) -> dict:
    """Store an uploaded package on disk + index it in the releases table.
    Computes the sha256 here so the admin never has to paste a hash by hand, and
    derives the authoritative version from the package's own VERSION file (a
    hand-typed version is only a fallback when the zip has none)."""
    os.makedirs(RELEASES_DIR, exist_ok=True)
    orig_name = file_storage.filename or "package.zip"
    stored_name = f"{uuid.uuid4().hex}_{orig_name}"
    path = os.path.join(RELEASES_DIR, stored_name)
    file_storage.save(path)

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    size = os.path.getsize(path)

    # The VERSION baked into the package wins over anything typed in the form.
    effective_version = (_version_from_zip(path) or (version or "")).strip()

    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO releases (kind, version, filename, stored_name,
                   sha256, size_bytes, uploaded_at)
               VALUES (?,?,?,?,?,?,?)""",
            (kind, effective_version, orig_name, stored_name, digest, size, _now_iso()))
        conn.commit()
        release_id = cur.lastrowid
    return {"id": release_id, "filename": orig_name, "sha256": digest,
            "size": size, "version": effective_version}


def _now():
    return datetime.now(timezone.utc)


def _is_online(last_seen: str) -> bool:
    if not last_seen:
        return False
    try:
        dt = datetime.fromisoformat(last_seen)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (_now() - dt) <= timedelta(minutes=ONLINE_WINDOW_MIN)
    except ValueError:
        return False


def login_required(fn):
    @wraps(fn)
    def wrap(*a, **k):
        if not session.get("admin_in"):
            return redirect(url_for("admin_ui.login"))
        return fn(*a, **k)
    return wrap


@ui_bp.route("/api-keys", methods=["GET", "POST"])
@login_required
def api_keys():
    """Issue / rotate / revoke per-CSP API keys. A CSP has no way to report
    (POST /report, GET /sync both 401 without one) until an admin generates a
    key for its csp_id here — this is the missing piece that made 'hum denge
    uss CSP ko' only work for the single seeded demo key before now. The
    plaintext key is shown ONCE, right after issue/rotate, on this same
    response (never redirected away, or it would be lost); the list below
    only ever shows the last 4 characters."""
    new_key = None
    if request.method == "POST":
        action = request.form.get("action")
        csp_id = request.form.get("csp_id", "").strip()
        if not csp_id:
            flash("CSP ID is required.")
            return redirect(url_for("admin_ui.api_keys"))

        if action == "toggle":
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT active FROM api_keys WHERE csp_id=?", (csp_id,)).fetchone()
                if row:
                    conn.execute("UPDATE api_keys SET active=? WHERE csp_id=?",
                                (0 if row["active"] else 1, csp_id))
                    conn.commit()
            return redirect(url_for("admin_ui.api_keys"))

        # issue (new CSP) or rotate (existing CSP gets a fresh key + re-activates)
        name = request.form.get("name", "").strip()
        key = secrets.token_urlsafe(32)
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO api_keys (csp_id, api_key, name, active, created_at)
                   VALUES (?,?,?,1,?)
                   ON CONFLICT(csp_id) DO UPDATE SET
                       api_key=excluded.api_key, name=excluded.name,
                       active=1, created_at=excluded.created_at""",
                (csp_id, key, name, _now_iso()))
            conn.commit()
        new_key = {"csp_id": csp_id, "api_key": key}
        flash(f"Key issued for {csp_id} — copy it now, it will not be shown again.")

    with get_connection() as conn:
        keys = conn.execute("SELECT * FROM api_keys ORDER BY csp_id").fetchall()
    return render_template("admin_api_keys.html", keys=keys, new_key=new_key)


@ui_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        from core.auth import verify_password
        lid = request.form.get("login_id", "").strip()
        pw = request.form.get("password", "").strip()
        with get_connection() as conn:
            u = conn.execute("SELECT * FROM admin_users WHERE login_id=?", (lid,)).fetchone()
        if u and verify_password(pw, u["password"]):
            session.clear()
            session["admin_in"] = True
            session["admin_login"] = lid
            return redirect(url_for("admin_ui.fleet"))
        flash("Invalid credentials")
    return render_template("admin_login.html")


@ui_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin_ui.login"))


@ui_bp.route("/")
@login_required
def fleet():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM csps ORDER BY name, csp_id").fetchall()
        # The admin-set label (from the API Keys page) is authoritative for
        # display and WINS over the CSP's self-reported name — which can be a
        # placeholder like "Demo CSP" until the CSP finishes branch onboarding.
        labels = {r["csp_id"]: r["name"] for r in conn.execute(
            "SELECT csp_id, name FROM api_keys").fetchall()}
    csps = []
    for r in rows:
        d = dict(r)
        d["online"] = _is_online(r["last_seen"])
        d["name"] = (labels.get(d["csp_id"]) or "").strip() or d.get("name")
        csps.append(d)
    online = sum(1 for c in csps if c["online"])
    return render_template("admin_fleet.html", csps=csps, total=len(csps),
                           online=online, offline=len(csps) - online,
                           window=ONLINE_WINDOW_MIN)


@ui_bp.route("/csp/<csp_id>")
@login_required
def csp_detail(csp_id):
    with get_connection() as conn:
        c = conn.execute("SELECT * FROM csps WHERE csp_id=?", (csp_id,)).fetchone()
        label = conn.execute(
            "SELECT name FROM api_keys WHERE csp_id=?", (csp_id,)).fetchone()
        prog = conn.execute(
            "SELECT * FROM progress WHERE csp_id=? ORDER BY month DESC, campaign_id",
            (csp_id,)).fetchall()
        band_rows = conn.execute(
            "SELECT * FROM progress_bands WHERE csp_id=? ORDER BY band", (csp_id,)
        ).fetchall()
        audit = conn.execute(
            "SELECT type, ts FROM audit WHERE csp_id=? ORDER BY id DESC LIMIT 50",
            (csp_id,)).fetchall()
        updates = conn.execute(
            "SELECT from_version, to_version, ts FROM update_events WHERE csp_id=? "
            "ORDER BY id DESC LIMIT 50", (csp_id,)).fetchall()
    if not c:
        return "CSP not found", 404
    # group bands by (campaign_id, month) so each progress row shows its bars
    bands = {}
    for b in band_rows:
        bands.setdefault((b["campaign_id"], b["month"]), []).append(dict(b))
    prog = [dict(p, bands=bands.get((p["campaign_id"], p["month"]), [])) for p in prog]
    d = dict(c); d["online"] = _is_online(c["last_seen"])
    if label and (label["name"] or "").strip():   # admin-set label wins over self-reported
        d["name"] = label["name"].strip()
    return render_template("admin_csp_detail.html", c=d, progress=prog, audit=audit,
                           updates=updates, update_count=len(updates))


@ui_bp.route("/campaigns")
@login_required
def campaigns():
    """Fleet-wide campaign rollup: message tracking + visit tracking summed
    across every CSP, per campaign/month, PLUS each contributing CSP's own
    numbers underneath (still aggregate counts only — no PII)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT campaign_id, month,
                      COUNT(DISTINCT csp_id) csps,
                      SUM(total) total, SUM(reached) reached, SUM(failed) failed,
                      SUM(wa_sent) wa_sent, SUM(wa_delivered) wa_delivered,
                      SUM(wa_read) wa_read, SUM(wa_failed) wa_failed,
                      SUM(sms_sent) sms_sent, SUM(sms_delivered) sms_delivered,
                      SUM(sms_failed) sms_failed, SUM(escalated) escalated,
                      SUM(visit_pending) visit_pending, SUM(visited) visited,
                      SUM(in_progress) in_progress, SUM(completed) completed,
                      SUM(closed) closed
               FROM progress GROUP BY campaign_id, month
               ORDER BY month DESC, campaign_id""").fetchall()
        band_rows = conn.execute(
            """SELECT campaign_id, month, band,
                      SUM(total) total, SUM(reached) reached
               FROM progress_bands GROUP BY campaign_id, month, band
               ORDER BY band""").fetchall()
        csp_rows = conn.execute(
            """SELECT p.campaign_id, p.month, p.csp_id, c.name AS csp_name,
                      p.total, p.reached, p.failed, p.pct,
                      p.wa_delivered, p.wa_read, p.sms_delivered, p.escalated,
                      p.visited, p.closed
               FROM progress p LEFT JOIN csps c ON c.csp_id = p.csp_id
               ORDER BY p.campaign_id, p.month, c.name, p.csp_id""").fetchall()
    bands = {}
    for b in band_rows:
        bands.setdefault((b["campaign_id"], b["month"]), []).append(dict(b))
    per_csp = {}
    for c in csp_rows:
        per_csp.setdefault((c["campaign_id"], c["month"]), []).append(dict(c))
    data = []
    for r in rows:
        d = dict(r)
        d["reach_rate"] = round(100.0 * (d["reached"] or 0) / d["total"], 1) if d["total"] else 0.0
        d["bands"] = bands.get((r["campaign_id"], r["month"]), [])
        d["per_csp"] = per_csp.get((r["campaign_id"], r["month"]), [])
        data.append(d)
    return render_template("admin_campaigns.html", rows=data)


@ui_bp.route("/earnings")
@login_required
def earnings():
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT p.csp_id, c.name, p.campaign_id, p.month, p.earnings, p.pct,
                      p.total, p.reached, p.closed
               FROM progress p LEFT JOIN csps c ON c.csp_id=p.csp_id
               ORDER BY p.month DESC, c.name""").fetchall()
        tot = conn.execute("SELECT COALESCE(SUM(earnings),0) s FROM progress").fetchone()["s"]
    return render_template("admin_earnings.html", rows=rows, total_earnings=tot)


@ui_bp.route("/setup/csp_setup_bat")
@login_required
def download_csp_setup_bat():
    """Generate a ready-to-send CSP_Setup.bat: the same file kept in the CSP
    app folder (code/csp_dashboard/CSP_Setup.bat), with its APP_URL line
    pre-filled to point at the currently uploaded install package's public
    download link. Read from that file at request time (not duplicated here),
    so improvements to CSP_Setup.bat stay in sync automatically.

    Pass ?csp_id=CSP002 to ALSO bake that CSP's active API key straight into
    the file — CSP_Setup.bat then writes .env itself before the dependency
    installer runs, so INSTALL.bat's own connect prompt never fires. This is
    the "one single file, nothing else to send" path: the CSP gets ONE
    attachment and it is fully self-contained, no separate key message."""
    csp_id = request.args.get("csp_id", "").strip()
    key_row = None
    download_url = None
    with get_connection() as conn:
        if csp_id:
            key_row = conn.execute(
                "SELECT api_key FROM api_keys WHERE csp_id=? AND active=1", (csp_id,)
            ).fetchone()
            if not key_row:
                flash(f"No active API key for {csp_id} — issue one on the API Keys page first.")
                return redirect(url_for("admin_ui.api_keys"))
        # An uploaded install package is OPTIONAL: by default CSP_Setup.bat already
        # points APP_URL at the public GitHub repo, so no upload is needed. Only if
        # you deliberately self-host a package here do we override APP_URL with it.
        latest = conn.execute(
            "SELECT * FROM releases WHERE kind='install' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest:
            download_url = url_for("admin_ui.download_release", release_id=latest["id"],
                                   filename=latest["filename"], _external=True)

    template_path = os.path.join(os.path.dirname(_DIR), "csp_dashboard", "CSP_Setup.bat")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return ("CSP_Setup.bat template not found on the server.", 500)

    # Keep the GitHub APP_URL from the template; override only if self-hosted.
    if download_url:
        content = re.sub(r'set "APP_URL=.*?"', f'set "APP_URL={download_url}"',
                         content, count=1)

    out_name = "CSP_Setup.bat"
    if key_row:
        content = re.sub(r'set "CSP_ID=.*?"', f'set "CSP_ID={csp_id}"', content, count=1)
        content = re.sub(r'set "API_KEY=.*?"', f'set "API_KEY={key_row["api_key"]}"',
                         content, count=1)
        out_name = f"CSP_Setup_{csp_id}.bat"

    return Response(content, mimetype="text/plain",
                    headers={"Content-Disposition": f"attachment; filename={out_name}"})


@ui_bp.route("/downloads/<int:release_id>/<path:filename>")
def download_release(release_id, filename):
    """PUBLIC — deliberately NOT behind login_required. A brand-new CSP has no
    API key yet (CSP_Setup.bat must fetch anonymously), and the CSP-side
    self-updater (core/updater.py) also needs a plain HTTP GET. The package
    itself carries no customer data (see MAKE_ZIP.ps1's exclusions), so public
    exposure here is the same trust level as a public GitHub release asset."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if not row:
        return ("not found", 404)
    return send_from_directory(RELEASES_DIR, row["stored_name"],
                               as_attachment=True, download_name=row["filename"])


@ui_bp.route("/ocr-log")
@login_required
def ocr_log():
    """Centralized-OCR sharing log — PII-FREE by construction.

    ocr_metrics stores ONLY operational facts about each OCR request (which CSP,
    when, file type, page/row COUNTS, latency, ok/error/busy) — never a
    filename, image, extracted text, or any customer identifier. This page is
    the audit/monitoring view of that: 'kis CSP ne kab kitni OCR bheji', with no
    way to see what was in any document."""
    with get_connection() as conn:
        recent = conn.execute(
            """SELECT request_id, csp_id, file_type, page_count, row_count,
                      latency_ms, status, error_class, created_at
               FROM ocr_metrics ORDER BY id DESC LIMIT 200""").fetchall()
        agg = conn.execute(
            """SELECT COUNT(*) requests,
                      COALESCE(SUM(page_count),0) pages,
                      COALESCE(SUM(row_count),0) rows,
                      COALESCE(SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END),0) ok,
                      COALESCE(SUM(CASE WHEN status='error' THEN 1 ELSE 0 END),0) errors,
                      COALESCE(SUM(CASE WHEN status='busy' THEN 1 ELSE 0 END),0) busy
               FROM ocr_metrics""").fetchone()
        per_csp = conn.execute(
            """SELECT csp_id, COUNT(*) requests,
                      COALESCE(SUM(page_count),0) pages,
                      COALESCE(SUM(row_count),0) rows
               FROM ocr_metrics GROUP BY csp_id ORDER BY requests DESC""").fetchall()
    return render_template("admin_ocr_log.html", recent=recent, agg=agg,
                           per_csp=per_csp)


@ui_bp.route("/whatsapp")
@login_required
def whatsapp_health():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM csps ORDER BY whatsapp_banned DESC, name").fetchall()
    csps = [dict(r, online=_is_online(r["last_seen"])) for r in rows]
    banned = sum(1 for c in csps if c["whatsapp_banned"])
    connected = sum(1 for c in csps if c["whatsapp_connected"])
    return render_template("admin_whatsapp.html", csps=csps, banned=banned,
                           connected=connected, total=len(csps))
