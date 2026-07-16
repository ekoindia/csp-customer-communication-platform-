"""
CSP-side reporter — pushes a small, PII-FREE heartbeat to Eko's admin portal.

Eko cannot reach into a CSP's local PC, so the CSP install reports OUTBOUND on a
timer. The payload is strictly allow-listed here (see build_payload). It carries:

  • this install's opaque id, CSP shop name, app version;
  • WhatsApp connected/banned flags;
  • the CSP MACHINE hardware profile (RAM/CPU/GPU/OS/OCR engine) — about the
    computer, not any customer;
  • AGGREGATE campaign progress: message-tracking counts (WA/SMS sent/delivered/
    read/failed) and physical-visit tracking counts (visited/in-progress/
    completed/closed), plus per-band category counts;
  • audit EVENT TYPES (login, upload, send ...) with timestamps.

It NEVER includes a customer name, mobile, account number, father name,
address, message text, or case id — not even masked. The aggregate numbers are
counts of cases in each state; they cannot be reversed into an individual.

Every count below is computed with the SAME SQL the CSP's own dashboard uses
(database/queries.batch_overview), so the admin's numbers match what the CSP
sees — just summed campaign-wide instead of per-batch.

Controlled by config.ADMIN_REPORT_ENABLED (default False -> does nothing).
"""
import threading
from datetime import datetime, timezone

import config


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _campaign_ids(conn) -> list:
    rows = conn.execute(
        "SELECT DISTINCT campaign_id FROM customer_cases ORDER BY campaign_id"
    ).fetchall()
    return [r["campaign_id"] for r in rows]


def _campaign_progress(conn, campaign_id: str) -> dict:
    """Full aggregate for one campaign — message tracking + visit tracking +
    band bars. Counts only; mirrors database/queries.batch_overview logic."""
    total = conn.execute(
        "SELECT COUNT(*) c FROM customer_cases WHERE campaign_id=?",
        (campaign_id,)).fetchone()["c"]

    reached = conn.execute(
        """SELECT COUNT(DISTINCT cc.case_id) c
           FROM customer_cases cc
           JOIN communication_attempts ca ON ca.case_id = cc.case_id
           WHERE cc.campaign_id=?
             AND ca.status IN ('wa_delivered','wa_read','sms_delivered')""",
        (campaign_id,)).fetchone()["c"]

    failed = conn.execute(
        """SELECT COUNT(DISTINCT cc.case_id) c
           FROM customer_cases cc
           WHERE cc.campaign_id=?
             AND cc.case_id IN (
                 SELECT case_id FROM communication_attempts WHERE status='sms_failed')""",
        (campaign_id,)).fetchone()["c"]

    # per-channel, per-status breakdown (distinct case per channel/status)
    rows = conn.execute(
        """SELECT ca.channel, ca.status, COUNT(DISTINCT ca.case_id) n
           FROM communication_attempts ca
           JOIN customer_cases cc ON cc.case_id = ca.case_id
           WHERE cc.campaign_id=?
           GROUP BY ca.channel, ca.status""",
        (campaign_id,)).fetchall()
    cnt = {(r["channel"], r["status"]): r["n"] for r in rows}
    wa_attempted = cnt.get(("whatsapp", "wa_attempted"), 0)
    wa_deliv = cnt.get(("whatsapp", "wa_delivered"), 0)
    wa_read = cnt.get(("whatsapp", "wa_read"), 0)
    wa_failed = cnt.get(("whatsapp", "wa_failed"), 0)
    sms_sent = cnt.get(("sms", "sms_sent"), 0)
    sms_deliv = cnt.get(("sms", "sms_delivered"), 0)
    sms_failed = cnt.get(("sms", "sms_failed"), 0)

    # physical-visit (business) tracking breakdown
    brows = conn.execute(
        """SELECT bt.status, COUNT(*) n
           FROM business_tracking bt
           JOIN customer_cases cc ON cc.case_id = bt.case_id
           WHERE cc.campaign_id=?
           GROUP BY bt.status""",
        (campaign_id,)).fetchall()
    b = {r["status"]: r["n"] for r in brows}
    visited = conn.execute(
        """SELECT COUNT(*) c FROM business_tracking bt
           JOIN customer_cases cc ON cc.case_id = bt.case_id
           WHERE cc.campaign_id=? AND bt.visited_at IS NOT NULL""",
        (campaign_id,)).fetchone()["c"]
    escalated = conn.execute(
        """SELECT COUNT(*) c FROM business_tracking bt
           JOIN customer_cases cc ON cc.case_id = bt.case_id
           WHERE cc.campaign_id=? AND bt.is_escalated=1""",
        (campaign_id,)).fetchone()["c"]

    # per-band category counts (band is a category, never a person)
    band_rows = conn.execute(
        """SELECT cc.band_label,
                  COUNT(*) total,
                  SUM(CASE WHEN ca.status IN
                      ('wa_delivered','wa_read','sms_delivered') THEN 1 ELSE 0 END) reached
           FROM customer_cases cc
           LEFT JOIN (
               SELECT case_id, status FROM communication_attempts
               WHERE id IN (SELECT MAX(id) FROM communication_attempts GROUP BY case_id)
           ) ca ON ca.case_id = cc.case_id
           WHERE cc.campaign_id=?
           GROUP BY cc.band_label ORDER BY cc.band_label""",
        (campaign_id,)).fetchall()
    bands = [{"band": r["band_label"], "total": r["total"] or 0,
              "reached": r["reached"] or 0} for r in band_rows]

    return {
        "campaign_id": campaign_id,
        "total": total, "reached": reached, "failed": failed,
        "pct": round(100.0 * reached / total, 1) if total else 0.0,
        # message tracking
        "wa_sent": wa_attempted + wa_deliv + wa_read,
        "wa_delivered": wa_deliv, "wa_read": wa_read, "wa_failed": wa_failed,
        "sms_sent": sms_sent, "sms_delivered": sms_deliv, "sms_failed": sms_failed,
        "escalated": escalated,
        # physical-visit tracking
        "visit_pending": b.get("customer_not_visited", 0),
        "visited": visited,
        "in_progress": b.get("customer_visited_in_progress", 0),
        "completed": b.get("process_completed", 0),
        "closed": b.get("case_closed", 0),
        "earnings": 0,          # placeholder until commission formula (EDR-1)
        "bands": bands,
    }


