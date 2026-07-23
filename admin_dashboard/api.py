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
import base64
import hmac
import io
import threading
import time
import uuid

from flask import Blueprint, request, jsonify

from admin_dashboard.db import get_connection

# NOTE: the OCR stack (cryptography / numpy / opencv / pypdfium2 / onnxtr) is
# imported LAZILY inside ocr_extract(), never at module load. The admin portal
# must boot on `flask` alone so the fleet-heartbeat endpoints (/report, /sync)
# can NEVER be taken down by a missing or broken OCR dependency on the shared
# server. If the OCR stack is absent, /ocr/extract returns a clean 503 and the
# rest of the portal is unaffected.

api_bp = Blueprint("admin_api", __name__)
API_VERSION = "v1"

# Bounds simultaneous OCR jobs so a burst can't exhaust RAM on the shared box
# and starve the heartbeat endpoints. Built once, lazily (needs config).
_OCR_SEMAPHORE = None
_OCR_SEM_LOCK = threading.Lock()


def _ocr_semaphore():
    global _OCR_SEMAPHORE
    if _OCR_SEMAPHORE is None:
        with _OCR_SEM_LOCK:
            if _OCR_SEMAPHORE is None:
                import config
                n = max(1, int(getattr(config, "SERVER_OCR_MAX_CONCURRENCY", 2)))
                _OCR_SEMAPHORE = threading.BoundedSemaphore(n)
    return _OCR_SEMAPHORE


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_key(conn, csp_id, key):
    if not csp_id or not key:
        return False
    row = conn.execute(
        "SELECT api_key, active FROM api_keys WHERE csp_id=?", (csp_id,)
    ).fetchone()
    # Constant-time compare: never leak how many leading characters of the key
    # matched via response timing. Applies to /report and /sync too.
    return (bool(row) and row["active"] == 1
            and hmac.compare_digest(str(row["api_key"]), str(key)))


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


