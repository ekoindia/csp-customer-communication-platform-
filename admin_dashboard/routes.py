"""Admin portal UI — multi-page, each page one clear job. Read-only monitoring
of the CSP fleet using only the allow-listed, PII-free data."""
import hashlib
import io
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

        # Bulk "start everyone fresh": revoke ALL keys at once. After this every
        # CSP install stops reporting/using OCR until re-issued a fresh key +
        # given a fresh CSP_Setup.bat. Deliberately requires no csp_id.
        if action == "revoke_all":
            with get_connection() as conn:
                n = conn.execute("SELECT COUNT(*) c FROM api_keys WHERE active=1").fetchone()["c"]
                conn.execute("UPDATE api_keys SET active=0")
                conn.commit()
            flash(f"Revoked ALL keys ({n} active). Every CSP is now disconnected "
                  f"— issue a fresh key + send a new CSP_Setup.bat to each.")
            return redirect(url_for("admin_ui.api_keys"))

        csp_id = request.form.get("csp_id", "").strip()
        if not csp_id:
            flash("CSP ID is required.")
            return redirect(url_for("admin_ui.api_keys"))

        # Permanently remove a CSP from EVERY admin table. Needed when a wrong /
        # mistyped CSP ID was created (e.g. "1AB50895" vs the real "1A850895") —
        # leaving it behind splits that CSP's fleet status, progress and earnings
        # across two rows forever. Only admin data is removed; no customer data
        # is held here at all. Table list is fixed in code (never user input).
        if action == "delete_csp":
            removed = 0
            with get_connection() as conn:
                for table in ("api_keys", "csps", "progress", "progress_bands",
                              "progress_categories", "progress_outcomes",
                              "audit", "update_events", "ocr_metrics", "commands"):
                    cur = conn.execute(f"DELETE FROM {table} WHERE csp_id=?", (csp_id,))
                    removed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                conn.commit()
            flash(f"Deleted CSP {csp_id} from all admin records ({removed} row(s) removed).")
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


# Commands an admin may queue for a CSP install. This list is for the UI only —
# the CSP side enforces its OWN copy of the allow-list (csp_dashboard/core/
# commands.py), so a compromised or buggy server can never make a CSP PC run
# something that isn't one of these fixed, code-defined actions.
FLEET_COMMANDS = [
    ("update_now",     "Update now",
     "Fetch the published version immediately instead of waiting for the next "
     "5-minute poll, then restart the app to apply it."),
    ("restart_app",    "Restart the app",
     "Restart the dashboard + WhatsApp bridge. Waits if a batch is mid-send."),
    ("selfheal",       "Run self-heal",
     "Run the full diagnose-and-repair pass (Node/Baileys/port/session/webhook)."),
    ("reset_whatsapp", "Reset WhatsApp session",
     "Clear a dead WhatsApp session so a fresh QR is generated. The CSP must "
     "scan the new QR — use only when the link is already broken."),
    ("send_report",    "Send a fresh report",
     "Push an immediate status heartbeat so this portal is up to date."),
]
_FLEET_COMMAND_NAMES = {c[0] for c in FLEET_COMMANDS}


def _cfg_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM server_config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _cfg_set(conn, key, value):
    conn.execute("INSERT INTO server_config (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))


def _vtuple(v):
    try:
        return tuple(int(x) for x in str(v or "").strip().split("."))
    except (TypeError, ValueError):
        return ()


def _next_version(*candidates) -> str:
    """Suggest the next publishable version: highest version seen anywhere
    (published, this checkout, or any CSP in the fleet) with the patch bumped.
    A published version MUST be strictly newer than what a CSP runs, or that CSP
    will never see it — so the default is always a real increment."""
    best = max((_vtuple(c) for c in candidates if _vtuple(c)), default=(1, 0, 0))
    best = best + (0,) * (3 - len(best)) if len(best) < 3 else best
    return ".".join(str(x) for x in (best[0], best[1], best[2] + 1))


def _unreachable_host(url: str) -> bool:
    """A published URL is fetched by a CSP PC across the internet, but it is built
    from the hostname THIS admin happens to be browsing with. If that is
    localhost, the fleet would download nothing and no CSP would ever update —
    worth saying out loud at publish time rather than discovering later."""
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|0\.0\.0\.0)(:|/|$)", url or ""))