def _campaigns() -> list:
    """One aggregate rollup per campaign present in the local DB."""
    try:
        from database.db import get_connection
        with get_connection() as conn:
            return [_campaign_progress(conn, cid) for cid in _campaign_ids(conn)]
    except Exception:
        return []


def _audit_types(limit: int = 20) -> list:
    """Recent audit EVENT TYPES only (no detail, no customer reference)."""
    try:
        from database.db import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT action, created_at FROM audit_logs ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [{"type": r["action"], "ts": r["created_at"]} for r in rows]
    except Exception:
        return []


def _hardware() -> dict:
    """CSP MACHINE profile (about the computer, not any customer). Never raises."""
    out = {"ram_gb": None, "available_gb": None, "cpu_threads": None,
           "gpu": False, "os_name": "", "ocr_engine": ""}
    try:
        from core import hardware
        p = hardware.profile()
        out.update(ram_gb=p.get("ram_gb"), available_gb=p.get("available_gb"),
                   cpu_threads=p.get("cpu_threads"), ocr_engine=p.get("ocr_engine"),
                   gpu=hardware.has_nvidia_gpu())
    except Exception:
        pass
    try:
        import platform
        out["os_name"] = f"{platform.system()} {platform.release()}"
    except Exception:
        pass
    return out


def _dxdiag_cache_path() -> str:
    import os as _os
    return _os.path.join(_os.path.dirname(_os.path.abspath(config.DB_PATH)), "dxdiag.txt")


def _dxdiag() -> str:
    """Return the CACHED DxDiag text if it's been captured, else "" — this NEVER
    blocks. dxdiag itself is slow (~1-2 min on a 4 GB box), so it is captured once
    in a background thread (see _capture_dxdiag_async) and only READ here, so it
    never delays a heartbeat. An empty "" is COALESCE'd server-side (doesn't wipe
    a previously-sent value), so the machine report simply lands on a later beat."""
    import os as _os
    try:
        p = _dxdiag_cache_path()
        if _os.path.isfile(p) and _os.path.getsize(p) > 0:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return f.read()[:80000]
    except Exception:
        pass
    return ""


_dxdiag_capture_started = False


def _capture_dxdiag_async():
    """Capture DxDiag ONCE, in a daemon thread, off the report path. No-op if it
    is already cached or a capture is already running."""
    global _dxdiag_capture_started
    if _dxdiag_capture_started:
        return
    _dxdiag_capture_started = True
    import os as _os
    cache = _dxdiag_cache_path()
    if _os.path.isfile(cache) and _os.path.getsize(cache) > 0:
        return

    def _run():
        try:
            import subprocess
            import tempfile
            import time
            tmp = _os.path.join(tempfile.gettempdir(), "csp_dxdiag.txt")
            subprocess.run(["dxdiag", "/t", tmp], timeout=120, check=False)
            for _ in range(20):
                if _os.path.isfile(tmp) and _os.path.getsize(tmp) > 0:
                    break
                time.sleep(0.5)
            if _os.path.isfile(tmp):
                with open(tmp, "r", encoding="utf-8", errors="replace") as f:
                    txt = f.read()
                _os.makedirs(_os.path.dirname(cache), exist_ok=True)
                with open(cache, "w", encoding="utf-8") as f:
                    f.write(txt)
                try:
                    _os.remove(tmp)
                except Exception:
                    pass
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _whatsapp_status() -> dict:
    """Best-effort WhatsApp connected/banned flags (never raises)."""
    status = {"connected": False, "banned": False}
    try:
        import requests
        r = requests.get(f"{config.WA_SERVER_URL}/status", timeout=3)
        if r.ok:
            d = r.json()
            status["connected"] = bool(d.get("connected") or d.get("ready"))
            status["banned"] = bool(d.get("banned"))
    except Exception:
        pass
    return status


