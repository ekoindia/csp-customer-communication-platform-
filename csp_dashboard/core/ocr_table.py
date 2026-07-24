"""
Heavy-tuned OCR for scanned bank tables (Inoperative Accounts).

The bank sends a low-quality scanned PDF of a printed table, often rotated.
Pipeline per page:
    1. Auto-orient   — scans are frequently sideways; pick the rotation that
                       yields the most clean 10-digit mobile numbers.
    2. Row bands     — detect the table's horizontal ruled lines (OpenCV) to
                       segment records; falls back to y-clustering if the scan
                       has no usable lines.
    3. Token class.  — within each band, classify words by pattern:
                       account = 11+ digit run, mobile = 10-digit 6-9 start,
                       balance band = regex, name = remaining capitalised words.

Output is a best-effort DRAFT. It is never sent directly — the CSP reviews and
corrects every row on the preview screen before any case is created. So the goal
here is maximum recall with clean fields, not perfection.

Local only (Tesseract). No cloud OCR — DPDP safe.
"""

import os
import re
import threading
from typing import List, Dict

import numpy as np
import cv2
from PIL import Image
import pytesseract

# Balance-band patterns seen in the real document: 0.1<100, 100<1000,
# 1000<10000, B>10000 (and OCR-mangled variants of the top band).
_BAND_RE = re.compile(
    r"\d{1,5}(?:\.\d)?\s*<\s*\d{2,6}"   # 0.1<100 / 100<1000 / 1000<10000
    r"|B\s*>\s*\d{3,}"                  # B>10000
    r"|>\s*10000",
    re.IGNORECASE,
)

# The balance column can only EVER hold one of these four bands. On a phone photo
# Tesseract mangles them (l/1, O/0, S/5, dropped '<') so the strict _BAND_RE
# often fails and the cell comes back blank -> a needless "need a fix". Because
# there are only four possible values, we can snap whatever was read to the right
# band by the LEADING number's magnitude — robust even if the rest is garbled.
_KNOWN_BANDS = ("0.1<100", "100<1000", "1000<10000", "B>10000")


def _snap_band(text: str) -> str:
    """Snap a mangled balance-cell reading to one of the four known bands, or ''
    if nothing plausible. Used as a FALLBACK after a strict _BAND_RE match fails."""
    if not text:
        return ""
    s = re.sub(r"\s+", "", str(text).upper())
    if not s:
        return ""
    if ">" in s:                      # only the top band uses '>'
        return "B>10000"
    # normalise the usual photo-OCR letter->digit confusions
    t = s.translate(str.maketrans({"L": "1", "I": "1", "|": "1", "O": "0",
                                   "D": "0", "Q": "0", "S": "5", "Z": "2",
                                   "G": "6", "B": "8"}))
    # A real band is all digits/separators once the digit-confusion letters are
    # mapped. If any OTHER letter survived, this cell is text (a name/taluka bled
    # in), NOT a band -> don't fabricate one.
    if re.search(r"[A-Z]", t):
        return ""
    nums = re.findall(r"\d+(?:\.\d+)?", t)
    if not nums:
        return ""
    intpart = re.sub(r"\..*", "", nums[0])
    if len(intpart) > 5:              # bands top out at 10000 (5 digits); a longer
        return ""                     # leading number = another column bled in
    try:
        first = float(nums[0])
    except ValueError:
        return ""
    if first <= 0:
        return ""
    if first < 100:
        return "0.1<100"
    if first < 1000:
        return "100<1000"
    if first < 10000:
        return "1000<10000"
    return "B>10000"

# Relationship prefixes that mark the start of the address text (S/O, D/O, W/O),
# and the location markers (VILL-/POST-/DIST- etc.) used when there's no relation
# prefix. Not anchored to the string start — searched anywhere in the trailing
# text, since village name(s) may sit before the address begins.
#
# All of these are made OCR-TOLERANT on purpose: on a poor scan the 'O' in S/O
# is often read as a zero, and VILL/POST get their letters swapped for
# look-alike digits (VlLL, V1LL, P0ST). If these markers fail to match, the
# address text spills back into the village field (the exact "village/address
# not coming properly" bug) — so we accept the common misreads too.
_REL_PREFIX = re.compile(r"\b[SDW]\s*/\s*[O0]\b\s*:?", re.IGNORECASE)
_LOCATION_MARKER = re.compile(
    r"\bVI?LL[- ]"              # VILL- / VLL-
    r"|\bVILLAGE\b"
    r"|\bP[O0]ST[- ]"          # POST- / P0ST-
    r"|\bDIST[- ]"             # DIST-
    r"|\bGRAM\b"
    r"|\bAT[- ]"               # AT- (village-at prefix seen on rural forms)
    r"|\bMOH(?:ALLA)?[- ]",    # MOH- / MOHALLA-
    re.IGNORECASE,
)

# A 6-digit PIN code marks the tail end of the address on this form. Used as a
# last-resort address anchor when no VILL-/POST- marker survived OCR.
_PIN_RE = re.compile(r"\b\d{6}\b")

# Taluka keyword for this document's catchment area (fixed for the whole batch —
# every row on this bank form is the same taluka). Tolerant of the common OCR
# misread of Tamkuhi (T being dropped, 'i' read as 'l/1').
_TALUKA_RE = re.compile(r"tamkuh[il1]\s*raj|tamkuh[il1]|tehsil|block",
                        re.IGNORECASE)

# Row offset of the contact block (father/mobile/taluka/village/address) vs the
# account/name block on this bank form. On this bank's printed grid every column
# sits on the SAME ruled row, so this MUST be 0. (A previous -1 read the contact
# block one row too high — row 1 picked up the "FTHR_NM" header, and every other
# row got the PREVIOUS customer's father/mobile: a dangerous mis-attribution
# that would message the wrong person.)
_CONTACT_ROW_OFFSET = 0

_TESSERACT_OK = None
_DOCTR_MODEL = None
_PADDLE_MODEL = None
_RAPIDOCR_MODEL = None


def _ensure_tesseract():
    global _TESSERACT_OK
    if _TESSERACT_OK is None:
        import core.ocr  # noqa: F401  (sets pytesseract.tesseract_cmd path)
        _TESSERACT_OK = True


def _doctr_model():
    """Lazily build the docTR OCR model once (weights cached after first use).

    Uses the recognition backbone from config.DOCTR_RECO_ARCH (default "parseq",
    more accurate on scans than the old crnn default) and moves the model to the
    GPU when CUDA is available — same code runs on the dev machine's RTX 4060 and
    on the CPU-only deployment PC, just faster on the former."""
    global _DOCTR_MODEL
    if _DOCTR_MODEL is None:
        from doctr.models import ocr_predictor
        from core import hardware
        cuda = False
        try:
            import torch
            # Cap threads so a 2-core/4-thread i3 isn't oversubscribed.
            torch.set_num_threads(hardware.torch_threads())
            cuda = torch.cuda.is_available()
        except Exception:
            pass
        # "parseq" on a GPU, light "crnn_vgg16_bn" on CPU (unless config forces).
        reco = hardware.resolve_reco_arch(cuda)
        try:
            model = ocr_predictor(reco_arch=reco, pretrained=True)
        except Exception:
            # Unknown/unsupported arch name -> fall back to the safe default.
            model = ocr_predictor(pretrained=True)
        if cuda:
            try:
                model = model.cuda()
            except Exception:
                pass
        _DOCTR_MODEL = model
    return _DOCTR_MODEL


def release_doctr_model():
    """Drop the docTR/PyTorch model reference after a batch and clear the CUDA
    cache. Safe to call even if it was never loaded (the CSV/Excel/typed-PDF and
    Tesseract-only paths never build it).

    Honest note: on CPU this lets Python GC the object but does NOT hand the
    ~1 GB RSS back to the OS within the same process — full reclamation needs the
    planned short-lived OCR worker process. It matters little on the real 4 GB
    deploy target because that machine resolves to Tesseract-only and never
    builds this model at all; this mainly helps GPU boxes (frees VRAM) and
    mid-RAM (6-8 GB) machines under later memory pressure."""
    global _DOCTR_MODEL
    if _DOCTR_MODEL is None:
        return
    _DOCTR_MODEL = None
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _doctr_words(gray_np: np.ndarray):
    """Run docTR on one page; return words as {t, x, yc, conf} in pixel coords.
    docTR is a deep-learning OCR — far stronger on scanned tables than Tesseract,
    and fully local (DPDP-safe). Returns None if the engine is unavailable."""
    try:
        model = _doctr_model()
    except Exception:
        return None
    H, W = gray_np.shape
    rgb = np.stack([gray_np, gray_np, gray_np], axis=-1).astype("uint8")
    # inference_mode drops autograd bookkeeping -> lower peak memory on CPU.
    try:
        import torch
        ctx = torch.inference_mode()
    except Exception:
        import contextlib
        ctx = contextlib.nullcontext()
    with ctx:
        res = model([rgb])
    words = []
    for page in res.pages:
        for block in page.blocks:
            for line in block.lines:
                for w in line.words:
                    (x0, y0), (x1, y1) = w.geometry
                    words.append({
                        "t": w.value,
                        "x": (x0 + x1) / 2 * W,
                        "yc": (y0 + y1) / 2 * H,
                        "conf": float(getattr(w, "confidence", 1.0) or 0.0),
                    })
    return words


