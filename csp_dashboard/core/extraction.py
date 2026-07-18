"""
Extraction + review gate.

Scanned-document OCR is never trustworthy on its own, so uploads do NOT go
straight into the database. Instead:

    upload  ->  build_draft()   (parse / OCR -> editable rows + page images)
            ->  CSP reviews & corrects on screen
            ->  commit_draft()  (validate edited rows -> CustomerCases + Messages)

A draft lives in uploads/drafts/<draft_id>/ (rows.json, meta.json, page images)
and is deleted on commit or cancel. Nothing is sent until the CSP confirms.
"""

import os
import json
import uuid
from typing import List, Dict, Tuple

import config
from core.parser import detect_format, parse
from core.column_mapper import map_columns, extract_row
from core.validator import CustomerRow
from campaigns.inoperative.classifier import classify
from database import queries

# ── OCR memory-safety limits (tuned for the 4 GB deploy PC) ──────────────────
# The confirmed deploy machines have 4 GB RAM, no GPU, and already run a
# crashing biometric RD service + a browser, so only ~1.3-2 GB is really free.
# These bounds stop one oversized page/image — or a mid-batch memory spike —
# from pushing the box into swap-thrash or an OOM.
OCR_MAX_IMAGE_SIDE = 5000        # px: cap the long side of any rasterised/loaded page
OCR_MAX_IMAGE_PIXELS = 64_000_000  # ~64 MP: reject decompression-bomb images (PIL default ~178 MP would need ~530 MB just to decode)
OCR_MIN_FREE_RAM_GB = 0.6        # abort a batch rather than OCR with less free than this


def _ensure_ocr_memory() -> None:
    """Memory valve, checked before rasterising each page. If free RAM has
    dropped below the floor (e.g. the biometric RD service spiked), force a GC
    and, if still starved, abort the batch with a clear, non-technical message
    instead of swap-thrashing/OOM-ing the CSP PC. No-op if RAM can't be measured."""
    import gc
    try:
        from core import hardware
        free = hardware.available_ram_gb()
    except Exception:
        return
    if free is None or free >= OCR_MIN_FREE_RAM_GB:
        return
    gc.collect()
    try:
        free = hardware.available_ram_gb()
    except Exception:
        return
    if free is not None and free < OCR_MIN_FREE_RAM_GB:
        raise MemoryError(
            f"the PC is low on memory ({free:.1f} GB free) right now. OCR was "
            f"paused to avoid crashing it — please close other apps (browser, "
            f"etc.) and try again, or upload the bank's Excel/CSV file, which "
            f"needs no OCR and is 100% accurate.")


def _downscale_for_ocr(pil_img):
    """Shrink an image whose long side exceeds OCR_MAX_IMAGE_SIDE, in place-ish
    (returns a possibly-smaller copy). Keeps grid detection intact for normal
    A4 scans (well under the cap) while bounding memory for oversized inputs."""
    try:
        w, h = pil_img.size
        long_side = max(w, h)
        if long_side > OCR_MAX_IMAGE_SIDE:
            ratio = OCR_MAX_IMAGE_SIDE / float(long_side)
            pil_img = pil_img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))))
    except Exception:
        pass
    return pil_img


def _drafts_root() -> str:
    return os.path.join(config.UPLOAD_FOLDER, "drafts")


def _draft_dir(draft_id: str) -> str:
    return os.path.join(_drafts_root(), draft_id)


# ── Build a draft (parse / OCR, no DB writes) ─────────────────────────────────

