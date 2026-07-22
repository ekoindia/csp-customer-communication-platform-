"""
CSP-side client for Eko centralized OCR.

Deliberately a thin transport layer: it sends a scanned PDF/image to the
admin/RAG server and receives an encrypted .xlsx of extracted rows. It hands
the .xlsx BYTES back to the existing draft/review pipeline (parsed in memory,
never written to disk). It never creates cases and never bypasses human review.

Transport shape (Tier 1):
  request : { csp_id, payload: <AES-GCM envelope of {request_id, file_type,
              file_b64, page_from, page_to}> }        header X-API-Key
  response: { ok, payload: <AES-GCM envelope of {request_id, xlsx_b64,
              page_count, row_count}> }

On ANY failure (network, auth, 5xx, busy, decrypt, bad shape) this raises
ServerOcrError; the caller (core.extraction) then falls back to local OCR, so
no upload is ever lost and the CSP always reaches the same review gate.
"""
import base64
import os
import time
import uuid

import requests

import config
from core.ocr_envelope import EnvelopeError, decrypt_json, encrypt_json


class ServerOcrError(Exception):
    """Raised when centralized OCR is unavailable or returns an invalid result."""


def enabled() -> bool:
    return bool(getattr(config, "SERVER_OCR_ENABLED", False)
                and getattr(config, "ADMIN_API_BASE", "")
                and getattr(config, "ADMIN_CSP_ID", "")
                and getattr(config, "ADMIN_API_KEY", "")
                and config.ADMIN_API_KEY != "demo-key-CSP001")


def _endpoint() -> str:
    return getattr(config, "ADMIN_API_BASE", "").rstrip("/") + "/ocr/extract"


def extract_file(path: str, file_type: str, page_from: int = None,
                 page_to: int = None) -> dict:
    """Send one file to centralized OCR; return {xlsx_bytes, page_count,
    row_count, request_id}. Retries transient failures, then raises."""
    if not enabled():
        raise ServerOcrError("server OCR is not enabled")
    try:
        size = os.path.getsize(path)
    except OSError as e:
        raise ServerOcrError("could not read upload") from e
    max_bytes = int(getattr(config, "SERVER_OCR_MAX_MB", 100)) * 1024 * 1024
    if size > max_bytes:
        raise ServerOcrError("file is too large for server OCR")

    with open(path, "rb") as f:
        blob = f.read()

    # One request_id for the whole call (reused across retries — the server
    # keeps no state, so a retry is safe/idempotent) and echoed back so we can
    # detect a mismatched/replayed response.
    request_id = uuid.uuid4().hex
    plaintext = {
        "request_id": request_id,
        "file_type": file_type,
        "file_b64": base64.b64encode(blob).decode("ascii"),
        "page_from": page_from,
        "page_to": page_to,
    }

    retries = max(0, int(getattr(config, "SERVER_OCR_RETRIES", 2)))
    timeout = int(getattr(config, "SERVER_OCR_TIMEOUT_SEC", 900))
    last_err = None
    for attempt in range(retries + 1):
        try:
            return _attempt(plaintext, request_id, timeout)
        except _Retryable as e:
            last_err = e
            if attempt < retries:
                time.sleep(min(2.0, 0.5 * (attempt + 1)))
                continue
            raise ServerOcrError(str(e)) from e
    # unreachable, but keep the type checker happy
    raise ServerOcrError(str(last_err) if last_err else "server OCR failed")


class _Retryable(Exception):
    """Internal: a failure worth retrying before giving up to local OCR."""


def _attempt(plaintext: dict, request_id: str, timeout: int) -> dict:
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

    # 5xx and 503-busy are transient; 4xx (auth/bad request) are not.
    if resp.status_code >= 500:
        raise _Retryable(f"server OCR error ({resp.status_code})")
    if resp.status_code != 200:
        raise ServerOcrError(f"server OCR failed ({resp.status_code})")

    try:
        body = resp.json()
    except ValueError as e:
        raise ServerOcrError("server OCR returned invalid JSON") from e
    if not body.get("ok"):
        # A structured "busy" from the server is retryable; other errors aren't.
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
    try:
        xlsx_bytes = base64.b64decode(str(xlsx_b64).encode("ascii"), validate=True)
    except Exception as e:
        raise ServerOcrError("server OCR response has bad xlsx") from e

    return {
        "xlsx_bytes": xlsx_bytes,
        "page_count": int(out.get("page_count") or 0),
        "row_count": int(out.get("row_count") or 0),
        "request_id": request_id,
    }