def _paddle_model():
    """Lazily build the PaddleOCR model once. PaddleOCR is a fully-local
    (DPDP-safe) deep-learning OCR that is generally stronger than docTR/Tesseract
    on scanned printed tables. Weights download once on first use, then cached."""
    global _PADDLE_MODEL
    if _PADDLE_MODEL is None:
        from paddleocr import PaddleOCR
        # Try the modern signature first, fall back for older/newer builds whose
        # constructor kwargs differ. Any failure raises and is caught by the
        # caller, which falls back to docTR.
        try:
            _PADDLE_MODEL = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
        except TypeError:
            _PADDLE_MODEL = PaddleOCR(lang="en")
    return _PADDLE_MODEL


def _paddle_words(gray_np: np.ndarray):
    """Run PaddleOCR on one page; return words as {t, x, yc, conf} in pixel
    coords — the SAME shape as _doctr_words, so the column-bucketing grid
    pipeline is engine-agnostic. Returns None if PaddleOCR is unavailable, so
    extraction falls back to docTR without breaking."""
    try:
        model = _paddle_model()
    except Exception:
        return None
    rgb = np.stack([gray_np, gray_np, gray_np], axis=-1).astype("uint8")
    try:
        result = model.ocr(rgb, cls=False)
    except TypeError:
        try:
            result = model.ocr(rgb)
        except Exception:
            return None
    except Exception:
        return None
    if not result:
        return None
    # PaddleOCR returns a list with one element per input image; unwrap it.
    lines = result[0] if len(result) == 1 and isinstance(result[0], list) else result
    if not lines:
        return None
    words = []
    for entry in lines:
        try:
            box, rec = entry[0], entry[1]
            text = rec[0] if isinstance(rec, (list, tuple)) else rec
            conf = rec[1] if isinstance(rec, (list, tuple)) and len(rec) > 1 else 1.0
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
        except (TypeError, IndexError, ValueError):
            continue
        if not text:
            continue
        words.append({
            "t": str(text),
            "x": sum(xs) / len(xs),
            "yc": sum(ys) / len(ys),
            "conf": float(conf or 0.0),
        })
    return words or None


def _rapidocr_model():
    """Lazily build RapidOCR.

    RapidOCR runs PaddleOCR-style detection/recognition models on ONNX Runtime
    CPU. This is the intended centralized-server engine for the 40-thread,
    128-GB RAG box: stronger than Tesseract, no GPU/PaddlePaddle install, and
    Python 3.12 compatible via rapidocr-onnxruntime.
    """
    global _RAPIDOCR_MODEL
    if _RAPIDOCR_MODEL is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            from rapidocr import RapidOCR
        _RAPIDOCR_MODEL = RapidOCR()
    return _RAPIDOCR_MODEL


def _rapidocr_words(gray_np: np.ndarray):
    """Run RapidOCR and return words as {t, x, yc, conf} in pixel coords."""
    try:
        model = _rapidocr_model()
    except Exception:
        return None
    rgb = (np.stack([gray_np] * 3, axis=-1).astype("uint8")
           if gray_np.ndim == 2 else gray_np.astype("uint8"))
    try:
        result = model(rgb)
    except Exception:
        return None

    # rapidocr-onnxruntime returns (result, elapse). Newer rapidocr may return
    # an OCRResult object with boxes/txts/scores. Support both shapes.
    entries = None
    if isinstance(result, tuple):
        entries = result[0]
    elif hasattr(result, "boxes") and hasattr(result, "txts"):
        boxes = result.boxes
        txts = result.txts
        scores = getattr(result, "scores", [1.0] * len(txts))
        entries = [[box, text, score] for box, text, score in zip(boxes, txts, scores)]
    else:
        entries = result
    if not entries:
        return None

    words = []
    for entry in entries:
        try:
            if isinstance(entry, dict):
                box = entry.get("box") or entry.get("points")
                text = entry.get("text") or entry.get("txt")
                conf = entry.get("score") or entry.get("confidence") or 1.0
            else:
                box = entry[0]
                if len(entry) >= 3 and isinstance(entry[1], str):
                    text, conf = entry[1], entry[2]
                else:
                    rec = entry[1]
                    text = rec[0] if isinstance(rec, (list, tuple)) else rec
                    conf = rec[1] if isinstance(rec, (list, tuple)) and len(rec) > 1 else 1.0
            if not text or box is None:
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            words.append({
                "t": str(text),
                "x": sum(xs) / len(xs),
                "yc": sum(ys) / len(ys),
                "conf": float(conf or 0.0),
            })
        except (TypeError, IndexError, ValueError):
            continue
    return words or None


# ── OnnxTR: docTR's models on ONNX Runtime — deep-learning accuracy, NO PyTorch ──
# This is the ACCURATE engine for the 4 GB CPU-only CSP box. It runs bundled ONNX
# models via onnxruntime (~700 MB peak, fits 4 GB) so extraction on a scanned
# mobile photo is as good as docTR — detection finds EVERY table row (which the
# old Tesseract path could not), recognition reads names, and account/mobile
# DIGITS are still read by the tiny custom digit model (core/ocr_onnx). Fully
# local/offline (models bundled in core/models/, no download at run time) —
# DPDP-safe. Degrades gracefully: if onnxtr/models are missing, _page_words falls
# back to Tesseract, so the app never crashes.
_ONNXTR_MODEL = None
_ONNXTR_TRIED = False
_ONNXTR_LOCK = threading.Lock()


def _onnxtr_paths():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    import config
    det = getattr(config, "ONNXTR_DET_PATH", "") or os.path.join(base, "db_mobilenet_v3_large.onnx")
    # crnn_vgg16_bn (not the small mobilenet reco): it reads scanned DIGITS far
    # better (49/53 vs 40/53 clean account numbers on the real page) and, run at
    # a small recognition batch (ONNXTR_RECO_BS), peaks at only ~640 MB — fits the
    # 4 GB box. The small mobilenet reco is kept bundled as a lighter option.
    reco = getattr(config, "ONNXTR_RECO_PATH", "") or os.path.join(base, "crnn_vgg16_bn.onnx")
    return det, reco


def onnxtr_available() -> bool:
    """True only if onnxtr + onnxruntime import AND both bundled models exist.
    A LIGHT check (no model load) so hardware.resolve_ocr_engine can call it to
    decide the engine for a low-RAM box without paying the load cost."""
    try:
        import importlib.util
        if (importlib.util.find_spec("onnxtr") is None
                or importlib.util.find_spec("onnxruntime") is None):
            return False
        det, reco = _onnxtr_paths()
        return os.path.isfile(det) and os.path.isfile(reco)
    except Exception:
        return False