def build_draft(file_paths: List[str], campaign_id: str,
                original_names: List[str],
                page_from: int = None, page_to: int = None,
                progress=None) -> str:
    """Extract uploaded files into a review draft.

    page_from / page_to (1-based, inclusive) limit which PDF pages are processed
    — the CSP chooses how far into the document to run the campaign. None = all.

    progress: optional callback progress(done:int, total:int, message:str),
    invoked as pages are OCR'd so the upload can run in the background behind a
    progress bar (see core/jobs.py). None = run silently (tests, direct calls).
    """
    _purge_stale_drafts()
    draft_id = uuid.uuid4().hex
    ddir = _draft_dir(draft_id)
    os.makedirs(ddir, exist_ok=True)

    rows: List[Dict] = []
    page_images: List[str] = []
    page_span = None

    for idx, path in enumerate(file_paths):
        fmt = detect_format(path)
        if fmt == "pdf":
            raw_rows, imgs, span = _ocr_pdf(path, ddir, len(page_images),
                                            page_from, page_to, progress=progress)
            page_images.extend(imgs)
            page_span = span
        elif fmt == "image":
            if progress:
                progress(0, 1, "Reading image…")
            raw_rows, imgs = _ocr_image(path, ddir, len(page_images))
            page_images.extend(imgs)
            if progress:
                progress(1, 1, "Image read")
        else:
            if progress:
                progress(0, 1, "Reading file…")
            raw_rows = parse(path)
            if progress:
                progress(1, 1, "File read")

        if not raw_rows:
            continue
        mapping = map_columns(list(raw_rows[0].keys()))
        for raw in raw_rows:
            rows.append(_preview_row(extract_row(raw, mapping)))

    meta = {
        "campaign_id": campaign_id,
        "original_names": original_names,
        "page_images": [os.path.basename(p) for p in page_images],
        "page_span": page_span,   # {"from":x,"to":y,"total":n} or None
    }
    with open(os.path.join(ddir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    with open(os.path.join(ddir, "rows.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f)

    # DPDP: original uploads aren't kept — page images (for review) suffice.
    for path in file_paths:
        _remove_with_retry(path)

    return draft_id


def _remove_with_retry(path: str, attempts: int = 5, delay: float = 0.3) -> None:
    """Delete an uploaded source file, retrying briefly first.

    On Windows, pypdfium2/OpenCV can hold a file handle open for a moment after
    the PDF object is closed, so an immediate os.remove() can fail with a
    transient PermissionError even though nothing is really still using the
    file. A short retry absorbs that. If deletion still fails after retrying,
    this is a DPDP-relevant fact (a raw customer document would be left on
    disk) and must NOT be silently swallowed — it's logged loudly instead, and
    _purge_stale_uploads() (run at every app startup) sweeps up anything left
    behind so it never lingers indefinitely."""
    import time
    for i in range(attempts):
        try:
            os.remove(path)
            return
        except OSError:
            if i < attempts - 1:
                time.sleep(delay)
    print(f"WARNING: could not delete uploaded source file after {attempts} "
         f"attempts (DPDP: raw document left on disk): {path}")


def purge_stale_uploads() -> int:
    """Startup self-heal: delete any file sitting directly in the uploads
    folder (not the drafts/ subfolder, which manages its own lifecycle). Under
    normal operation nothing should persist there — every upload is deleted
    right after processing — but a crash mid-request, or a Windows file-lock
    that outlasts the retry above, can leave one behind. Returns the count
    removed."""
    import config
    root = config.UPLOAD_FOLDER
    if not os.path.isdir(root):
        return 0
    removed = 0
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


def _ocr_pdf(path: str, ddir: str, start_idx: int,
             page_from: int = None, page_to: int = None, progress=None):
    """OCR the requested page range of a scanned PDF; save oriented page images.
    Returns (rows, image_paths, span) where span records what was processed.
    progress: optional callback progress(done, total, message) per page."""
    from core.ocr_table import extract_with_image
    import pypdfium2 as pdfium

    rows: List[Dict] = []
    images: List[str] = []
    pdf = pdfium.PdfDocument(path)
    try:
        total = len(pdf)
        lo = max(1, page_from or 1)
        hi = min(total, page_to or total)
        if lo > hi:
            lo, hi = 1, total
        npages = hi - lo + 1
        if progress:
            progress(0, 1000, f"Reading {npages} page(s)…")   # 0-1000 = smooth %
        import gc
        from PIL import Image as _PILImage
        _PILImage.MAX_IMAGE_PIXELS = OCR_MAX_IMAGE_PIXELS
        angle = None  # detected on the first processed page, reused for the rest
        for pno in range(lo - 1, hi):
            # Memory valve: abort cleanly if free RAM dipped below the floor
            # mid-batch (e.g. the biometric RD service spiked) instead of thrashing.
            _ensure_ocr_memory()
            # 400 DPI so the table's ruled grid is detectable for cell-by-cell OCR.
            # A page raster at 400 DPI is large (~tens of MB); on the 4 GB deploy
            # PC we close the pdfium page/bitmap and drop the image every
            # iteration so a 20-page batch doesn't accumulate hundreds of MB.
            # The scale is also capped so an oversized page box can't render into
            # a huge bitmap that would blow the ~1.5 GB free budget.
            page = pdf[pno]
            scale = 400 / 72
            try:
                w_pt, h_pt = page.get_size()
                if max(w_pt, h_pt) * scale > OCR_MAX_IMAGE_SIDE:
                    scale = OCR_MAX_IMAGE_SIDE / max(w_pt, h_pt)
            except Exception:
                pass
            bitmap = page.render(scale=scale)
            pil = _downscale_for_ocr(bitmap.to_pil())
            try:
                page_base = pno - (lo - 1)   # 0-based page index within the range

                def _on_row(dr, tr, _b=page_base):
                    # real, per-row progress: overall fraction across all pages
                    if progress and tr:
                        frac = (_b + dr / tr) / npages
                        progress(int(frac * 1000), 1000,
                                 f"Page {_b + 1}/{npages}: reading row {dr}/{tr}")

                oriented, page_rows, angle = extract_with_image(pil, angle, on_row=_on_row)
                rows.extend(page_rows)
                images.append(_save_page(oriented, ddir, start_idx + len(images)))
                if progress:
                    progress(int((page_base + 1) / npages * 1000), 1000,
                             f"Finished page {page_base + 1} of {npages}")
            finally:
                del pil
                try:
                    bitmap.close()
                    page.close()
                except Exception:
                    pass
                gc.collect()
        span = {"from": lo, "to": hi, "total": total}
    finally:
        pdf.close()
    # Free the ~1 GB docTR/torch model now the batch is OCR'd, so it doesn't sit
    # resident through send/tracking on a low-RAM CSP PC (no-op if never loaded).
    from core.ocr_table import release_doctr_model, release_onnxtr_model
    release_doctr_model()
    release_onnxtr_model()   # free the ONNX sessions (~600 MB) after the batch too
    return rows, images, span


def _ocr_image(path: str, ddir: str, start_idx: int):
    from PIL import Image
    from core.ocr_table import extract_with_image
    # Reject decompression-bomb images and check free RAM before decoding a
    # potentially large user-supplied image on the 4 GB box.
    Image.MAX_IMAGE_PIXELS = OCR_MAX_IMAGE_PIXELS
    _ensure_ocr_memory()
    img = _downscale_for_ocr(Image.open(path))
    oriented, page_rows, _ = extract_with_image(img)
    from core.ocr_table import release_doctr_model, release_onnxtr_model
    release_doctr_model()
    release_onnxtr_model()   # free the ONNX sessions (~600 MB) after the batch too
    return page_rows, [_save_page(oriented, ddir, start_idx)]


def _save_page(pil_img, ddir: str, index: int) -> str:
    disp = pil_img.convert("L")
    disp.thumbnail((1400, 1980))  # keep review images light
    name = f"page_{index:03d}.png"
    disp.save(os.path.join(ddir, name))
    return os.path.join(ddir, name)


def _preview_row(extracted: Dict) -> Dict:
    """Turn one raw/OCR row into an editable preview row with status flags."""
    name = (extracted.get("name") or "").strip().upper()
    band_raw = (extracted.get("balance_band") or "").strip()
    mobile_raw = extracted.get("mobile") or ""

    issues = []
    band_label = ""
    is_sensitive = False
    try:
        c = classify(band_raw)
        band_label = c["band"]
        is_sensitive = c["is_sensitive"]
    except ValueError:
        issues.append("balance band unreadable")
    if not name:
        issues.append("name missing")

    mob_digits = "".join(ch for ch in str(mobile_raw) if ch.isdigit())
    if len(mob_digits) == 12 and mob_digits.startswith("91"):
        mob_digits = mob_digits[2:]
    reachable = len(mob_digits) == 10 and mob_digits[0] in "6789"
    mobile = mob_digits if reachable else ""

    return {
        "account_number": (extracted.get("account_number") or "").strip(),
        "name": name,
        "mobile": mobile,
        "balance_band": band_raw,
        "father_name": (extracted.get("father_name") or "").strip(),
        "village": (extracted.get("village") or "").strip(),
        "taluka": (extracted.get("taluka") or "").strip(),
        "address": (extracted.get("address") or "").strip(),
        "band_label": band_label,
        "is_sensitive": is_sensitive,
        "reachable": reachable,
        "issues": issues,
    }


# ── Load / discard ────────────────────────────────────────────────────────────

def load_draft(draft_id: str) -> Dict:
    ddir = _draft_dir(draft_id)
    with open(os.path.join(ddir, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    with open(os.path.join(ddir, "rows.json"), encoding="utf-8") as f:
        rows = json.load(f)
    return {"meta": meta, "rows": rows}


def draft_page_path(draft_id: str, page_name: str) -> str:
    # guard against path traversal
    safe = os.path.basename(page_name)
    return os.path.join(_draft_dir(draft_id), safe)


def discard_draft(draft_id: str):
    import shutil
    shutil.rmtree(_draft_dir(draft_id), ignore_errors=True)


def _purge_stale_drafts(max_age_hours: int = 12):
    """DPDP hygiene: delete abandoned review drafts (page images + rows) that
    were never confirmed or cancelled, so customer data doesn't linger on disk."""
    import time
    import shutil
    root = _drafts_root()
    if not os.path.isdir(root):
        return
    cutoff = time.time() - max_age_hours * 3600
    for name in os.listdir(root):
        d = os.path.join(root, name)
        try:
            if os.path.isdir(d) and os.path.getmtime(d) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


# ── Commit reviewed rows -> DB ────────────────────────────────────────────────

def commit_draft(draft_id: str, edited_rows: List[Dict], campaign_id: str
                 ) -> Tuple[str, dict]:
    """Validate the CSP-reviewed rows and create cases + messages.
    A row needs a name and a readable balance band to be committed; mobile is
    optional (blank -> the case is created and flagged 'not reachable')."""
    from datetime import datetime, timezone
    from core.message_engine import generate_batch_messages

    batch_id = f"BATCH_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
    meta = load_draft(draft_id)["meta"]
    original_name = _display_name(meta.get("original_names") or [])

    queries.insert_document(batch_id, campaign_id, original_name, "reviewed")
    queries.update_document_status(batch_id, "processing")

    errors = []
    created = 0
    duplicates = 0
    flagged = 0
    seen_accounts = set()

    def _norm_mobile(v):
        d = "".join(ch for ch in str(v or "") if ch.isdigit())
        if len(d) == 10 and d[0] in "6789":
            return d
        if len(d) == 12 and d.startswith("91") and d[2] in "6789":
            return d[2:]
        return ""   # unusable -> "" (not reachable); never a fabricated number

    for i, r in enumerate(edited_rows, start=1):
        # FAITHFUL CAPTURE — the PDF/sheet is the source of truth. We do NOT
        # reject, alter, or drop a row for a missing/uncertain field. We store
        # exactly what was reviewed, FLAG anything uncertain, and let the human
        # review gate (rows shown against the source image) be the only arbiter.
        # The only rows skipped are genuine account DUPLICATES (one account = one
        # case ever) and fully-empty extraction artefacts (not sheet data).
        account = (r.get("account_number") or "").strip()
        name = (r.get("name") or "").strip().upper()
        mobile = _norm_mobile(r.get("mobile"))
        band = (r.get("balance_band") or "").strip()
        father = r.get("father_name") or None
        village = r.get("village") or None
        taluka = r.get("taluka") or None
        address = r.get("address") or None

        acct = _norm_account(account)
        real_acct = bool(acct) and acct != "(none)"

        if not (real_acct or name or mobile or band):
            continue    # nothing on this row at all -> not real sheet data

        # Template classification only. An UNREADABLE band is KEPT (its raw value
        # is stored in balance_band) and defaulted to the normal template +
        # flagged for review — never a reason to drop the row.
        try:
            c = classify(band)
        except ValueError:
            c = {"band": band or "?", "tone": "normal",
                 "template_id": "template_1", "is_sensitive": False}
            flagged += 1

        # Duplicate guard: one account = one case (the money key). Skips a
        # re-uploaded page / overlapping range / account resent in a later list,
        # so a case (and its commission) is never counted twice. Missing accounts
        # can't be keyed, so they always pass through.
        if real_acct and (acct in seen_accounts
                          or queries.account_exists(campaign_id, acct)):
            duplicates += 1
            continue
        if real_acct:
            seen_accounts.add(acct)
        if not name:
            flagged += 1   # kept, but flagged: name couldn't be read from the sheet

        case_id = f"{batch_id}_C{i:04d}"
        queries.insert_customer_case(
            case_id=case_id, batch_id=batch_id, campaign_id=campaign_id,
            account_number=acct, name=name, mobile=mobile,
            father_name=father, balance_band=band,
            village=village, taluka=taluka, address=address,
            band_label=c["band"], tone=c["tone"], template_id=c["template_id"],
            is_sensitive=c["is_sensitive"],
        )
        queries.init_business_tracking(case_id)
        created += 1

    total = len(edited_rows)
    valid = created
    invalid = 0   # faithful capture never drops a row as "invalid"
    if valid == 0:
        queries.update_document_status(batch_id, "failed")
        discard_draft(draft_id)
        return batch_id, {"total": total, "valid": 0, "invalid": invalid,
                          "duplicates": duplicates, "flagged": flagged,
                          "messages_generated": 0, "not_reachable": 0, "errors": errors}

    queries.update_document_counts(batch_id, total, valid, invalid)
    msg = generate_batch_messages(batch_id)
    discard_draft(draft_id)

    return batch_id, {
        "total": total, "valid": valid, "invalid": invalid,
        "duplicates": duplicates, "flagged": flagged,
        "messages_generated": msg["generated"],
        "not_reachable": msg.get("not_reachable", 0),
        "errors": errors,
    }


def _norm_account(a: str) -> str:
    """Normalise an account number to digits only, so the same account written
    with stray spaces/OCR noise dedups to one key. Keeps a non-digit placeholder
    like '(none)' as-is (those are treated as un-keyable and never deduped)."""
    import re
    a = (a or "").strip()
    digits = re.sub(r"\D", "", a)
    return digits or a


def _display_name(names: List[str]) -> str:
    if not names:
        return "reviewed upload"
    if len(names) == 1:
        return names[0]
    return f"{len(names)} files: " + ", ".join(names[:3])