def _record_ocr_metric(conn, request_id: str, csp_id: str, file_type: str,
                       page_count: int, row_count: int, latency_ms: int,
                       status: str, error_class: str = None):
    conn.execute(
        """INSERT INTO ocr_metrics (request_id, csp_id, file_type, page_count,
               row_count, latency_ms, status, error_class, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (request_id, csp_id, file_type, int(page_count or 0), int(row_count or 0),
         int(latency_ms or 0), status, (error_class or "")[:80], _now()))


def _b64_file(payload: dict) -> bytes:
    try:
        return base64.b64decode(str(payload.get("file_b64") or "").encode("ascii"),
                                validate=True)
    except Exception as e:
        raise ValueError("bad file payload") from e


def _release_ocr_memory():
    """Best-effort memory reclaim after each OCR request. Never raises."""
    try:
        import gc
        gc.collect()
    except Exception:
        pass


def _extract_ocr_rows(file_bytes: bytes, file_type: str, page_from=None,
                      page_to=None) -> tuple[list, int]:
    """Run OCR from bytes only. Returns (rows, page_count). No disk writes."""
    import config
    from core import ocr_table

    engine = str(getattr(config, "SERVER_OCR_ENGINE", "onnxtr") or "onnxtr").lower()
    allowed = {"rapidocr", "onnxtr", "doctr", "paddle", "tesseract"}
    if engine not in allowed:
        raise ValueError("unsupported SERVER_OCR_ENGINE")
    if engine == "onnxtr" and not ocr_table.onnxtr_available():
        raise RuntimeError("SERVER_OCR_ENGINE=onnxtr but OnnxTR/models are not installed")

    old_override = getattr(ocr_table, "_ENGINE_OVERRIDE", None)
    old_strict = getattr(ocr_table, "_STRICT_ENGINE", False)
    old_engine = getattr(config, "OCR_ENGINE", "auto")
    ocr_table._ENGINE_OVERRIDE = engine
    ocr_table._STRICT_ENGINE = True
    config.OCR_ENGINE = engine
    # Engine default on the server is the BUNDLED onnxtr models (db_mobilenet +
    # crnn_mobilenet): committed in the repo, so they need NO runtime download
    # and work offline — proven on the real SBI scans (account 100 / name 99 /
    # band 95 / mobile 85 %). The heavier arches (db_resnet50 + parseq) are
    # opt-in via OCR_ONNXTR_HEAVY=1, because they fetch weights on first use and
    # a server without outbound access to the model host would otherwise get
    # zero rows. When heavy IS enabled, _onnxtr_model falls back to the bundled
    # models if the download fails, so OCR never silently returns nothing.
    try:
        return _extract_ocr_rows_with_engine(file_bytes, file_type, page_from, page_to)
    finally:
        ocr_table._ENGINE_OVERRIDE = old_override
        ocr_table._STRICT_ENGINE = old_strict
        config.OCR_ENGINE = old_engine


def _extract_ocr_rows_with_engine(file_bytes: bytes, file_type: str, page_from=None,
                                  page_to=None) -> tuple[list, int]:
    """OCR implementation after the server engine has been pinned."""
    if file_type == "image":
        from PIL import Image
        from core.ocr_table import extract_rows_from_pil
        img = Image.open(io.BytesIO(file_bytes))
        try:
            return extract_rows_from_pil(img), 1
        finally:
            try:
                img.close()
            except Exception:
                pass

    if file_type != "pdf":
        raise ValueError("unsupported OCR file_type")

    import gc
    import pypdfium2 as pdfium
    from core.ocr_table import extract_rows_from_pil

    rows = []
    pdf = pdfium.PdfDocument(file_bytes)
    try:
        total = len(pdf)
        lo = max(1, int(page_from or 1))
        hi = min(total, int(page_to or total))
        if lo > hi:
            lo, hi = 1, total
        dpi = int(getattr(config, "SERVER_OCR_RENDER_DPI", 300))
        scale = dpi / 72
        for pno in range(lo - 1, hi):
            page = pdf[pno]
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil()
            try:
                rows.extend(extract_rows_from_pil(pil))
            finally:
                try:
                    pil.close()
                except Exception:
                    pass
                try:
                    bitmap.close()
                    page.close()
                except Exception:
                    pass
                gc.collect()
        return rows, hi - lo + 1
    finally:
        pdf.close()
        # NB: we deliberately do NOT release the OCR model here. On the 128 GB
        # server the model stays resident between requests (the heavy arches
        # cost ~15 s to load), which is the right trade — unlike the 4 GB CSP
        # box, which frees it after each batch. Per-request page buffers are
        # freed above; the model is process-lifetime.


@api_bp.route("/api/v1", methods=["GET"])
def api_root():
    """Health/discovery for the single Eko API."""
    return jsonify({"ok": True, "service": "eko-admin-api", "version": API_VERSION,
                    "endpoints": ["/api/v1/report (POST)", "/api/v1/sync (GET)",
                                  "/api/v1/ocr/extract (POST)"]})


@api_bp.route("/api/v1/ocr/extract", methods=["POST"])
def ocr_extract():
    """Centralized OCR Tier 1.

    Auth: same per-CSP API key as /report and /sync (constant-time compare).
    Request/response: AES-GCM envelope (core.ocr_envelope). The response body is
    an encrypted, in-memory .xlsx (xlsx_b64) — never rows in the clear, never a
    file on disk. Persistence: metrics only (core.ocr_metrics) — never
    filenames, images, text, or extracted rows.

    The whole OCR stack is imported HERE, lazily: if any dependency is missing
    or broken on this server, only this endpoint degrades (clean 503); the fleet
    heartbeat endpoints (/report, /sync) are never affected.
    """
    try:
        from core.ocr_envelope import EnvelopeError, decrypt_json, encrypt_json
        from core.ocr_excel import rows_to_xlsx_bytes
    except Exception:  # ImportError or a broken transitive dep — never fatal
        return jsonify({"ok": False, "error": "ocr_unavailable"}), 503

    key = request.headers.get("X-API-Key", "")
    body = request.get_json(silent=True) or {}
    csp_id = str(body.get("csp_id") or "").strip()

    started = time.monotonic()
    request_id = uuid.uuid4().hex
    with get_connection() as conn:
        if not _valid_key(conn, csp_id, key):
            return jsonify({"ok": False, "error": "invalid csp_id or API key"}), 401

        # Decrypt (cheap) BEFORE taking an OCR slot, so a malformed payload can
        # never occupy scarce compute capacity.
        try:
            payload = decrypt_json(body.get("payload"), key)
        except EnvelopeError as e:
            _record_ocr_metric(conn, request_id, csp_id, "", 0, 0,
                               int((time.monotonic() - started) * 1000),
                               "error", e.__class__.__name__)
            conn.commit()
            return jsonify({"ok": False, "error": "bad encrypted payload"}), 400

        request_id = str(payload.get("request_id") or request_id)[:64]
        file_type = str(payload.get("file_type") or "").lower()[:20]

        # Bounded concurrency: if the box is already at capacity, don't queue
        # (which would pile up threads + RAM) — tell the client to back off. The
        # client falls back to local OCR, so no request is ever lost.
        sem = _ocr_semaphore()
        if not sem.acquire(blocking=False):
            _record_ocr_metric(conn, request_id, csp_id, file_type, 0, 0,
                               int((time.monotonic() - started) * 1000),
                               "busy", "AtCapacity")
            conn.commit()
            return jsonify({"ok": False, "error": "ocr_busy"}), 503

        try:
            import config
            max_mb = int(getattr(config, "SERVER_OCR_MAX_MB", 100))
            file_bytes = _b64_file(payload)
            if len(file_bytes) > max_mb * 1024 * 1024:
                raise ValueError("file too large")
            rows, pages = _extract_ocr_rows(
                file_bytes, file_type,
                page_from=payload.get("page_from"),
                page_to=payload.get("page_to"),
            )
            # Serialize to .xlsx entirely in RAM (all-text cells -> lossless).
            xlsx_b64 = base64.b64encode(rows_to_xlsx_bytes(rows)).decode("ascii")
            latency_ms = int((time.monotonic() - started) * 1000)
            _record_ocr_metric(conn, request_id, csp_id, file_type, pages,
                               len(rows), latency_ms, "ok")
            conn.commit()
            encrypted = encrypt_json({
                "request_id": request_id,
                "xlsx_b64": xlsx_b64,
                "page_count": pages,
                "row_count": len(rows),
            }, key)
            return jsonify({"ok": True, "payload": encrypted})
        except Exception as e:  # noqa: BLE001 - return a clean OCR failure
            latency_ms = int((time.monotonic() - started) * 1000)
            _record_ocr_metric(conn, request_id, csp_id, file_type, 0, 0,
                               latency_ms, "error", e.__class__.__name__)
            conn.commit()
            return jsonify({"ok": False, "error": "OCR failed"}), 500
        finally:
            # Free the OCR slot AND the model/image memory for this request.
            sem.release()
            _release_ocr_memory()


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

        exists = conn.execute("SELECT version FROM csps WHERE csp_id=?", (csp_id,)).fetchone()
        # Record a software update: the reported version changed since last beat
        # (the CSP ran UPDATE.bat and picked up a new build). Software-only info.
        if exists and version and (exists["version"] or "") != version:
            conn.execute(
                "INSERT INTO update_events (csp_id, from_version, to_version, ts) "
                "VALUES (?,?,?,?)", (csp_id, exists["version"] or "", version, now))
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
