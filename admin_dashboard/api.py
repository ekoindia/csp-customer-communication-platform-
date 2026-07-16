"""
The single Eko API (v1) — the ONE interface between every CSP install and the
admin portal that lives on Eko's server.

  POST /api/v1/report   CSP -> Eko : PII-free heartbeat + status (see below)
  GET  /api/v1/sync     Eko -> CSP : latest version + queued commands (pull)
  GET  /api/v1          health / discovery

Security/DPDP: /report reads ONLY a fixed set of PII-free keys from the payload;
anything else in the body is ignored and never stored. There is NO code path and
NO column that can persist a customer name / mobile / account / address / message
text / case id here — not even masked. Everything stored is operational (about
the CSP machine/install) or an aggregate count (about a campaign).
"""
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

from admin_dashboard.db import get_connection

api_bp = Blueprint("admin_api", __name__)
API_VERSION = "v1"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_key(conn, csp_id, key):
    if not csp_id or not key:
        return False
    row = conn.execute(
        "SELECT api_key, active FROM api_keys WHERE csp_id=?", (csp_id,)
    ).fetchone()
    return bool(row) and row["active"] == 1 and row["api_key"] == key


def _i(d, k):
    """Coerce to int, defaulting to 0 — never trusts the wire type."""
    try:
        return int(d.get(k) or 0)
    except (TypeError, ValueError):
        return 0