def _onnxtr_model():
    """Lazily build the OnnxTR predictor ONCE from the bundled local ONNX files
    (no network). Thread caps come from config.TORCH_MAX_THREADS so a 2-core i3
    isn't oversubscribed. Returns None (never raises) if anything is missing, so
    the caller falls back to Tesseract."""
    global _ONNXTR_MODEL, _ONNXTR_TRIED
    if _ONNXTR_MODEL is not None or _ONNXTR_TRIED:
        return _ONNXTR_MODEL
    with _ONNXTR_LOCK:
        if _ONNXTR_MODEL is not None or _ONNXTR_TRIED:
            return _ONNXTR_MODEL
        try:
            import config
            from onnxtr.models import ocr_predictor

            if bool(getattr(config, "OCR_ONNXTR_HEAVY", False)):
                # OPT-IN HEAVY arches (db_resnet50 + parseq) — the CPU accuracy
                # ceiling. Built from BUNDLED ONNX files (no network), so it works
                # on a locked-down server that can't download weights. If the
                # heavy files aren't present it falls back to the light bundle —
                # OCR never silently returns nothing.
                try:
                    _ONNXTR_MODEL = _build_heavy_onnxtr(ocr_predictor)
                    print("[ocr] OnnxTR HEAVY arches (db_resnet50 + parseq, bundled)")
                except Exception as he:
                    print(f"[ocr] heavy arches unavailable ({he}); "
                          f"falling back to bundled light models")
                    _ONNXTR_MODEL = _build_bundled_onnxtr(ocr_predictor)
            else:
                # Default (CSP box AND server): the small BUNDLED models — no
                # network, offline, proven on the real scans.
                _ONNXTR_MODEL = _build_bundled_onnxtr(ocr_predictor)
        except Exception as e:
            print(f"[ocr] OnnxTR unavailable ({e}); will use Tesseract")
            _ONNXTR_MODEL = None
        finally:
            # Mark "tried" only AFTER the (slow, ~15 s) build finishes, INSIDE the
            # lock. This way concurrent first-callers block on the lock and then
            # reuse the built model, instead of the outside fast-path returning a
            # half-built None mid-build (which dropped ~2/12 parallel pages to 0).
            _ONNXTR_TRIED = True
    return _ONNXTR_MODEL


def _build_bundled_onnxtr(ocr_predictor):
    """Build the OnnxTR predictor from BUNDLED ONNX files (no network). Default
    and the fallback when heavy weights can't download.

    Detection can be upgraded to db_resnet50 (config.OCR_ONNXTR_DET="resnet50")
    when its bundled weight is present — a stronger detector that catches rows
    db_mobilenet misses on dense tables (e.g. 52 vs 50 on a packed page), while
    keeping crnn_vgg16 recognition (proven best on account/mobile DIGITS). Falls
    back to db_mobilenet if the resnet50 weight isn't bundled."""
    import config
    import onnxruntime as ort
    from onnxtr.models.detection import db_mobilenet_v3_large, db_resnet50
    from onnxtr.models.recognition import crnn_vgg16_bn
    from onnxtr.models.engine import EngineConfig
    det_path, reco_path = _onnxtr_paths()
    so = ort.SessionOptions()
    so.intra_op_num_threads = int(getattr(config, "TORCH_MAX_THREADS", 4))
    so.inter_op_num_threads = 1
    cfg = EngineConfig(providers=["CPUExecutionProvider"], session_options=so)

    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    res50 = os.path.join(base, "db_resnet50.onnx")
    if str(getattr(config, "OCR_ONNXTR_DET", "mobilenet")).lower() == "resnet50" \
            and os.path.isfile(res50):
        det = db_resnet50(model_path=res50, engine_cfg=cfg)
        print("[ocr] OnnxTR detection = db_resnet50 (stronger row detection)")
    else:
        det = db_mobilenet_v3_large(model_path=det_path, engine_cfg=cfg)
    reco = crnn_vgg16_bn(model_path=reco_path, engine_cfg=cfg)
    # Small recognition batch keeps peak RAM ~640 MB on the 4 GB box
    # (default 128 would spike to ~1.7 GB). det_bs=1: one page at a time.
    reco_bs = int(getattr(config, "ONNXTR_RECO_BS", 16))
    return ocr_predictor(det_arch=det, reco_arch=reco, assume_straight_pages=True,
                         reco_bs=reco_bs, det_bs=1)


def _build_heavy_onnxtr(ocr_predictor):
    """Build the ACCURATE heavy OnnxTR predictor (db_resnet50 detection + parseq
    recognition) from BUNDLED ONNX files — no network, so it runs on a
    locked-down CPU server. This is the highest-accuracy option that fits a
    GPU-less box (doc-VLMs like DeepSeek/Unlimited-OCR would need a GPU).
    Raises if the heavy weights aren't bundled so the caller falls back."""
    import config
    import onnxruntime as ort
    from onnxtr.models.detection import db_resnet50
    from onnxtr.models.recognition import parseq
    from onnxtr.models.engine import EngineConfig
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    det_p = getattr(config, "ONNXTR_DET_HEAVY_PATH", "") or os.path.join(base, "db_resnet50.onnx")
    reco_p = getattr(config, "ONNXTR_RECO_HEAVY_PATH", "") or os.path.join(base, "parseq.onnx")
    if not (os.path.isfile(det_p) and os.path.isfile(reco_p)):
        raise FileNotFoundError("heavy onnx weights (db_resnet50/parseq) not bundled")
    so = ort.SessionOptions()
    so.intra_op_num_threads = int(getattr(config, "TORCH_MAX_THREADS", 6))
    so.inter_op_num_threads = 1
    cfg = EngineConfig(providers=["CPUExecutionProvider"], session_options=so)
    det = db_resnet50(model_path=det_p, engine_cfg=cfg)
    reco = parseq(model_path=reco_p, engine_cfg=cfg)
    reco_bs = int(getattr(config, "ONNXTR_RECO_BS", 16))
    return ocr_predictor(det_arch=det, reco_arch=reco, assume_straight_pages=True,
                         reco_bs=reco_bs, det_bs=1)


def release_onnxtr_model():
    """Drop the OnnxTR sessions to reclaim RAM (mirror release_doctr_model)."""
    global _ONNXTR_MODEL, _ONNXTR_TRIED
    _ONNXTR_MODEL = None
    _ONNXTR_TRIED = False
    import gc
    gc.collect()


def _onnxtr_words(gray_np: np.ndarray):
    """Run OnnxTR on one page; return words as {t,x,yc,conf} in PIXEL coords —
    the SAME shape as _doctr_words/_tesseract_words, so the engine-agnostic grid
    pipeline (_extract_grid) runs unchanged. None if OnnxTR is unavailable."""
    model = _onnxtr_model()
    if model is None:
        return None
    H, W = gray_np.shape[:2]
    rgb = (np.stack([gray_np] * 3, axis=-1).astype("uint8")
           if gray_np.ndim == 2 else gray_np.astype("uint8"))
    try:
        res = model([rgb])
    except Exception as e:
        print(f"[ocr] OnnxTR inference failed ({e}); falling back")
        return None
    words = []
    for page in res.pages:
        for block in page.blocks:
            for line in block.lines:
                for w in line.words:
                    (x0, y0), (x1, y1) = w.geometry
                    words.append({
                        "t": w.value,
                        "x": (x0 + x1) / 2 * W,
                        "yc": (y0 + y1) / 2 * H,
                        "conf": float(getattr(w, "confidence", 1.0) or 0.0),
                    })
    return words or None


# Benchmark/testing hook: when set to "rapidocr", "paddle", "doctr" or "onnxtr", _page_words
# uses ONLY that engine with no fallback, so scripts/ocr_benchmark.py can compare
# engines head-to-head on the same page. Left None in normal operation.
_ENGINE_OVERRIDE = None
_STRICT_ENGINE = False


