"""
On-device digit recogniser (inference) — the runtime half of the custom OCR
model trained in ../ocr_training/. Runs the exported CRNN via **onnxruntime on
CPU** (no PyTorch on the CSP box): given a cropped account/mobile cell it returns
the digit string. Tiny (~1.5 MB model), fast (~ms/cell), fits the 4 GB i3.

Loads lazily and degrades gracefully: if onnxruntime or the model file is
missing, recognize() returns "" so the caller falls back to Tesseract — never
crashes the app.
"""
import os
import threading

import numpy as np

CHARS = "0123456789"
_BLANK = len(CHARS)
IMG_H, IMG_W = 32, 192

_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "crnn.onnx")
_SESSION = None
_LOCK = threading.Lock()
_TRIED = False


def _session():
    global _SESSION, _TRIED
    if _SESSION is not None or _TRIED:
        return _SESSION
    with _LOCK:
        if _SESSION is not None or _TRIED:
            return _SESSION
        _TRIED = True
        try:
            import onnxruntime as ort
            if not os.path.exists(_MODEL_PATH):
                return None
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2       # i3 = 2 cores / 4 threads
            so.inter_op_num_threads = 1
            _SESSION = ort.InferenceSession(_MODEL_PATH, so, providers=["CPUExecutionProvider"])
        except Exception:
            _SESSION = None
    return _SESSION


def available() -> bool:
    return _session() is not None


def _fit(gray: np.ndarray) -> np.ndarray:
    import cv2
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if h == 0 or w == 0:
        return np.full((IMG_H, IMG_W), 255, np.uint8)
    nw = max(1, int(round(w * IMG_H / h)))
    g = cv2.resize(gray, (nw, IMG_H), interpolation=cv2.INTER_AREA)
    if nw >= IMG_W:
        g = cv2.resize(g, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA)
    else:
        pad = np.full((IMG_H, IMG_W - nw), int(g[0, 0]), np.uint8)
        g = np.hstack([g, pad])
    return g


def _greedy_ctc(logits: np.ndarray) -> str:
    idx = logits.argmax(axis=1)
    out, prev = [], -1
    for v in idx.tolist():
        if v != prev and v != _BLANK and 0 <= v < len(CHARS):
            out.append(CHARS[v])
        prev = v
    return "".join(out)


def recognize(gray_crop: np.ndarray) -> str:
    """Recognise the digit string in one grayscale cell crop; "" if unavailable/error."""
    sess = _session()
    if sess is None or gray_crop is None or getattr(gray_crop, "size", 0) == 0:
        return ""
    try:
        x = _fit(np.asarray(gray_crop)).astype(np.float32) / 255.0
        logits = sess.run(None, {"image": x[None, None, :, :]})[0][0]
        return _greedy_ctc(logits)
    except Exception:
        return ""