def _csp_name() -> str:
    """The CSP's REAL branch name from onboarding (branches table), not the
    config placeholder. Falls back to config.CSP_NAME. Never raises."""
    try:
        from core import settings
        n = (settings.get_csp_settings() or {}).get("csp_name")
        if n and str(n).strip():
            return str(n).strip()
    except Exception:
        pass
    return getattr(config, "CSP_NAME", "")


def build_payload() -> dict:
    """Assemble the allow-listed payload. This is the ONLY data that leaves the
    CSP PC. Every key here is operational or an aggregate count — never PII."""
    return {
        "csp_id": config.ADMIN_CSP_ID,
        "name": _csp_name(),              # REAL branch name (onboarding), not a person
        "install_id": config.ADMIN_CSP_ID,
        "version": getattr(config, "APP_VERSION", "0"),
        "month": _month(),
        "whatsapp": _whatsapp_status(),
        "hardware": _hardware(),
        "dxdiag": _dxdiag(),              # full machine system report (machine, not PII)
        "campaigns": _campaigns(),        # full message + visit tracking, per campaign
        "audit": _audit_types(),
    }


def _base() -> str:
    return getattr(config, "ADMIN_API_BASE", "").rstrip("/")


def report_once() -> dict:
    """POST status to the single Eko API ({base}/report). Never raises."""
    if not getattr(config, "ADMIN_REPORT_ENABLED", False):
        return {"ok": False, "error": "reporting disabled"}
    try:
        import requests
        r = requests.post(
            _base() + "/report",
            headers={"X-API-Key": config.ADMIN_API_KEY, "Content-Type": "application/json"},
            json=build_payload(), timeout=10)
        return {"ok": r.ok, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except (TypeError, ValueError):
        return ()


def _is_newer(remote: str, local: str) -> bool:
    rt, lt = _version_tuple(remote), _version_tuple(local)
    return bool(rt) and rt > lt


def sync_once() -> dict:
    """Poll the single Eko API ({base}/sync) for the latest version + any queued
    commands (Eko can't push to this local PC). When a NEWER version is published
    with a download URL, STAGE it (download + verify) so the launcher can apply it
    at the next start. Never raises."""
    if not getattr(config, "ADMIN_REPORT_ENABLED", False):
        return {"ok": False, "error": "reporting disabled"}
    try:
        import requests
        r = requests.get(
            _base() + "/sync",
            headers={"X-API-Key": config.ADMIN_API_KEY},
            params={"csp_id": config.ADMIN_CSP_ID}, timeout=10)
        data = r.json() if r.ok else {}
        latest = data.get("latest_version")
        staged = None
        local = getattr(config, "APP_VERSION", "0")
        if latest and _is_newer(latest, local):
            print(f"[admin-sync] a newer version is available: {latest} (have {local})")
            url = data.get("update_url")
            if url:
                from core import updater
                if updater.pending_version() == latest:
                    staged = latest  # already staged, waiting for restart
                else:
                    res = updater.stage_update(latest, url, data.get("update_sha256"))
                    if res.get("ok"):
                        staged = latest
                        print(f"[admin-sync] update {latest} staged; will apply on next start")
                    else:
                        print(f"[admin-sync] staging failed: {res.get('error')}")
        for cmd in data.get("commands", []):
            print(f"[admin-sync] command received: {cmd.get('command')}")
        return {"ok": r.ok, "latest_version": latest, "staged": staged,
                "commands": data.get("commands", [])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def start_background():
    """Start the periodic report+sync loop in a daemon thread (no-op if off)."""
    if not getattr(config, "ADMIN_REPORT_ENABLED", False):
        return
    _capture_dxdiag_async()   # machine report captured once, OFF the heartbeat path
    interval = max(60, int(getattr(config, "ADMIN_REPORT_INTERVAL_SEC", 300)))

    def _loop():
        report_once()
        sync_once()
        t = threading.Timer(interval, _loop)
        t.daemon = True
        t.start()

    threading.Timer(3, _loop).start()  # first beat almost immediately (no dxdiag wait)