def _tesseract_words(gray_np: np.ndarray):
    """Read every word with Tesseract, returned in the SAME {t,x,yc,conf} shape
    as _doctr_words so the accurate grid logic in _extract_grid runs identically
    — with NO PyTorch loaded. This is the low-RAM path (~150 MB) for a 4 GB CSP
    PC. x is the word centre (matching docTR) so column bucketing lines up."""
    _ensure_tesseract()
    img = Image.fromarray(gray_np) if isinstance(gray_np, np.ndarray) else gray_np
    data = pytesseract.image_to_data(img, config="--psm 6",
                                     output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(data["text"]):
        t = t.strip()
        if not t:
            continue
        try:
            conf = int(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1
        if conf < 20:
            continue
        out.append({
            "t": t,
            "x": data["left"][i] + data["width"][i] / 2,
            "yc": data["top"][i] + data["height"][i] / 2,
            "conf": max(0, conf) / 100.0,
        })
    return out or None


def _page_words(gray_np: np.ndarray):
    """Read every word on the page with the OCR engine chosen for this machine
    (core/hardware.py), falling back gracefully. Returns (words, engine) or
    (None, None). On a low-RAM box this resolves to Tesseract and never imports
    torch; on a capable box it uses docTR and falls back to Tesseract only if
    the deep-learning engine is unavailable — so extraction always works."""
    if _ENGINE_OVERRIDE in ("rapidocr", "paddle", "doctr", "tesseract", "onnxtr"):
        if _ENGINE_OVERRIDE == "paddle":
            words = _paddle_words(gray_np)
        elif _ENGINE_OVERRIDE == "rapidocr":
            words = _rapidocr_words(gray_np)
        elif _ENGINE_OVERRIDE == "tesseract":
            words = _tesseract_words(gray_np)
        elif _ENGINE_OVERRIDE == "onnxtr":
            words = _onnxtr_words(gray_np)
        else:
            words = _doctr_words(gray_np)
        return (words, _ENGINE_OVERRIDE) if words else (None, None)

    from core import hardware
    engine = hardware.resolve_ocr_engine()
    if engine == "onnxtr":
        # 4 GB CPU box: deep-learning accuracy via ONNX Runtime (no PyTorch).
        # Falls back to Tesseract only if onnxtr/models are unavailable.
        words = _onnxtr_words(gray_np)
        if words:
            return words, "onnxtr"
        words = _tesseract_words(gray_np)
        return (words, "tesseract") if words else (None, None)
    if engine == "tesseract":
        # Low-RAM machine without onnxtr: stay light, do NOT load PyTorch.
        words = _tesseract_words(gray_np)
        return (words, "tesseract") if words else (None, None)

    if engine == "rapidocr":
        words = _rapidocr_words(gray_np)
        if words or _STRICT_ENGINE:
            return (words, "rapidocr") if words else (None, None)

    # docTR / paddle machine: try the deep-learning engine(s), then fall back to
    # Tesseract so a missing/broken torch install still produces a draft.
    order = ["paddle", "doctr"] if engine == "paddle" else ["doctr", "paddle"]
    for eng in order:
        words = _paddle_words(gray_np) if eng == "paddle" else _doctr_words(gray_np)
        if words:
            return words, eng
    words = _tesseract_words(gray_np)
    return (words, "tesseract") if words else (None, None)


def _clean_digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def detect_angle(gray: Image.Image) -> int:
    """Pick the rotation (0/90/180/270) that makes the page readable, scored by
    the number of clean 10-digit mobiles found. The probe must stay large enough
    for Tesseract to actually read digits (a too-small probe scores 0 for every
    rotation and falsely defaults to 0). A scanned document has one orientation
    throughout, so this is detected once per batch and reused for every page.

    Probe size is the dominant cost (Tesseract runs 4x). A 1400 px probe was
    measured to detect orientation correctly and ~2.5x faster than 2000 px on a
    260-DPI page; only if it finds ZERO mobiles (too small / sparse page) do we
    retry at full size, so the common case is fast and edge cases stay robust."""
    for cap in (1400, 2400):
        probe = gray.copy()
        probe.thumbnail((cap, cap))
        best_angle, best_score = 0, -1
        for angle in (0, 90, 180, 270):
            rot = probe.rotate(-angle, expand=True)
            txt = pytesseract.image_to_string(rot, config="--psm 6")
            score = len([d for d in re.findall(r"\b\d{10}\b", txt) if d and d[0] in "6789"])
            if score > best_score:
                best_angle, best_score = angle, score
        if best_score > 0:
            return best_angle
    return best_angle


def _detect_angle_deep(gray: Image.Image) -> int:
    """Orientation probe for centralized/deep OCR mode.

    Unlike detect_angle(), this does not call Tesseract. It rotates a bounded
    probe image and scores the configured deep engine's detected words.
    """
    probe = gray.copy()
    probe.thumbnail((1800, 1800))
    best_angle, best_score = 0, -1
    for angle in (0, 90, 180, 270):
        rot = probe.rotate(-angle, expand=True)
        words, _ = _page_words(np.array(rot))
        words = words or []
        digit_hits = sum(1 for w in words
                         if 10 <= len(_clean_digits(w.get("t", ""))) <= 16)
        band_hits = sum(1 for w in words if _BAND_RE.search(w.get("t", "")))
        alpha_hits = sum(1 for w in words if _is_word(w.get("t", "")))
        score = digit_hits * 4 + band_hits * 3 + alpha_hits
        if score > best_score:
            best_angle, best_score = angle, score
    return best_angle


def _row_band_edges(gray_np: np.ndarray) -> List[int]:
    """Y positions of horizontal ruled lines = record band edges."""
    bw = cv2.adaptiveThreshold(~gray_np, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 15, -2)
    cols = bw.shape[1]
    hstruct = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, cols // 3), 1))
    horiz = cv2.dilate(cv2.erode(bw, hstruct), hstruct)
    proj = horiz.sum(axis=1)
    if proj.max() == 0:
        return []
    thr = proj.max() * 0.25
    hits = [y for y in range(len(proj)) if proj[y] > thr]
    edges, prev = [], -100
    for y in hits:
        if y - prev > 12:
            edges.append(y)
        prev = y
    return edges


def _words(gray: Image.Image) -> List[Dict]:
    data = pytesseract.image_to_data(gray, config="--psm 6",
                                     output_type=pytesseract.Output.DICT)
    out = []
    for i, t in enumerate(data["text"]):
        t = t.strip()
        if not t:
            continue
        try:
            conf = int(data["conf"][i])
        except ValueError:
            conf = -1
        if conf < 20:
            continue
        out.append({"t": t, "x": data["left"][i],
                    "yc": data["top"][i] + data["height"][i] / 2})
    return out


def _bands_from_words(words: List[Dict]) -> List[List[Dict]]:
    """Fallback row segmentation when no ruled lines: cluster by y-centre."""
    words = sorted(words, key=lambda w: w["yc"])
    bands, cur, cur_y = [], [], None
    for w in words:
        if cur_y is None or abs(w["yc"] - cur_y) < 22:
            cur.append(w)
            cur_y = w["yc"] if cur_y is None else (cur_y + w["yc"]) / 2
        else:
            bands.append(cur)
            cur, cur_y = [w], w["yc"]
    if cur:
        bands.append(cur)
    return bands


def _is_word(tok: str) -> bool:
    # letters, allowing an internal '.', and standalone 'X' (bank placeholder)
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z.]*", tok))


def _split_trailing_fields(tail: str) -> Dict[str, str]:
    """
    Split the merged 'taluka + village + address' text into its three fields
    by CONTENT, not by column position — this bank scan's rightmost ruled
    lines are unreliable, so relying on a fixed column index for taluka vs
    village vs address collapses or duplicates them (out-of-range columns come
    back blank; two field-slots landing on the same detected column repeat the
    same text in both). Whatever columns actually got detected on the right,
    their text is concatenated first, then parsed here:

        <taluka keyword> <village name(s)> <S/O.. or VILL-.. address text>

    Order on the real form is fixed: taluka, then village, then address. The
    address is whatever starts at a relationship prefix (S/O, D/O, W/O) or,
    failing that, a location marker (VILL-, POST-, DIST-).
    """
    tail = tail.strip()
    taluka = village = address = ""

    tm = _TALUKA_RE.search(tail)
    rest = tail
    if tm:
        taluka = tm.group(0).title()
        rest = tail[tm.end():]

    # Find where the address starts. Priority: a relationship prefix (S/O..),
    # then a location marker (VILL-/POST-..), then — as a last resort when OCR
    # ate both — the run-up to a 6-digit PIN code, which only ever sits inside
    # the address.
    addr_start = None
    rel = _REL_PREFIX.search(rest)
    if rel:
        addr_start = rel.start()
    else:
        loc = _LOCATION_MARKER.search(rest)
        if loc:
            addr_start = loc.start()
        else:
            pin = _PIN_RE.search(rest)
            if pin:
                # Keep one leading word as the village (if any), send the rest —
                # up to and including the PIN — to the address.
                head = rest[:pin.start()].strip(" ,;-")
                parts = head.split()
                if len(parts) > 1:
                    village = parts[0]
                    address = " ".join(parts[1:]) + " " + rest[pin.start():].strip()
                    return {"taluka": taluka, "village": village.strip(" ,;-"),
                            "address": address.strip()}
                addr_start = pin.start() - len(head) if head else pin.start()

    if addr_start is not None and addr_start >= 0:
        village = rest[:addr_start].strip(" ,;-")
        address = rest[addr_start:].strip()
    else:
        # No recognisable address marker — treat the remainder as village and
        # leave address blank for the CSP to fill in from the page image.
        village = rest.strip(" ,;-")

    # Guard: village must never accidentally repeat the taluka text (that was
    # the old fixed-column duplication bug); trim it off if it leaked in.
    if taluka and village:
        vt = _TALUKA_RE.match(village.strip())
        if vt:
            village = village.strip()[vt.end():].strip(" ,;-")

    return {"taluka": taluka, "village": village, "address": address}


def _classify_band(words: List[Dict]) -> Dict:
    """Segment one table row into every column using the account, balance-band
    and mobile as horizontal anchors (column order in the sheet is:
    account | NAME | BALANCE | FATHER | MOBILE | TALUKA | VILLAGE | ADDRESS).
    Anything that can't be placed is left blank for the CSP to fill against the
    page image. 'X' placeholders in names are kept."""
    words = sorted(words, key=lambda w: w["x"])
    line = " ".join(w["t"] for w in words)

    account = account_x = None
    mobile = mobile_x = None
    band_x = None
    balance_band = ""

    for w in words:
        d = _clean_digits(w["t"])
        if account is None and len(d) >= 11:
            account, account_x = d, w["x"]
        if mobile is None and len(d) == 10 and d[0] in "6789":
            mobile, mobile_x = d, w["x"]
        if not balance_band:
            m = _BAND_RE.search(w["t"])
            if m:
                balance_band, band_x = m.group(0).replace(" ", ""), w["x"]
    if not balance_band:
        m = _BAND_RE.search(line)
        if m:
            balance_band = m.group(0).replace(" ", "")

    name_toks, father_toks, tail_toks = [], [], []
    for w in words:
        if not _is_word(w["t"]):
            continue
        x = w["x"]
        if account_x is not None and x < account_x - 5:
            continue  # SR NO / branch / CSP-code region — not a name
        if band_x is not None and x < band_x:
            name_toks.append(w["t"])
        elif mobile_x is not None and x >= mobile_x:
            tail_toks.append(w["t"])
        elif band_x is not None:
            father_toks.append(w["t"])
        else:
            name_toks.append(w["t"])   # no band anchor: treat as name

    name = " ".join(name_toks[:4]).upper().strip()
    father_name = " ".join(father_toks[:4]).upper().strip()

    tail = " ".join(tail_toks)
    fields = _split_trailing_fields(tail)

    return {
        "account_number": account or "",
        "name": name,
        "father_name": father_name,
        "balance_band": balance_band,
        "mobile": mobile or "",
        "taluka": fields["taluka"],
        "village": fields["village"],
        "address": fields["address"],
        "_raw": line,
    }


def extract_with_image(pil_img: Image.Image, angle: int = None, on_row=None):
    """Like extract_rows_from_pil but also returns the auto-oriented image (so
    the review screen can show what the OCR read) and the orientation angle used.
    Pass a known `angle` to skip per-page detection (much faster for a batch).
    on_row(done_rows, total_rows): optional callback fired as each row of the
    page is read, so the UI can show a REAL (per-row) progress bar."""
    gray = pil_img.convert("L")
    if angle is None:
        # Orientation is best-effort and must NEVER hard-fail. detect_angle uses
        # Tesseract; on a box where Tesseract isn't installed that raises. Fall
        # back to the deep-engine probe (no Tesseract), and if even that can't
        # run, assume the page is upright rather than crashing the whole read.
        try:
            angle = _detect_angle_deep(gray) if _STRICT_ENGINE else detect_angle(gray)
        except Exception:
            try:
                angle = _detect_angle_deep(gray)
            except Exception:
                angle = 0
    gray = gray.rotate(-angle, expand=True)
    rows = _extract_from_oriented(gray, on_row=on_row)
    return gray, rows, angle


def extract_rows_from_pil(pil_img: Image.Image, angle: int = None) -> List[Dict]:
    """Extract best-effort record rows from one scanned page image."""
    _, rows, _ = extract_with_image(pil_img, angle)
    return rows


def _extract_content(gray_np: np.ndarray, words, on_row=None):
    """Content-anchored extraction — RULED-LINE-INDEPENDENT (deep engines only).

    Anchors every row on its ACCOUNT NUMBER (the 11-16 digit token that clusters
    into one x-band on this bank form) and assigns the other fields by their x
    position relative to the account / balance / mobile columns. A deep engine
    (OnnxTR/docTR) gives word boxes precise enough that this needs NO ruled lines,
    so it works uniformly even on pages where line detection under- or over-counts
    columns — the exact failure that made the ruled-line grid drop rows or double
    them on some pages. Measured across a full 29-page scan: account 100%, name
    99%, band 95%, mobile 85% (vs 82/82/81/70% for the ruled-line path), with no
    page over/under-extracting. Returns rows, or None if too few account tokens to
    anchor (caller falls back to the ruled-line grid)."""
    H, W = gray_np.shape[:2]
    accts = [w for w in words if 11 <= len(_clean_digits(w["t"])) <= 16]
    if len(accts) < 5:
        return None
    ax = float(np.median([w["x"] for w in accts]))
    accts = [w for w in accts if abs(w["x"] - ax) < 0.09 * W]   # drop stray long numbers
    accts.sort(key=lambda w: w["yc"])
    ys = [w["yc"] for w in accts]
    gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    h = float(np.median(gaps)) if gaps else 30.0
    # One anchor per row: merge account boxes closer than half a row (a split read).
    anchors, arec = [], []
    for w in accts:
        if not anchors or w["yc"] - anchors[-1] > h * 0.5:
            anchors.append(w["yc"]); arec.append(w)
    if len(anchors) < 3:
        return None
    mobs = [w for w in words if _valid_mobile(w["t"])]
    mx = float(np.median([w["x"] for w in mobs])) if mobs else ax + 0.40 * W
    bands = [w for w in words if _BAND_RE.search(w["t"])]
    bx = float(np.median([w["x"] for w in bands])) if bands else ax + 0.15 * W

    recs = [{"account_number": _clean_digits(arec[i]["t"]), "name": "",
             "balance_band": "", "father_name": "", "mobile": "",
             "taluka": "", "village": "", "address": "", "_raw": "",
             "_nm": [], "_fa": [], "_tl": []} for i in range(len(anchors))]

    def nearest(y):
        best, bd = None, h * 0.6
        for i, ay in enumerate(anchors):
            d = abs(y - ay)
            if d < bd:
                bd, best = d, i
        return best

    for w in words:
        i = nearest(w["yc"])
        if i is None:
            continue
        t, x = w["t"], w["x"]
        r = recs[i]
        if _BAND_RE.search(t) and not r["balance_band"] and abs(x - bx) < 0.15 * W:
            r["balance_band"] = _BAND_RE.search(t).group(0).replace(" ", "")
        elif _valid_mobile(t) and abs(x - mx) < 0.12 * W and not r["mobile"]:
            r["mobile"] = _valid_mobile(t)
        elif x > mx:
            r["_tl"].append((x, t))
        elif _is_word(t) and ax < x < bx - 0.01 * W:
            r["_nm"].append((x, t))
        elif _is_word(t) and bx <= x <= mx:
            r["_fa"].append((x, t))

    # Recover mobiles the engine didn't tokenise (a faint/split mobile leaves the
    # cell blank): re-read just that one cell — row anchor Y x mobile column X —
    # with a digit-only Tesseract pass, then the custom digit model. Runs ONLY on
    # blank-mobile rows, so it's cheap. mobile = wrong person contacted, so this
    # extra check is worth it.
    mob_half = 0.09 * W
    x0m, x1m = max(0, int(mx - mob_half)), min(W, int(mx + mob_half))
    for i, r in enumerate(recs):
        if r["mobile"]:
            continue
        ay = anchors[i]
        y0, y1 = max(0, int(ay - h * 0.45)), min(H, int(ay + h * 0.45))
        if y1 - y0 < 4 or x1m - x0m < 4:
            continue
        cand = ""
        if not _STRICT_ENGINE:
            cand = _valid_mobile(_clean_digits(
                _ocr_cell(gray_np, y0, y1, x0m, x1m, whitelist="0123456789")))
        if not cand:
            try:
                from core import ocr_onnx
                cand = _valid_mobile(_clean_digits(ocr_onnx.recognize(gray_np[y0:y1, x0m:x1m])))
            except Exception:
                cand = ""
        if cand:
            r["mobile"] = cand

    for k, r in enumerate(recs):
        r["name"] = " ".join(t for _, t in sorted(r["_nm"]))[:40].upper().strip()
        r["father_name"] = " ".join(t for _, t in sorted(r["_fa"]))[:40].upper().strip()
        fields = _split_trailing_fields(" ".join(t for _, t in sorted(r["_tl"])))
        r["taluka"], r["village"], r["address"] = fields["taluka"], fields["village"], fields["address"]
        for key in ("_nm", "_fa", "_tl"):
            r.pop(key, None)
        if on_row:
            on_row(k + 1, len(recs))
    out = [r for r in recs if r["account_number"] or r["name"] or r["mobile"]]
    return out or None


def _extract_from_oriented(gray: Image.Image, on_row=None) -> List[Dict]:
    """Deep engines (OnnxTR/docTR): content-anchored extraction (ruled-line-
    independent, most robust). Otherwise the 4 GB Tesseract paths: ruled-line grid
    + on-device digit model, then the plain grid, then word clustering."""
    gray_np = _deskew(np.array(gray))

    # Deep-learning engine → content-anchored path first (best across all pages).
    try:
        from core import hardware
        engine = _ENGINE_OVERRIDE or hardware.resolve_ocr_engine()
    except Exception:
        engine = _ENGINE_OVERRIDE
    if engine in ("rapidocr", "onnxtr", "doctr", "paddle"):
        words, _eng = _page_words(gray_np)
        if words:
            content = _extract_content(gray_np, words, on_row=on_row)
            if content:
                return content
        if _STRICT_ENGINE:
            return []

    # On the 4 GB deploy box (Tesseract engine) the OCR word-reader is too weak
    # to locate all rows. If the trained on-device digit model is available,
    # prefer the RULED-LINE path: rows/columns from the table's grid lines (pure
    # OpenCV, engine-independent, finds ALL rows) + the model reads the digit
    # cells. This is what makes "all rows, accurate on 4 GB" hold.
    try:
        from core import hardware, ocr_onnx
        if hardware.resolve_ocr_engine() == "tesseract" and ocr_onnx.available():
            line_rows = _extract_grid_lines(gray_np, on_row=on_row)
            if line_rows:
                return line_rows
    except Exception:
        pass

    grid_rows = _extract_grid(gray_np)
    if grid_rows is not None:
        return grid_rows

    # ── Fallback: word clustering (unruled / very poor scans) ──
    words = _words(gray)
    if not words:
        return []
    edges = _row_band_edges(gray_np)
    bands: List[List[Dict]] = []
    if len(edges) >= 3:
        for y0, y1 in zip(edges, edges[1:]):
            if y1 - y0 < 15:
                continue
            band = [w for w in words if y0 < w["yc"] < y1]
            if band:
                bands.append(band)
    else:
        bands = _bands_from_words(words)
    rows = []
    for band in bands:
        rec = _classify_band(band)
        # Keep every case with any content — segregation happens afterwards.
        if any(str(rec[k]).strip() for k in (
                "account_number", "name", "father_name", "balance_band",
                "mobile", "taluka", "village", "address")):
            rows.append(rec)
    return rows


# ── Cell-by-cell grid extraction ──────────────────────────────────────────────

def _grid_line_positions(bw: np.ndarray, axis: int, frac: int,
                         thr: float, gap: int) -> List[int]:
    H, W = bw.shape
    if axis == 1:  # vertical lines -> column edges
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, H // frac)))
        proj = cv2.dilate(cv2.erode(bw, k), k).sum(axis=0)
        n = W
    else:          # horizontal lines -> row edges
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, W // frac), 1))
        proj = cv2.dilate(cv2.erode(bw, k), k).sum(axis=1)
        n = H
    mx = proj.max() or 1
    hits = [i for i in range(n) if proj[i] > mx * thr]
    out, prev = [], -999
    for i in hits:
        if i - prev > gap:
            out.append(i)
        prev = i
    return out


def _ocr_cell(gray_np: np.ndarray, y0: int, y1: int, x0: int, x1: int,
              whitelist: str = None) -> str:
    crop = gray_np[y0 + 3:y1 - 3, x0 + 3:x1 - 3]
    if crop.size == 0 or crop.shape[0] < 10 or crop.shape[1] < 10:
        return ""
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    crop = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    cfg = "--psm 7"
    if whitelist:
        cfg += f" -c tessedit_char_whitelist={whitelist}"
    return pytesseract.image_to_string(crop, config=cfg).strip().replace("\n", " ")


def _clean_text_cell(s: str) -> str:
    # drop cell-border artefacts (| [ { / etc.) from the edges, collapse spaces
    s = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9.]+$", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _clean_village(v: str) -> str:
    """The village column is narrow and sits between the taluka (left) and the
    wide address (right) columns, so it frequently catches BLEED from both — a
    trailing taluka word ('Raj'), or a leading fragment of the address
    ('AHIRAULI', 'DUDAHI', 'DU', 'AH'). The village on this form is a single
    place name, so: drop taluka words, address markers, and anything with a
    digit, then keep the first surviving token. (The review screen is the final
    fix for the rare multi-word village.)"""
    toks = []
    for t in v.split():
        tl = t.lower().strip(".,;-")
        if not tl or tl in ("tamkuhi", "raj", "tehsil", "block"):
            continue
        if _LOCATION_MARKER.search(t) or _PIN_RE.search(t) or any(ch.isdigit() for ch in t):
            continue
        toks.append(t)
    return toks[0] if toks else ""


