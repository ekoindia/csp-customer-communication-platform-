"""
CSP-side client for Eko centralized OCR — PER-PAGE CHUNKED.

Why per-page: a full multi-page scan OCR'd in ONE request takes minutes on the
CPU server, which blows past the HTTP/nginx timeout and the result never gets
delivered. So instead we render the PDF locally and send ONE PAGE AT A TIME —
each request is short (tens of seconds) and small, well under every limit. The
per-page rows are combined into a single in-memory .xlsx and handed to the
existing draft/review pipeline (parsed in memory, never written to disk). The
CSP still sees the same review gate; nothing is auto-created.

A per-page callback drives a real progress bar ("page 5/29"), so the CSP sees
steady progress instead of one long silent wait.
"""
import base64
import io
import time
import uuid

import requests

import config
from core import admin_api_base
from core.ocr_envelope import EnvelopeError, decrypt_json, encrypt_json


class ServerOcrError(Exception):
    """Centralized OCR is unavailable or returned an invalid result."""


class _Retryable(Exception):
    """A transient failure worth retrying before giving up to local OCR."""


def enabled() -> bool:
    return bool(getattr(config, "SERVER_OCR_ENABLED", False)
                and admin_api_base.get()
                and getattr(config, "ADMIN_CSP_ID", "")
                and getattr(config, "ADMIN_API_KEY", "")
                and config.ADMIN_API_KEY != "demo-key-CSP001")


def _endpoint() -> str:
    return admin_api_base.get() + "/ocr/extract"


def check_connection(timeout: int = 8) -> dict:
    """Proactively self-diagnose the Eko server link — WITHOUT needing an upload.

    Returns a dict the dashboard shows as a live status chip:
      {ok, status, reason, fix, key_last4, base}
    status is one of: connected | off | not_configured | unreachable | bad_key |
    server_error. `ok` is True only for 'connected'. This is what lets the CSP
    (and Eko) see 'Connected / OCR ready' or the EXACT problem + fix at a glance,
    instead of finding out only when a scan silently yields 0 rows."""
    base = admin_api_base.get()
    csp_id = getattr(config, "ADMIN_CSP_ID", "") or ""
    key = getattr(config, "ADMIN_API_KEY", "") or ""
    out = {"ok": False, "status": "off", "reason": "", "fix": "",
           "key_last4": (key[-4:] if key else ""), "base": base}
    if not getattr(config, "SERVER_OCR_ENABLED", False):
        out.update(status="off",
                   reason="Server OCR is turned OFF for this install.",
                   fix="Re-run this CSP's CSP_Setup.bat (it turns it on).")
        return out
    if not base or not csp_id or not key or key == "demo-key-CSP001":
        missing = []
        if not base:
            missing.append("server address")
        if not csp_id:
            missing.append("CSP ID")
        if not key or key == "demo-key-CSP001":
            missing.append("API key")
        out.update(status="not_configured",
                   reason="Eko connection not configured (" + ", ".join(missing) + ").",
                   fix="Re-download and run this CSP's CSP_Setup.bat — it bakes the key in.")
        return out
    try:
        r = requests.get(base + "/sync", params={"csp_id": csp_id},
                         headers={"X-API-Key": key}, timeout=timeout)
    except requests.RequestException as e:
        out.update(status="unreachable",
                   reason=f"Cannot reach the Eko server ({e}).",
                   fix=f"Check the internet connection. Server: {base}")
        return out
    if r.status_code == 200:
        out.update(ok=True, status="connected",
                   reason=f"Connected to Eko · key …{key[-4:]} valid · OCR ready.",
                   fix="")
    elif r.status_code in (401, 403):
        out.update(status="bad_key",
                   reason="The Eko server REJECTED this CSP's API key (revoked or wrong).",
                   fix="Ask Eko to issue a fresh key, then re-run this CSP's CSP_Setup.bat.")
    else:
        out.update(status="server_error",
                   reason=f"Eko server returned HTTP {r.status_code}.",
                   fix="Try again shortly; if it persists, inform Eko.")
    return out


def _send_image(img_bytes: bytes, timeout: int, retries: int) -> list:
    """Send ONE page image to the server and return its extracted rows.
    Retries transient/5xx/busy failures, then raises ServerOcrError."""
    request_id = uuid.uuid4().hex
    plaintext = {
        "request_id": request_id,
        "file_type": "image",
        "file_b64": base64.b64encode(img_bytes).decode("ascii"),
        "page_from": None,
        "page_to": None,
    }
    last = None
    for attempt in range(retries + 1):
        try:
            return _attempt_send(plaintext, request_id, timeout)
        except _Retryable as e:
            last = e
            if attempt < retries:
                time.sleep(min(2.0, 0.5 * (attempt + 1)))
                continue
            raise ServerOcrError(str(e)) from e
    raise ServerOcrError(str(last) if last else "server OCR failed")