def _f(d, k):
    try:
        return float(d.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


def queue_command(csp_id: str, command: str, payload: str = None):
    """Admin-side helper: queue a command for a CSP. It is delivered the next
    time that CSP polls /api/v1/sync (Eko cannot push to a local CSP PC)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO commands (csp_id, command, payload, status, created_at) "
            "VALUES (?,?,?, 'pending', ?)",
            (csp_id, command, payload, _now()))
        conn.commit()


@api_bp.route("/api/v1", methods=["GET"])
def api_root():
    """Health/discovery for the single Eko API."""
    return jsonify({"ok": True, "service": "eko-admin-api", "version": API_VERSION,
                    "endpoints": ["/api/v1/report (POST)", "/api/v1/sync (GET)"]})


@api_bp.route("/api/v1/sync", methods=["GET"])
def sync():
    """CSP polls this to receive server-side info it can't be pushed: the latest
    published version (for self-update) and any queued commands. PII-free."""
    key = request.headers.get("X-API-Key", "")
    csp_id = str(request.args.get("csp_id") or "").strip()
    with get_connection() as conn:
        if not _valid_key(conn, csp_id, key):
            return jsonify({"ok": False, "error": "invalid csp_id or API key"}), 401
        now = _now()
        conn.execute("UPDATE csps SET last_seen=? WHERE csp_id=?", (now, csp_id))
        cfg = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM server_config").fetchall()}
        cmds = conn.execute(
            "SELECT id, command, payload FROM commands WHERE csp_id=? AND status='pending' "
            "ORDER BY id", (csp_id,)).fetchall()
        cmd_list = [{"id": r["id"], "command": r["command"], "payload": r["payload"]}
                    for r in cmds]
        if cmd_list:
            conn.execute(
                "UPDATE commands SET status='delivered', delivered_at=? "
                "WHERE csp_id=? AND status='pending'", (now, csp_id))
        conn.commit()
    return jsonify({"ok": True, "server_time": now,
                    "latest_version": cfg.get("latest_version"),
                    # where the CSP fetches the update package + its hash to verify
                    "update_url": cfg.get("update_url") or None,
                    "update_sha256": cfg.get("update_sha256") or None,
                    "commands": cmd_list})


@api_bp.route("/api/v1/report", methods=["POST"])
def report():
    key = request.headers.get("X-API-Key", "")
    body = request.get_json(silent=True) or {}
    csp_id = str(body.get("csp_id") or "").strip()

    with get_connection() as conn:
        if not _valid_key(conn, csp_id, key):
            return jsonify({"ok": False, "error": "invalid csp_id or API key"}), 401

        now = _now()
        month = str(body.get("month") or "")[:7]

        # ---- csps (heartbeat) : allow-listed scalar fields only ------------
        name = str(body.get("name") or "")[:120]
        # The admin-set label (API Keys page) is AUTHORITATIVE for the CSP's
        # display name and overrides the self-reported one (which can be a config
        # placeholder like "Demo CSP" until branch onboarding). Fixing it here, at
        # the write, keeps EVERY admin page (Fleet/Earnings/Campaigns/WhatsApp)
        # consistent with no per-page changes.
        _label = conn.execute("SELECT name FROM api_keys WHERE csp_id=?", (csp_id,)).fetchone()
        if _label and (_label["name"] or "").strip():
            name = _label["name"].strip()[:120]
        version = str(body.get("version") or "")[:40]
        install_id = str(body.get("install_id") or csp_id)[:80]
        wa = body.get("whatsapp") or {}
        connected = 1 if wa.get("connected") else 0
        banned = 1 if wa.get("banned") else 0
        hw = body.get("hardware") or {}
        hw_ram = _f(hw, "ram_gb")
        hw_avail = _f(hw, "available_gb")
        hw_cpu = _i(hw, "cpu_threads")
        hw_gpu = 1 if hw.get("gpu") else 0
        os_name = str(hw.get("os_name") or "")[:60]
        ocr_engine = str(hw.get("ocr_engine") or "")[:20]
        # Full DxDiag machine report (machine/system info only, not customer PII).
        dxdiag = str(body.get("dxdiag") or "")[:120000]

        exists = conn.execute("SELECT 1 FROM csps WHERE csp_id=?", (csp_id,)).fetchone()
        if exists:
            conn.execute(
                """UPDATE csps SET name=?, version=?, install_id=?,
                       whatsapp_connected=?, whatsapp_banned=?,
                       hw_ram_gb=?, hw_available_gb=?, hw_cpu_threads=?, hw_gpu=?,
                       os_name=?, ocr_engine=?,
                       dxdiag=COALESCE(NULLIF(?, ''), dxdiag), last_seen=? WHERE csp_id=?""",
                (name, version, install_id, connected, banned, hw_ram, hw_avail,
                 hw_cpu, hw_gpu, os_name, ocr_engine, dxdiag, now, csp_id))
        else:
            conn.execute(
                """INSERT INTO csps (csp_id, name, version, install_id,
                       whatsapp_connected, whatsapp_banned, hw_ram_gb, hw_available_gb,
                       hw_cpu_threads, hw_gpu, os_name, ocr_engine, dxdiag, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (csp_id, name, version, install_id, connected, banned, hw_ram, hw_avail,
                 hw_cpu, hw_gpu, os_name, ocr_engine, dxdiag, now, now))

        # ---- progress : one row per campaign, AGGREGATE counts only --------
        # Accept the new `campaigns` list; also tolerate the older single
        # `progress` + top-level campaign_id shape for backward-compat.
        campaigns = body.get("campaigns")
        if not campaigns:
            pr = body.get("progress")
            if pr:
                pr = dict(pr)
                pr["campaign_id"] = body.get("campaign_id")
                campaigns = [pr]
        for camp in (campaigns or []):
            if not isinstance(camp, dict):
                continue
            campaign_id = str(camp.get("campaign_id") or "")[:80]
            conn.execute(
                """INSERT INTO progress (csp_id, campaign_id, month, total, reached,
                       failed, pct, wa_sent, wa_delivered, wa_read, wa_failed,
                       sms_sent, sms_delivered, sms_failed, escalated,
                       visit_pending, visited, in_progress, completed, closed,
                       earnings, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(csp_id, campaign_id, month) DO UPDATE SET
                       total=excluded.total, reached=excluded.reached,
                       failed=excluded.failed, pct=excluded.pct,
                       wa_sent=excluded.wa_sent, wa_delivered=excluded.wa_delivered,
                       wa_read=excluded.wa_read, wa_failed=excluded.wa_failed,
                       sms_sent=excluded.sms_sent, sms_delivered=excluded.sms_delivered,
                       sms_failed=excluded.sms_failed, escalated=excluded.escalated,
                       visit_pending=excluded.visit_pending, visited=excluded.visited,
                       in_progress=excluded.in_progress, completed=excluded.completed,
                       closed=excluded.closed, earnings=excluded.earnings,
                       updated_at=excluded.updated_at""",
                (csp_id, campaign_id, month, _i(camp, "total"), _i(camp, "reached"),
                 _i(camp, "failed"), _f(camp, "pct"), _i(camp, "wa_sent"),
                 _i(camp, "wa_delivered"), _i(camp, "wa_read"), _i(camp, "wa_failed"),
                 _i(camp, "sms_sent"), _i(camp, "sms_delivered"), _i(camp, "sms_failed"),
                 _i(camp, "escalated"), _i(camp, "visit_pending"), _i(camp, "visited"),
                 _i(camp, "in_progress"), _i(camp, "completed"), _i(camp, "closed"),
                 _f(camp, "earnings"), now))

            # per-band category counts (band = category, not a person)
            for bd in (camp.get("bands") or [])[:20]:
                if not isinstance(bd, dict):
                    continue
                band = str(bd.get("band") or "")[:40]
                if not band:
                    continue
                conn.execute(
                    """INSERT INTO progress_bands (csp_id, campaign_id, month, band,
                           total, reached, updated_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(csp_id, campaign_id, month, band) DO UPDATE SET
                           total=excluded.total, reached=excluded.reached,
                           updated_at=excluded.updated_at""",
                    (csp_id, campaign_id, month, band, _i(bd, "total"),
                     _i(bd, "reached"), now))

        # ---- audit : EVENT TYPES only (type + ts), capped ------------------
        for ev in (body.get("audit") or [])[:50]:
            etype = str((ev or {}).get("type") or "")[:60]
            ets = str((ev or {}).get("ts") or now)[:32]
            if etype:
                conn.execute(
                    "INSERT INTO audit (csp_id, type, ts) VALUES (?,?,?)",
                    (csp_id, etype, ets))
        conn.commit()
    return jsonify({"ok": True, "received_at": now})