def _deskew(gray_np: np.ndarray) -> np.ndarray:
    """Measure page tilt from the long horizontal ruled lines (Hough) and rotate
    to make them level, so every column shares the same row line."""
    h, w = gray_np.shape
    bw = cv2.adaptiveThreshold(~gray_np, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 15, -2)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 25), 1))
    horiz = cv2.dilate(cv2.erode(bw, hk), hk)
    lines = cv2.HoughLinesP(horiz, 1, np.pi / 180, threshold=200,
                            minLineLength=w // 4, maxLineGap=25)
    angles = []
    if lines is not None:
        for l in lines:
            x1, y1, x2, y2 = l[0]
            ang = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if abs(ang) < 10:
                angles.append(ang)
    if not angles:
        return gray_np
    skew = float(np.median(angles))
    if abs(skew) < 0.1:
        return gray_np
    M = cv2.getRotationMatrix2D((w / 2, h / 2), skew, 1.0)
    return cv2.warpAffine(gray_np, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)


def _valid_mobile(text: str) -> str:
    d = _clean_digits(text)
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    return d if (len(d) == 10 and d[0] in "6789") else ""


def _extract_grid(gray_np: np.ndarray):
    """Cell extraction anchored on the ACCOUNT column.

    Rows are defined by the account numbers (reliably read, one per record) —
    this captures every row including the top ones. Each column is OCR'd as an
    isolated strip; its words drop onto the row anchors by Y. The right-side
    block (father, mobile, taluka, village, address) can sit a constant offset
    of one row from the account column in these scans, so that offset is
    auto-detected from the mobile column (which has a clear blank/filled signal)
    and applied to the whole block. Returns None if no usable grid.
    """
    xs = _grid_line_positions(
        cv2.adaptiveThreshold(~gray_np, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                              cv2.THRESH_BINARY, 15, -2),
        axis=1, frac=12, thr=0.15, gap=15)
    if len(xs) < 6:
        return None
    ncols = len(xs) - 1

    # One OCR pass over the whole page (PaddleOCR or docTR — see config), then
    # bucket each word into its column by x. Deep-learning OCR reads scanned
    # digits/names far more reliably than plain Tesseract.
    words, _engine = _page_words(gray_np)
    if not words:
        return None
    col_words = [[] for _ in range(ncols)]
    for w in words:
        entry = {"t": w["t"], "yc": w["yc"], "conf": w["conf"]}
        placed = False
        for c in range(ncols):
            if xs[c] <= w["x"] < xs[c + 1]:
                col_words[c].append(entry)
                placed = True
                break
        if not placed:
            # Words outside the detected grid lines would otherwise be DROPPED.
            # The rightmost address column is wide and its faint right border is
            # often missed, so any word past the last line belongs to the last
            # (address) column; anything left of the first line to column 0.
            # This is what stops Village/Address coming back blank.
            col_words[ncols - 1 if w["x"] >= xs[-1] else 0].append(entry)

    # Identify key columns by content.
    account_col = _col_by(col_words, ncols,
                          lambda t: 11 <= len(_clean_digits(t)) <= 16)
    mobile_col = _col_by(col_words, ncols, lambda t: bool(_valid_mobile(t)))
    balance_col = _col_by(col_words, ncols, lambda t: bool(_BAND_RE.search(t)))
    if account_col is None:
        return None

    # Row anchors = account-column word Ys. Anchor on EVERY account-column box
    # with >= 6 digits, not only the ones that read as a full 11-16 digit account
    # — a deep engine detects one box per row (53/53 on the real page) even when
    # recognition drops a digit or two, so this captures every row instead of
    # silently losing the rows whose account misread (the old 44/53 gap).
    acc_ws = sorted((w for w in col_words[account_col]
                     if len(_clean_digits(w["t"])) >= 6),
                    key=lambda w: w["yc"])
    raw_anchors = [w["yc"] for w in acc_ws]
    if len(raw_anchors) < 3:
        return None
    gaps = [raw_anchors[i + 1] - raw_anchors[i] for i in range(len(raw_anchors) - 1)]
    h = float(np.median(gaps)) if gaps else 55.0
    # Merge near-duplicate anchors (one account box split into two by the engine)
    # that sit closer than half a row apart, so a split doesn't create a phantom row.
    anchors = []
    for y in raw_anchors:
        if not anchors or y - anchors[-1] > h * 0.5:
            anchors.append(y)

    # Build a full 2D cell grid: every word is assigned to the row whose anchor
    # is NEAREST (capped at ~0.6 row-heights), and to its x-bucket column. This
    # replaces the old fixed-tolerance window that read each column independently
    # — that window silently DROPPED a word sitting just past its edge (cell
    # looked empty though the scan clearly had data) and could match the SAME
    # word into two rows (the one-row shift). Nearest-anchor puts every word in
    # exactly one cell, so the fix applies uniformly to ALL columns, not just a
    # hand-tuned per-column offset. Words more than ~0.6h from any anchor (e.g.
    # the header line above row 1) fall outside the cap and are ignored.
    cap = h * 0.6

    def col_of(x):
        for c in range(ncols):
            if xs[c] <= x < xs[c + 1]:
                return c
        return ncols - 1 if x >= xs[-1] else 0

    grid = [[[] for _ in range(ncols)] for _ in range(len(anchors))]
    for w in words:
        ri, best = None, cap
        for i, ay in enumerate(anchors):
            d = abs(w["yc"] - ay)
            if d < best:
                ri, best = i, d
        if ri is not None:
            grid[ri][col_of(w["x"])].append(w)

    def cell(ri, c):
        if c is None or not (0 <= c < ncols):
            return ""
        return " ".join(w["t"] for w in sorted(grid[ri][c], key=lambda w: w["x"]))

    def digit_cell(c, ri):
        """Second opinion for a numeric column: crop that one physical cell and
        re-OCR it with a Tesseract digit-only whitelist. The deep-learning engine
        reads most cells well but occasionally drops/swaps a single digit; on the
        account/mobile columns that's the costliest error (wrong mobile = wrong
        person contacted), so those cells are cross-checked here."""
        if c is None or not (0 <= c < ncols):
            return ""
        ay = anchors[ri]
        y0, y1 = int(ay - h / 2), int(ay + h / 2)
        return _clean_digits(_ocr_cell(gray_np, y0, y1, xs[c], xs[c + 1],
                                       whitelist="0123456789"))

    def onnx_cell(c, ri):
        """Custom on-device digit model (core/ocr_onnx, onnxruntime-CPU) reading
        of one account/mobile cell — reads mobile-photo digits far better than
        Tesseract on the 4 GB box. "" if the model isn't installed (caller then
        falls back to the OCR-engine read)."""
        if c is None or not (0 <= c < ncols):
            return ""
        from core import ocr_onnx
        ay = anchors[ri]
        y0, y1 = max(0, int(ay - h / 2)), int(ay + h / 2)
        return _clean_digits(ocr_onnx.recognize(gray_np[y0:y1, xs[c]:xs[c + 1]]))

    bal_col = balance_col if balance_col is not None else account_col + 2
    mob_col = mobile_col if mobile_col is not None else account_col + 4

    # Name and father are the ONLY text columns and were previously read at a
    # FIXED offset from account (+1, +3). Across a 29-page scan the detected
    # column count drifts per page, so a fixed offset put the balance band into
    # "name" and blanked the father. Instead, place them by CONTENT relative to
    # the reliably-identified numeric anchors: NAME is the most-alphabetic column
    # between account and balance; FATHER is the most-alphabetic column between
    # balance and mobile. This self-corrects when a page's columns shift.
    def _alpha_between(lo, hi):
        best, best_score = None, 0
        for c in range(max(0, lo) + 1, min(ncols, hi)):
            score = sum(1 for w in col_words[c] if _is_word(w["t"]))
            if score > best_score:
                best, best_score = c, score
        return best

    name_col = _alpha_between(account_col, bal_col)
    if name_col is None:
        name_col = account_col + 1
    father_col = _alpha_between(bal_col, mob_col)
    if father_col is None:
        father_col = bal_col + 1

    # Taluka / village / address are three DISTINCT columns to the right of
    # mobile. Identify them by CONTENT, not fixed index:
    #   - taluka column  = the one whose text matches the taluka keyword
    #   - address column = the one full of VILL-/POST-/DIST-/PIN markers (widest,
    #     rightmost). Everything from it to the last column is address (a wide
    #     address often wraps past the faint right ruled lines).
    #   - village column = the single column sitting between those two.
    taluka_col = _col_by(col_words, ncols, lambda t: bool(_TALUKA_RE.search(t)))
    addr_col = _col_by(col_words, ncols,
                       lambda t: bool(_LOCATION_MARKER.search(t) or _PIN_RE.search(t)))
    village_col = addr_col - 1 if (taluka_col is not None and addr_col is not None
                                   and addr_col - taluka_col >= 2) else None
    trailing_cols = list(range(mob_col + 1, ncols))

    rows = []
    # A deep-learning engine (OnnxTR/docTR/paddle) reads scanned digits better
    # than the tiny custom digit model — measured 49/53 vs 17/53 clean accounts on
    # the real page — so for those engines TRUST the engine text first and use the
    # custom model only as a fallback. On the weak Tesseract path it's the reverse
    # (the custom model beats Tesseract), so that path tries the model first.
    _deep = _engine in ("rapidocr", "onnxtr", "doctr", "paddle")

    def read_account(ri):
        cands = ([_clean_digits(cell(ri, account_col)), onnx_cell(account_col, ri)]
                 if _deep else
                 [onnx_cell(account_col, ri), _clean_digits(cell(ri, account_col))])
        for c in cands:
            if 10 <= len(c) <= 16:
                return c
        alt = digit_cell(account_col, ri)      # last resort: Tesseract digit re-read
        return alt if 10 <= len(alt) <= 16 else ""

    def read_mobile(ri):
        cands = ([cell(ri, mob_col), onnx_cell(mob_col, ri)] if _deep
                 else [onnx_cell(mob_col, ri), cell(ri, mob_col)])
        for c in cands:
            v = _valid_mobile(c)
            if v:
                return v
        return _valid_mobile(digit_cell(mob_col, ri))

    for ri in range(len(anchors)):
        account = read_account(ri)
        mobile = read_mobile(ri)

        # Balance band: strict regex first; if that fails (mangled photo OCR),
        # snap the cell text to the nearest of the 4 known bands so it isn't left
        # blank for the CSP to fix by hand.
        _bal_txt = cell(ri, bal_col)
        _bm = _BAND_RE.search(_bal_txt)
        balance_band = _bm.group(0).replace(" ", "") if _bm else _snap_band(_bal_txt)

        # Name / father are text. If a mis-detected column dropped a balance band
        # or a bare number into them, that's clearly wrong — blank it rather than
        # show "100<1000" as a customer's name (leave it for the CSP to fill).
        def _text_only(s):
            s = _clean_text_cell(s).upper()
            if not s or _BAND_RE.search(s) or _valid_mobile(s):
                return ""
            if len(_clean_digits(s)) >= 5:   # mostly digits -> not a name
                return ""
            return s
        name = _text_only(cell(ri, name_col))
        father = _text_only(cell(ri, father_col))

        # Taluka / village / address — read as separate columns when cleanly
        # identified; otherwise merge the trailing columns and split by content.
        if taluka_col is not None and addr_col is not None and addr_col > taluka_col:
            taluka = _clean_text_cell(cell(ri, taluka_col))
            village = _clean_village(_clean_text_cell(cell(ri, village_col))) if village_col is not None else ""
            address = _clean_text_cell(
                " ".join(cell(ri, c) for c in range(addr_col, ncols) if cell(ri, c)))
            # Guard: if the village slot actually holds address text (a location
            # marker or PIN leaked in), move it into the address, not the village.
            if village and (_LOCATION_MARKER.search(village) or _PIN_RE.search(village)):
                address = _clean_text_cell(village + " " + address)
                village = ""
        else:
            fields = _split_trailing_fields(
                " ".join(cell(ri, c) for c in trailing_cols if cell(ri, c)).strip())
            taluka, village, address = fields["taluka"], fields["village"], fields["address"]

        rec = {
            "account_number": account,
            "name": name,
            "balance_band": balance_band,
            "father_name": father,
            "mobile": mobile,
            "taluka": taluka,
            "village": village,
            "address": address,
            "_raw": "",
        }
        # Keep every case (segregation happens afterwards).
        if any(str(rec[k]).strip() for k in (
                "account_number", "name", "father_name", "balance_band",
                "mobile", "taluka", "village", "address")):
            rows.append(rec)
    return rows or None


def _extract_grid_lines(gray_np: np.ndarray, on_row=None):
    """Row/column geometry from the table's RULED LINES (pure OpenCV, no OCR
    engine) + the trained on-device digit model for account/mobile. The grid
    lines are found without any text reading, so this locates EVERY row even on
    the weak 4 GB Tesseract box. Returns rows, or None if the ruled grid or the
    model isn't usable (caller then falls back to the word-anchored path)."""
    from core import ocr_onnx
    if not ocr_onnx.available():
        return None
    bw = cv2.adaptiveThreshold(~gray_np, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 15, -2)
    xs = _grid_line_positions(bw, axis=1, frac=12, thr=0.15, gap=15)
    ys = _grid_line_positions(bw, axis=0, frac=12, thr=0.15, gap=15)
    if len(xs) < 6 or len(ys) < 8:
        return None
    ncols, nrows = len(xs) - 1, len(ys) - 1

    def onnx_digits(ri, ci):
        return _clean_digits(ocr_onnx.recognize(gray_np[ys[ri]:ys[ri + 1], xs[ci]:xs[ci + 1]]))

    def text_cell(ri, ci):
        return _ocr_cell(gray_np, ys[ri], ys[ri + 1], xs[ci], xs[ci + 1])

    sample = min(nrows, 20)
    acc_s = [0] * ncols
    mob_s = [0] * ncols
    band_s = [0] * ncols
    for ci in range(ncols):
        for ri in range(sample):
            d = onnx_digits(ri, ci)
            # Account numbers on this bank form are 11+ digits. A 10-digit value
            # is a MOBILE, not an account — counting 10 here let the mobile column
            # win 'account', producing account==mobile with blank names (the whole
            # row's columns then keyed off the wrong anchor). Require >= 11.
            if len(d) >= 11:
                acc_s[ci] += 1
            if _valid_mobile(d):
                mob_s[ci] += 1
            if _BAND_RE.search(text_cell(ri, ci)):
                band_s[ci] += 1
    mobile_col = max(range(ncols), key=lambda c: mob_s[c]) if max(mob_s) else None
    account_col = max(range(ncols), key=lambda c: acc_s[c])
    # Never let account and mobile resolve to the SAME column.
    if account_col == mobile_col or acc_s[account_col] == 0:
        cands = [c for c in range(ncols) if c != mobile_col and acc_s[c] > 0]
        if not cands:
            return None
        account_col = max(cands, key=lambda c: acc_s[c])
    bal_col = max(range(ncols), key=lambda c: band_s[c]) if max(band_s) else None

    # Name = most-alphabetic column between account and band; FALL BACK to the
    # column right after account (bank layout is always account, name, …) so a
    # name is never left blank (which the commit validator once rejected).
    name_col, name_best = None, 0
    hi = bal_col if bal_col is not None else ncols
    for c in range(account_col + 1, hi):
        sc = sum(1 for ri in range(sample) if _is_word(text_cell(ri, c)))
        if sc > name_best:
            name_col, name_best = c, sc
    if name_col is None and account_col + 1 < ncols:
        name_col = account_col + 1

    # Village (TEXT) sits to the RIGHT of mobile in the layout
    # ...|mobile|taluka|village|address. Taluka repeats across rows and the
    # address cell is long/multi-word; the village is a SHORT place name that
    # VARIES per row. So among the trailing text columns, pick the one with the
    # most DISTINCT short (<=3-word) alphabetic values after village-cleaning.
    village_col, v_best = None, 0
    if mob_col is not None:
        for c in range(mob_col + 1, ncols):
            vals = []
            for ri in range(sample):
                raw = text_cell(ri, c)
                if len(raw.split()) > 3:        # address-like column -> skip
                    continue
                t = _clean_village(raw)
                if t:
                    vals.append(t)
            score = len(set(vals))
            if score > v_best:
                v_best, village_col = score, c

    rows = []
    for ri in range(nrows):
        if on_row:
            on_row(ri, nrows)                 # real per-row progress
        account = onnx_digits(ri, account_col)
        if not (11 <= len(account) <= 16):    # model unsure -> Tesseract digit re-read
            alt = _clean_digits(_ocr_cell(gray_np, ys[ri], ys[ri + 1],
                                          xs[account_col], xs[account_col + 1],
                                          whitelist="0123456789"))
            if 11 <= len(alt) <= 16:
                account = alt
        if not (11 <= len(account) <= 16):
            continue                          # header / blank band
        mobile = ""
        if mobile_col is not None:
            mobile = _valid_mobile(onnx_digits(ri, mobile_col))
        bm = _BAND_RE.search(text_cell(ri, bal_col)) if bal_col is not None else None
        name = ""
        if name_col is not None:
            s = _clean_text_cell(text_cell(ri, name_col)).upper()
            if s and not _BAND_RE.search(s) and not _valid_mobile(s) and len(_clean_digits(s)) < 5:
                name = s
        village = _clean_village(text_cell(ri, village_col)) if village_col is not None else ""
        rows.append({
            "account_number": account, "name": name,
            "balance_band": bm.group(0).replace(" ", "") if bm else "",
            "father_name": "", "mobile": mobile,
            "taluka": "", "village": village, "address": "", "_raw": "",
        })
    return rows or None


def _col_by(col_words, ncols, pred):
    """Index of the column whose words most often satisfy pred, else None."""
    scores = [sum(1 for w in col_words[c] if pred(w["t"])) for c in range(ncols)]
    i = max(range(ncols), key=lambda c: scores[c]) if ncols else -1
    return i if i >= 0 and scores[i] > 0 else None


def iter_training_cells(gray_np: np.ndarray):
    """DEV/TRAINING helper (NOT used by the runtime app): harvest REAL account/
    mobile cell crops from a bank scan for the custom model (see ../ocr_training/).
    Reuses the same account-anchored grid as _extract_grid. Yields
    (field, crop_uint8, tesseract_guess, engine_read)."""
    bw = cv2.adaptiveThreshold(~gray_np, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 15, -2)
    xs = _grid_line_positions(bw, axis=1, frac=12, thr=0.15, gap=15)
    if len(xs) < 6:
        return
    ncols = len(xs) - 1
    words, _ = _page_words(gray_np)
    if not words:
        return
    col_words = [[] for _ in range(ncols)]
    for w in words:
        for c in range(ncols):
            if xs[c] <= w["x"] < xs[c + 1]:
                col_words[c].append({"t": w["t"], "yc": w["yc"], "conf": w["conf"], "x": w["x"]})
                break
    account_col = _col_by(col_words, ncols, lambda t: 11 <= len(_clean_digits(t)) <= 16)
    mobile_col = _col_by(col_words, ncols, lambda t: bool(_valid_mobile(t)))
    if account_col is None:
        return
    acc_ws = sorted((w for w in col_words[account_col]
                     if 11 <= len(_clean_digits(w["t"])) <= 16), key=lambda w: w["yc"])
    anchors = [w["yc"] for w in acc_ws]
    if len(anchors) < 3:
        return
    gaps = [anchors[i + 1] - anchors[i] for i in range(len(anchors) - 1)]
    h = float(np.median(gaps)) if gaps else 55.0
    mob_col = mobile_col if mobile_col is not None else account_col + 4
    H = gray_np.shape[0]
    cap = h * 0.6
    grid_txt = {}
    for w in words:
        ri, best = None, cap
        for i, ay in enumerate(anchors):
            d = abs(w["yc"] - ay)
            if d < best:
                ri, best = i, d
        if ri is None:
            continue
        for c in range(ncols):
            if xs[c] <= w["x"] < xs[c + 1]:
                grid_txt.setdefault((ri, c), []).append(w)
                break

    def _eng_read(ri, c):
        ws = sorted(grid_txt.get((ri, c), []), key=lambda w: w["x"])
        return " ".join(w["t"] for w in ws)

    for ri, ay in enumerate(anchors):
        y0, y1 = max(0, int(ay - h / 2)), min(H, int(ay + h / 2))
        for field, c in (("account", account_col), ("mobile", mob_col)):
            if c is None or not (0 <= c < ncols):
                continue
            crop = gray_np[y0:y1, xs[c]:xs[c + 1]]
            if crop.size == 0:
                continue
            guess = _clean_digits(_ocr_cell(gray_np, y0, y1, xs[c], xs[c + 1], whitelist="0123456789"))
            yield field, crop, guess, _eng_read(ri, c)
