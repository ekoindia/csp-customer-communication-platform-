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
    csps = []
    for r in rows:
        d = dict(r)
        d["online"] = _is_online(r["last_seen"])
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
        prog = conn.execute(
            "SELECT * FROM progress WHERE csp_id=? ORDER BY month DESC, campaign_id",
            (csp_id,)).fetchall()
        band_rows = conn.execute(
            "SELECT * FROM progress_bands WHERE csp_id=? ORDER BY band", (csp_id,)
        ).fetchall()
        audit = conn.execute(
            "SELECT type, ts FROM audit WHERE csp_id=? ORDER BY id DESC LIMIT 50",
            (csp_id,)).fetchall()
    if not c:
        return "CSP not found", 404
    # group bands by (campaign_id, month) so each progress row shows its bars
    bands = {}
    for b in band_rows:
        bands.setdefault((b["campaign_id"], b["month"]), []).append(dict(b))
    prog = [dict(p, bands=bands.get((p["campaign_id"], p["month"]), [])) for p in prog]
    d = dict(c); d["online"] = _is_online(c["last_seen"])
    return render_template("admin_csp_detail.html", c=d, progress=prog, audit=audit)


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


@ui_bp.route("/setup", methods=["GET", "POST"])
@login_required
def setup_files():
    """Distribute the install package for BRAND-NEW CSPs. Upload the
    CSP_Platform.zip (built by MAKE_ZIP.ps1) once here; the admin portal then
    hosts it at a public download link (no API key exists yet for a CSP that
    hasn't installed anything) and can hand out a ready CSP_Setup.bat with
    that link already baked in — nothing to edit by hand per CSP."""
    if request.method == "POST":
        f = request.files.get("package")
        if not f or not f.filename:
            flash("Choose a .zip file first.")
            return redirect(url_for("admin_ui.setup_files"))
        version = request.form.get("version", "").strip()
        info = _save_release(f, "install", version)
        flash(f"Install package uploaded: {info['filename']} ({info['size'] // 1024} KB).")
        return redirect(url_for("admin_ui.setup_files"))

    with get_connection() as conn:
        latest = conn.execute(
            "SELECT * FROM releases WHERE kind='install' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        history = conn.execute(
            "SELECT * FROM releases WHERE kind='install' ORDER BY id DESC LIMIT 10"
        ).fetchall()
    download_url = None
    if latest:
        download_url = url_for("admin_ui.download_release", release_id=latest["id"],
                               filename=latest["filename"], _external=True)
    return render_template("admin_setup.html", latest=latest, history=history,
                           download_url=download_url)


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
    with get_connection() as conn:
        latest = conn.execute(
            "SELECT * FROM releases WHERE kind='install' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not latest:
            flash("Upload an install package first.")
            return redirect(url_for("admin_ui.setup_files"))

        csp_id = request.args.get("csp_id", "").strip()
        key_row = None
        if csp_id:
            key_row = conn.execute(
                "SELECT api_key FROM api_keys WHERE csp_id=? AND active=1", (csp_id,)
            ).fetchone()
            if not key_row:
                flash(f"No active API key for {csp_id} — issue one on the API Keys page first.")
                return redirect(url_for("admin_ui.api_keys"))

    download_url = url_for("admin_ui.download_release", release_id=latest["id"],
                           filename=latest["filename"], _external=True)
    template_path = os.path.join(os.path.dirname(_DIR), "csp_dashboard", "CSP_Setup.bat")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return ("CSP_Setup.bat template not found on the server.", 500)

    content, n = re.subn(r'set "APP_URL=.*?"', f'set "APP_URL={download_url}"',
                         content, count=1)
    if n == 0:
        return ("CSP_Setup.bat template is missing its APP_URL line.", 500)

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


@ui_bp.route("/updates", methods=["GET", "POST"])
@login_required
def updates():
    """Publish a new app version (URL + sha256) that every CSP picks up on its
    next /sync, and optionally push an 'update now' command to one/all CSPs.
    Eko can't reach into a CSP PC, so this is the publish side of pull-based
    updates. Two ways to publish: paste an externally-hosted URL + hash, or
    upload the .zip right here (sha256 computed automatically, hosted at the
    same public /downloads/ route used for install packages)."""
    from admin_dashboard.api import queue_command

    if request.method == "POST":
        act = request.form.get("action")

        if act == "publish_file":
            ver = request.form.get("version", "").strip()
            f = request.files.get("package")
            if not f or not f.filename:
                flash("Choose a .zip file first.")
                return redirect(url_for("admin_ui.updates"))
            info = _save_release(f, "update", ver)
            eff_ver = info["version"]
            if not eff_ver:
                flash("Could not determine a version: the .zip has no VERSION file "
                      "and no version was typed. CSPs cannot pick this up. Rebuild "
                      "the package (MAKE_ZIP.ps1 includes VERSION) or type a version.")
                return redirect(url_for("admin_ui.updates"))
            download_url = url_for("admin_ui.download_release", release_id=info["id"],
                                   filename=info["filename"], _external=True)
            with get_connection() as conn:
                for k, v in (("latest_version", eff_ver), ("update_url", download_url),
                             ("update_sha256", info["sha256"])):
                    conn.execute(
                        "INSERT INTO server_config (key,value) VALUES (?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
                conn.commit()
            note = "" if (ver in ("", eff_ver)) else f" (typed '{ver}' overridden by package's VERSION)"
            flash(f"Published version {eff_ver} from {info['filename']} "
                 f"(sha256 computed automatically){note}. CSPs will stage it on next sync.")
            return redirect(url_for("admin_ui.updates"))

        with get_connection() as conn:
            if act == "publish":
                ver = request.form.get("version", "").strip()
                url = request.form.get("update_url", "").strip()
                sha = request.form.get("update_sha256", "").strip()
                for k, v in (("latest_version", ver), ("update_url", url),
                             ("update_sha256", sha)):
                    conn.execute(
                        "INSERT INTO server_config (key,value) VALUES (?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
                conn.commit()
                flash(f"Published version {ver}. CSPs will stage it on next sync.")
            elif act == "push":
                target = request.form.get("target", "").strip()
                ids = ([target] if target and target != "__all__"
                       else [r["csp_id"] for r in conn.execute(
                           "SELECT csp_id FROM csps").fetchall()])
                for cid in ids:
                    queue_command(cid, "update_software",
                                  json.dumps({"note": "apply on next restart"}))
                flash(f"Queued 'update now' for {len(ids)} CSP(s).")
        return redirect(url_for("admin_ui.updates"))

    with get_connection() as conn:
        cfg = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM server_config").fetchall()}
        csps = conn.execute(
            "SELECT csp_id, name, version, last_seen FROM csps ORDER BY name, csp_id"
        ).fetchall()
    fleet = []
    for c in csps:
        d = dict(c)
        d["online"] = _is_online(c["last_seen"])
        d["current"] = (c["version"] == cfg.get("latest_version"))
        fleet.append(d)
    return render_template("admin_updates.html", cfg=cfg, fleet=fleet,
                           behind=sum(1 for c in fleet if not c["current"]))


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