def _publish_release(conn, release_id: int):
    """Point the fleet at an already-stored release: /sync starts advertising it,
    every CSP downloads + sha-verifies it on its next poll, and applies it on the
    next app start. Rolling back = publishing an older release row again."""
    row = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if not row:
        return None
    url = url_for("admin_ui.download_release", release_id=row["id"],
                  filename=row["filename"], _external=True)
    if _unreachable_host(url):
        flash(f"Warning: the package URL was built as {url} — a CSP PC cannot "
              f"reach that. Open this portal on the address CSPs use "
              f"(the same host as their OCR endpoint) and publish again.")
    _cfg_set(conn, "latest_version", row["version"])
    _cfg_set(conn, "update_url", url)
    _cfg_set(conn, "update_sha256", row["sha256"])
    _cfg_set(conn, "published_at", _now_iso())
    return row


def _queue_command(conn, csp_id: str, command: str, payload: str = None):
    conn.execute("INSERT INTO commands (csp_id, command, payload, status, created_at) "
                 "VALUES (?,?,?, 'pending', ?)",
                 (csp_id, command, payload, _now_iso()))


@ui_bp.route("/releases", methods=["GET", "POST"])
@login_required
def releases():
    """Publish a software update to the whole fleet — the one page that makes an
    update a one-click job HERE instead of a visit or a remote session THERE.

    How the update reaches a CSP PC (nothing is ever pushed — a CSP sits behind
    NAT with no inbound access):
      1. Publish here  -> server_config.latest_version / update_url / sha256.
      2. The CSP app polls /api/v1/sync (every 5 min), sees a newer version,
         downloads the pinned package and verifies its sha256, and stages it.
      3. It applies at the next app start (Windows won't let a running process
         overwrite its own files), and 'update_now' below makes that restart
         happen straight away instead of at the CSP's next open.
    The package is built from THIS server's checkout, so 'git push' + publish is
    the whole release process."""
    if request.method == "POST":
        action = request.form.get("action")

        if action == "publish_current":
            version = (request.form.get("version") or "").strip()
            if not _vtuple(version):
                flash("Version must be numeric like 1.0.1 — CSPs compare it "
                      "number-by-number to decide what is newer.")
                return redirect(url_for("admin_ui.releases"))
            data = build_csp_app_zip(version)
            if data is None:
                flash("This server has no csp_dashboard/ checkout to package.")
                return redirect(url_for("admin_ui.releases"))
            os.makedirs(RELEASES_DIR, exist_ok=True)
            filename = f"csp_app_{version}.zip"
            stored = f"{uuid.uuid4().hex}_{filename}"
            with open(os.path.join(RELEASES_DIR, stored), "wb") as f:
                f.write(data)
            digest = hashlib.sha256(data).hexdigest()
            with get_connection() as conn:
                cur = conn.execute(
                    """INSERT INTO releases (kind, version, filename, stored_name,
                           sha256, size_bytes, uploaded_at)
                       VALUES ('update',?,?,?,?,?,?)""",
                    (version, filename, stored, digest, len(data), _now_iso()))
                rid = cur.lastrowid
                _publish_release(conn, rid)
                pushed = 0
                if request.form.get("push_now"):
                    for r in conn.execute(
                            "SELECT csp_id FROM api_keys WHERE active=1").fetchall():
                        _queue_command(conn, r["csp_id"], "update_now")
                        pushed += 1
                conn.commit()
            flash(f"Published v{version} ({len(data) >> 10} KB)." +
                  (f" {pushed} CSP(s) told to update immediately — they will "
                   f"fetch it within ~5 minutes and restart themselves."
                   if pushed else
                   " CSPs will pick it up on their next poll and apply it at "
                   "their next app start."))
            return redirect(url_for("admin_ui.releases"))

        if action == "republish":
            with get_connection() as conn:
                row = _publish_release(conn, int(request.form.get("release_id", 0)))
                pushed = 0
                if row and request.form.get("push_now"):
                    for r in conn.execute(
                            "SELECT csp_id FROM api_keys WHERE active=1").fetchall():
                        _queue_command(conn, r["csp_id"], "update_now")
                        pushed += 1
                conn.commit()
            flash(f"Now publishing v{row['version']} again."
                  if row else "That release no longer exists.")
            return redirect(url_for("admin_ui.releases"))

        if action == "command":
            cmd = (request.form.get("command") or "").strip()
            target = (request.form.get("csp_id") or "").strip()
            if cmd not in _FLEET_COMMAND_NAMES:
                flash("Unknown command.")
                return redirect(url_for("admin_ui.releases"))
            with get_connection() as conn:
                if target:
                    _queue_command(conn, target, cmd)
                    n = 1
                else:
                    rows = conn.execute(
                        "SELECT csp_id FROM api_keys WHERE active=1").fetchall()
                    for r in rows:
                        _queue_command(conn, r["csp_id"], cmd)
                    n = len(rows)
                conn.commit()
            flash(f"Queued '{cmd}' for {n} CSP(s). It runs when each one next "
                  f"polls (within ~5 minutes) — nothing for the CSP to do.")
            return redirect(url_for("admin_ui.releases"))

    with get_connection() as conn:
        published = {"version": _cfg_get(conn, "latest_version"),
                     "url": _cfg_get(conn, "update_url"),
                     "sha256": _cfg_get(conn, "update_sha256"),
                     "at": _cfg_get(conn, "published_at")}
        rel_rows = conn.execute(
            "SELECT * FROM releases ORDER BY id DESC LIMIT 25").fetchall()
        fleet = conn.execute(
            """SELECT k.csp_id, c.name, c.version, c.last_seen
                 FROM api_keys k LEFT JOIN csps c ON c.csp_id = k.csp_id
                WHERE k.active=1 ORDER BY k.csp_id""").fetchall()
        cmd_rows = conn.execute(
            "SELECT * FROM commands ORDER BY id DESC LIMIT 40").fetchall()
    code_version = server_code_version()
    fleet_view = [{"csp_id": r["csp_id"], "name": r["name"] or "",
                   "version": r["version"] or "—", "last_seen": r["last_seen"],
                   "online": _is_online(r["last_seen"]),
                   "current": bool(published["version"]
                                   and r["version"] == published["version"])}
                  for r in fleet]
    on_latest = sum(1 for f in fleet_view if f["current"])
    suggested = _next_version(published["version"], code_version,
                              *[f["version"] for f in fleet_view])
    return render_template("admin_releases.html", published=published,
                           releases=rel_rows, fleet=fleet_view,
                           on_latest=on_latest, code_version=code_version,
                           suggested=suggested, commands=cmd_rows,
                           fleet_commands=FLEET_COMMANDS)


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
                      SUM(visit_not_started) visit_not_started,
                      SUM(visit_pending) visit_pending, SUM(visited) visited,
                      SUM(in_progress) in_progress, SUM(completed) completed,
                      SUM(closed) closed,
                      SUM(with_mobile) with_mobile, SUM(no_mobile) no_mobile,
                      SUM(not_on_whatsapp) not_on_whatsapp
               FROM progress GROUP BY campaign_id, month
               ORDER BY month DESC, campaign_id""").fetchall()
        band_rows = conn.execute(
            """SELECT campaign_id, month, band,
                      SUM(total) total, SUM(reached) reached
               FROM progress_bands GROUP BY campaign_id, month, band
               ORDER BY band""").fetchall()
        # How many CSPs actually HAVE this category in their bank list. Balance
        # band is a property of one particular list format (Alamgir's has it,
        # Sanjeev's and Rajan's do not) — without this, a band chart built from
        # one CSP silently reads as if it described the whole campaign.
        band_cov = conn.execute(
            """SELECT campaign_id, month, COUNT(DISTINCT csp_id) n
               FROM progress_bands
               WHERE COALESCE(NULLIF(TRIM(band),''),'NA') NOT IN ('NA','?')
               GROUP BY campaign_id, month""").fetchall()
        band_csps = {(b["campaign_id"], b["month"]): b["n"] for b in band_cov}
        # GENERIC category groupings, with the dimension NAME carried through, so
        # the page renders whatever dimensions the fleet's lists actually have
        # (band / village / taluka) instead of a hard-coded band chart. `csps`
        # per dimension shows how much of the campaign each grouping describes.
        cat_rows = conn.execute(
            """SELECT campaign_id, month, dimension, value,
                      SUM(total) total, SUM(reached) reached,
                      COUNT(DISTINCT csp_id) csps
               FROM progress_categories
               GROUP BY campaign_id, month, dimension, value
               ORDER BY dimension, total DESC""").fetchall()
        cat_cov = conn.execute(
            """SELECT campaign_id, month, dimension, COUNT(DISTINCT csp_id) n
               FROM progress_categories GROUP BY campaign_id, month, dimension"""
        ).fetchall()
        cat_csps = {(c["campaign_id"], c["month"], c["dimension"]): c["n"]
                    for c in cat_cov}
        # COVERAGE of the newer per-case metrics. A CSP still running an older
        # build doesn't report contactability/outcomes at all, so these panels can
        # describe only PART of the campaign — showing them without saying so made
        # 37 of 87 cases look like the whole picture.
        contact_cov = conn.execute(
            """SELECT campaign_id, month,
                      COUNT(DISTINCT CASE WHEN (with_mobile + no_mobile) > 0
                                          THEN csp_id END) csps,
                      SUM(with_mobile + no_mobile) cases
               FROM progress GROUP BY campaign_id, month""").fetchall()
        contact_cov = {(c["campaign_id"], c["month"]):
                       {"csps": c["csps"] or 0, "cases": c["cases"] or 0}
                       for c in contact_cov}
        outcome_cov = conn.execute(
            """SELECT campaign_id, month, COUNT(DISTINCT csp_id) csps,
                      SUM(count) cases
               FROM progress_outcomes GROUP BY campaign_id, month""").fetchall()
        outcome_cov = {(o["campaign_id"], o["month"]):
                       {"csps": o["csps"] or 0, "cases": o["cases"] or 0}
                       for o in outcome_cov}
        # Format-INDEPENDENT breakdown: balance band exists only in some bank
        # lists, so outcomes are what let Eko compare CSPs on the same footing.
        outcome_rows = conn.execute(
            """SELECT campaign_id, month, outcome, SUM(count) n
               FROM progress_outcomes GROUP BY campaign_id, month, outcome
               ORDER BY n DESC""").fetchall()
        csp_rows = conn.execute(
            """SELECT p.campaign_id, p.month, p.csp_id, c.name AS csp_name,
                      p.total, p.reached, p.failed, p.pct,
                      p.wa_delivered, p.wa_read, p.sms_delivered, p.escalated,
                      p.visited, p.closed
               FROM progress p LEFT JOIN csps c ON c.csp_id = p.csp_id
               ORDER BY p.campaign_id, p.month, c.name, p.csp_id""").fetchall()
    _OUTCOME_LABELS = {
        "reactivated": "Account reactivated / KYC done",
        "visited_pending": "Customer came, process pending",
        "deceased": "Account holder has died",
        "moved_away": "Moved away / not traceable",
        "wrong_contact": "Wrong or unreachable contact",
        "refused": "Customer not willing",
        "account_closed": "Account already closed",
        "other": "Other",
        "not_recorded": "No outcome recorded yet",
    }
    bands = {}
    for b in band_rows:
        bands.setdefault((b["campaign_id"], b["month"]), []).append(dict(b))
    outcomes = {}
    for o in outcome_rows:
        d = dict(o)
        d["label"] = _OUTCOME_LABELS.get(d["outcome"], d["outcome"])
        outcomes.setdefault((o["campaign_id"], o["month"]), []).append(d)
    # dimension -> friendly title (unknown dimensions fall back to a tidied name,
    # so a NEW dimension added later renders without touching this code)
    _DIM_LABELS = {"village": "Village", "taluka": "Taluka / block"}
    # Balance band is NOT shown fleet-wide: only some bank lists have that column,
    # so the chart described a single CSP while looking like the whole campaign and
    # parked everyone else in a meaningless "no band in list" bar. Per-CSP band
    # detail still exists in that CSP's own dashboard.
    _HIDDEN_DIMS = {"band_label"}
    cats = {}
    for c in cat_rows:
        key = (c["campaign_id"], c["month"])
        dim = c["dimension"]
        if dim in _HIDDEN_DIMS:
            continue
        entry = cats.setdefault(key, {}).setdefault(dim, {
            "dimension": dim,
            "label": _DIM_LABELS.get(dim, dim.replace("_", " ").title()),
            "csps": cat_csps.get((c["campaign_id"], c["month"], dim), 0),
            "values": [],
        })
        entry["values"].append(dict(c))
    per_csp = {}
    for c in csp_rows:
        per_csp.setdefault((c["campaign_id"], c["month"]), []).append(dict(c))
    data = []
    for r in rows:
        d = dict(r)
        d["reach_rate"] = round(100.0 * (d["reached"] or 0) / d["total"], 1) if d["total"] else 0.0
        d["bands"] = bands.get((r["campaign_id"], r["month"]), [])
        # "?" / "" / "NA" means that CSP's bank list simply has no balance-band
        # column — say so instead of showing a mystery bucket.
        for b in d["bands"]:
            if (b.get("band") or "").strip() in ("", "?", "NA"):
                b["band"] = "no band in list"
        # Coverage: this category exists only in SOME lists, so state how many of
        # the campaign's CSPs it actually describes.
        d["band_csps"] = band_csps.get((r["campaign_id"], r["month"]), 0)
        d["outcomes"] = outcomes.get((r["campaign_id"], r["month"]), [])
        d["contact_cov"] = contact_cov.get((r["campaign_id"], r["month"]),
                                           {"csps": 0, "cases": 0})
        d["outcome_cov"] = outcome_cov.get((r["campaign_id"], r["month"]),
                                           {"csps": 0, "cases": 0})
        # Dynamic groupings actually present in the fleet's data for this campaign.
        d["categories"] = list(cats.get((r["campaign_id"], r["month"]), {}).values())
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


# Dirs/files never shipped to a CSP (server-only, dev-only, secrets, or data).
# core/models is the big one: ~87 MB of OCR weights the CSP does NOT need
# (OCR runs on THIS server), and shipping them is what made the GitHub
# whole-repo zip ~83 MB and unreliable to download on CSP networks.
_CSP_ZIP_EXCLUDE_DIRS = {
    "tests", "scripts", "__pycache__", ".venv", ".venv_onnxtr",
    ".pytest_cache", ".git", ".wa_session", "uploads", "node_modules", "drafts",
}
_CSP_ZIP_EXCLUDE_NESTED = {"core/models"}
_CSP_ZIP_EXCLUDE_FILES = {".env", "secret.key", "pii.key", "csp_platform.db"}
_CSP_ZIP_EXCLUDE_EXT = {".pyc", ".db"}

CSP_APP_DIR = os.path.join(os.path.dirname(_DIR), "csp_dashboard")


def build_csp_app_zip(version: str = None):
    """Build the slim CSP app package from THIS server's own checkout and return
    it as bytes (None if the checkout is missing).

    Everything lands under a single `csp_dashboard/` folder, which is what both
    consumers expect: CSP_Setup.bat for a fresh install, and the CSP-side
    updater, whose _zip_root() unwraps a single top folder and stages its
    contents as the app root.

    `version`, when given, OVERWRITES the VERSION file inside the package. That
    file is the one thing an update hinges on — config.APP_VERSION reads it, the
    CSP reports it on every heartbeat, and /sync compares it — so stamping it at
    publish time is what makes "publish 1.0.1" actually reach the fleet, instead
    of depending on someone having remembered to bump VERSION in git first."""
    if not os.path.isdir(CSP_APP_DIR):
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(CSP_APP_DIR):
            rel = os.path.relpath(root, CSP_APP_DIR).replace("\\", "/")
            kept = []
            for d in dirs:
                child = d if rel == "." else rel + "/" + d
                if d in _CSP_ZIP_EXCLUDE_DIRS or child in _CSP_ZIP_EXCLUDE_NESTED:
                    continue
                kept.append(d)
            dirs[:] = kept
            for f in files:
                if f in _CSP_ZIP_EXCLUDE_FILES or os.path.splitext(f)[1] in _CSP_ZIP_EXCLUDE_EXT:
                    continue
                if version and rel == "." and f == "VERSION":
                    continue                      # replaced below with the published one
                full = os.path.join(root, f)
                arc = "csp_dashboard/" + os.path.relpath(full, CSP_APP_DIR).replace("\\", "/")
                z.write(full, arc)
        if version:
            z.writestr("csp_dashboard/VERSION", version.strip() + "\n")
    return buf.getvalue()


def server_code_version() -> str:
    """The VERSION currently in this server's checkout (the code that would be
    published right now)."""
    try:
        with open(os.path.join(CSP_APP_DIR, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


@ui_bp.route("/download/csp_app.zip")
def download_csp_app():
    """PUBLIC: serve a SLIM (~2-3 MB) zip of the CSP app, built on the fly from
    THIS server's own checkout, with the server-only OCR models and all dev/secret
    files stripped out.

    Why: CSP_Setup.bat used to pull the ~83 MB GitHub whole-repo zip, most of
    which is OCR model weights the CSP never needs (OCR runs on this server). On
    CSP networks that large download from codeload.github.com frequently failed.
    A small package served from THIS host — the same one the CSP already reaches
    for /api/v1/ocr/extract — downloads fast and reliably. Not behind
    login_required: a brand-new CSP has no key yet, and the package carries no
    customer data."""
    data = build_csp_app_zip()
    if data is None:
        return ("CSP app not found on this server.", 500)
    return Response(data, mimetype="application/zip",
                    headers={"Content-Disposition": "attachment; filename=csp_app.zip",
                             "Content-Length": str(len(data))})


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