def _attempt_send(plaintext: dict, request_id: str, timeout: int) -> list:
    payload = encrypt_json(plaintext, config.ADMIN_API_KEY)
    try:
        resp = requests.post(
            _endpoint(),
            json={"csp_id": config.ADMIN_CSP_ID, "payload": payload},
            headers={"X-API-Key": config.ADMIN_API_KEY},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise _Retryable(f"server OCR unavailable: {e}") from e

    if resp.status_code >= 500:
        raise _Retryable(f"server OCR error ({resp.status_code})")
    if resp.status_code != 200:
        raise ServerOcrError(f"server OCR failed ({resp.status_code})")
    try:
        body = resp.json()
    except ValueError as e:
        raise ServerOcrError("server OCR returned invalid JSON") from e
    if not body.get("ok"):
        if str(body.get("error")) in ("ocr_busy", "ocr_unavailable"):
            raise _Retryable(str(body.get("error")))
        raise ServerOcrError(str(body.get("error") or "server OCR failed"))
    try:
        out = decrypt_json(body.get("payload"), config.ADMIN_API_KEY)
    except EnvelopeError as e:
        raise ServerOcrError("server OCR response could not be decrypted") from e
    if out.get("request_id") != request_id:
        raise ServerOcrError("server OCR response request_id mismatch")
    xlsx_b64 = out.get("xlsx_b64")
    if not xlsx_b64:
        raise ServerOcrError("server OCR response missing xlsx")
    from core.ocr_excel import xlsx_bytes_to_rows
    try:
        blob = base64.b64decode(str(xlsx_b64).encode("ascii"), validate=True)
    except Exception as e:
        raise ServerOcrError("server OCR response has bad xlsx") from e
    return xlsx_bytes_to_rows(blob)


def extract_file(path: str, file_type: str, page_from: int = None,
                 page_to: int = None, progress=None) -> dict:
    """Send a scan to centralized OCR, ONE PAGE PER REQUEST, and return the
    combined result as {xlsx_bytes, page_count, row_count}. Raises on failure so
    the caller falls back to local OCR."""
    if not enabled():
        raise ServerOcrError("server OCR is not enabled")
    timeout = int(getattr(config, "SERVER_OCR_TIMEOUT_SEC", 900))
    retries = max(0, int(getattr(config, "SERVER_OCR_RETRIES", 2)))
    from core.ocr_excel import rows_to_xlsx_bytes

    if file_type == "image":
        with open(path, "rb") as f:
            blob = f.read()
        if progress:
            progress(0, 1000, "Sending page to Eko OCR service...")
        rows = _send_image(blob, timeout, retries)
        if progress:
            progress(1000, 1000, f"Eko OCR read {len(rows)} row(s)")
        info = {"kb": round(len(blob) / 1024), "dpi": None}
        try:    # record the image size we sent (helps diagnose 0-row results)
            from PIL import Image as _I
            with _I.open(path) as _im:
                info["w"], info["h"] = _im.width, _im.height
        except Exception:
            pass
        return {"xlsx_bytes": rows_to_xlsx_bytes(rows), "page_count": 1,
                "row_count": len(rows), "sent_info": info}

    if file_type != "pdf":
        raise ServerOcrError("unsupported file type for server OCR")

    import gc
    import pypdfium2 as pdfium
    from concurrent.futures import ThreadPoolExecutor

    dpi = int(getattr(config, "SERVER_OCR_RENDER_DPI", 300))
    scale = dpi / 72.0
    parallel = max(1, int(getattr(config, "SERVER_OCR_PARALLEL", 6)))
    all_rows = []
    sent_info = None   # what the FIRST page render actually looked like
    with open(path, "rb") as f:
        pdf = pdfium.PdfDocument(f.read())
    try:
        total = len(pdf)
        lo = max(1, int(page_from or 1))
        hi = min(total, int(page_to or total))
        if lo > hi:
            lo, hi = 1, total
        n = hi - lo + 1
        done = 0
        # Render sequentially (pypdfium is NOT thread-safe), but run the slow
        # server OCR calls CONCURRENTLY, in waves of `parallel`. Only `parallel`
        # rendered page images are held in RAM at once (safe on the 4 GB box),
        # and the 40-vCPU server OCRs several pages at the same time — wall-clock
        # drops from pages x per-page to ~ceil(pages/parallel) x per-page.
        for wave_start in range(lo - 1, hi, parallel):
            wave = list(range(wave_start, min(wave_start + parallel, hi)))
            # Show which pages are being read DURING this wave (the ~seconds-long
            # server call), so the CSP screen shows live progress instead of a
            # static message while a wave is in flight.
            if progress:
                a = wave[0] - (lo - 1) + 1
                b = wave[-1] - (lo - 1) + 1
                label = f"page {a}" if a == b else f"pages {a}-{b}"
                progress(int(done / n * 1000), 1000,
                         f"Reading {label} of {n}...")
            pngs = []
            for pno in wave:
                page = pdf[pno]
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil()
                try:
                    buf = io.BytesIO()
                    pil.convert("RGB").save(buf, format="PNG")
                    pngs.append(buf.getvalue())
                    # Record WHAT we actually sent for the first page — a tiny or
                    # blank render is a real failure mode (0 rows come back fast),
                    # and without this the CSP screen can't tell a bad render from
                    # a bad scan. Surfaced in the review self-diagnosis banner.
                    if sent_info is None:
                        sent_info = {"w": pil.width, "h": pil.height,
                                     "kb": round(len(pngs[-1]) / 1024),
                                     "dpi": dpi}
                finally:
                    try:
                        pil.close()
                        bitmap.close()
                        page.close()
                    except Exception:
                        pass
                    gc.collect()
            if len(pngs) == 1:
                wave_rows = [_send_image(pngs[0], timeout, retries)]
            else:
                with ThreadPoolExecutor(max_workers=len(pngs)) as ex:
                    wave_rows = list(ex.map(
                        lambda b: _send_image(b, timeout, retries), pngs))
            for rows_i in wave_rows:          # ex.map preserves page order
                all_rows.extend(rows_i)
            done += len(wave)
            if progress:
                progress(int(done / n * 1000), 1000,
                         f"Eko OCR: {done} of {n} pages ({len(all_rows)} rows)")
        return {"xlsx_bytes": rows_to_xlsx_bytes(all_rows), "page_count": n,
                "row_count": len(all_rows), "sent_info": sent_info}
    finally:
        pdf.close()
