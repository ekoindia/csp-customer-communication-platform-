"""
Synthetic training data for the on-device digit recognizer.

Renders random digit strings in many fonts, then degrades them to mimic a
DocScanner-style phone capture (perspective warp, rotation, blur, sensor noise,
uneven lighting, low contrast, JPEG blocking, stroke-weight change) AND draws
ruled grid-line remnants at the edges (real cell crops include a sliver of the
table's border, which the model otherwise misreads as an extra leading digit).

Charset is digits only ('0'-'9') — all the mobile/account fields need.
"""
import glob
import os
import random

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

IMG_H = 32
IMG_W = 192
CHARS = "0123456789"
BLANK = len(CHARS)          # CTC blank index = 10
NUM_CLASSES = len(CHARS) + 1

_FONT_PATHS = []
for _p in glob.glob(r"C:\Windows\Fonts\*.ttf"):
    _name = os.path.basename(_p).lower()
    if any(bad in _name for bad in ("wingding", "webding", "symbol", "marlett",
                                    "bssym", "holomdl", "segmdl", "mtextra")):
        continue
    _FONT_PATHS.append(_p)
if not _FONT_PATHS:
    _FONT_PATHS = [None]


def random_digits(rng) -> str:
    """A digit string like a mobile (10, starts 6-9) or account (9-16)."""
    if rng.random() < 0.55:
        return rng.choice("6789") + "".join(rng.choice(CHARS) for _ in range(9))
    n = rng.randint(9, 16)
    return "".join(rng.choice(CHARS) for _ in range(n))


def _render_clean(text, rng) -> np.ndarray:
    font_path = rng.choice(_FONT_PATHS)
    size = rng.randint(26, 40)
    try:
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    tmp = Image.new("L", (10, 10), 255)
    d = ImageDraw.Draw(tmp)
    try:
        l, t, r, b = d.textbbox((0, 0), text, font=font)
        tw, th = r - l, b - t
    except Exception:
        tw, th = (len(text) * size // 2, size)
        l = t = 0
    pad_x, pad_y = rng.randint(4, 14), rng.randint(4, 12)
    bg = rng.randint(225, 255)
    img = Image.new("L", (tw + 2 * pad_x, th + 2 * pad_y), bg)
    d = ImageDraw.Draw(img)
    d.text((pad_x - l, pad_y - t), text, fill=rng.randint(0, 60), font=font)
    return np.array(img)


def _warp_perspective(img, rng):
    h, w = img.shape
    m = 0.10
    def j(v):
        return v * rng.uniform(-m, m)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[j(w), j(h)], [w + j(w), j(h)], [w + j(w), h + j(h)], [j(w), h + j(h)]])
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, M, (w, h), borderValue=int(img[0, 0]), flags=cv2.INTER_LINEAR)


def _shadow(img, rng):
    h, w = img.shape
    grad = np.tile(np.linspace(rng.uniform(0.6, 1.0), rng.uniform(0.6, 1.0), w), (h, 1))
    return np.clip(img.astype(np.float32) * grad, 0, 255).astype(np.uint8)


def _degrade(img, rng) -> np.ndarray:
    if rng.random() < 0.9:
        img = _warp_perspective(img, rng)
    if rng.random() < 0.7:
        h, w = img.shape
        M = cv2.getRotationMatrix2D((w / 2, h / 2), rng.uniform(-4, 4), 1.0)
        img = cv2.warpAffine(img, M, (w, h), borderValue=int(img[0, 0]))
    if rng.random() < 0.6:
        img = _shadow(img, rng)
    if rng.random() < 0.5:
        k = rng.choice([3, 3, 5])
        img = cv2.GaussianBlur(img, (k, k), 0)
    if rng.random() < 0.3:
        kern = np.ones((2, 2), np.uint8)
        img = cv2.erode(img, kern) if rng.random() < 0.5 else cv2.dilate(img, kern)
    if rng.random() < 0.6:
        noise = rng.uniform(4, 22)
        img = np.clip(img.astype(np.float32) + np.random.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    if rng.random() < 0.4:
        lo, hi = rng.randint(0, 60), rng.randint(190, 255)
        img = np.clip((img.astype(np.float32) - lo) * 255.0 / max(1, hi - lo), 0, 255).astype(np.uint8)
    if rng.random() < 0.5:
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(25, 70)])
        if ok:
            img = cv2.imdecode(enc, cv2.IMREAD_GRAYSCALE)
    return img


def _add_borders(img, rng) -> np.ndarray:
    """Draw ruled grid-line remnants at the edges — real cell crops include a
    sliver of the column border, which the model otherwise misreads as an extra
    leading/trailing digit. Teaches it to ignore the border. No real labels needed."""
    h, w = img.shape
    ink = rng.randint(0, 70)
    if rng.random() < 0.75:
        x = rng.randint(0, max(1, w // 12)); th = rng.randint(1, 3)
        img[:, x:x + th] = ink
    if rng.random() < 0.55:
        x = max(0, w - rng.randint(2, max(3, w // 12))); th = rng.randint(1, 3)
        img[:, x:x + th] = ink
    if rng.random() < 0.45:
        img[0:rng.randint(1, 3), :] = ink
    if rng.random() < 0.45:
        img[h - rng.randint(1, 3):h, :] = ink
    return img


def _fit(img) -> np.ndarray:
    """Resize keeping aspect to height IMG_H, pad/crop width to IMG_W."""
    h, w = img.shape
    nw = max(1, int(round(w * IMG_H / h)))
    img = cv2.resize(img, (nw, IMG_H), interpolation=cv2.INTER_AREA)
    if nw >= IMG_W:
        img = cv2.resize(img, (IMG_W, IMG_H), interpolation=cv2.INTER_AREA)
    else:
        pad = np.full((IMG_H, IMG_W - nw), int(img[0, 0]), np.uint8)
        img = np.hstack([img, pad])
    return img


def make_sample(rng):
    """Return (float32 HxW image in [0,1], list[int] label)."""
    text = random_digits(rng)
    img = _render_clean(text, rng)
    img = _degrade(img, rng)
    img = _add_borders(img, rng)
    img = _fit(img)
    return img.astype(np.float32) / 255.0, [CHARS.index(c) for c in text]
