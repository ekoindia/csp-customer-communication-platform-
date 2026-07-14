"""Produce CLEAN line-cropped training cells for fine-tuning: crop cells from the
ruled grid lines (how the deploy path crops), labelled from docTR's accurate
FULL-PAGE read aligned by digit-similarity. DPDP: real_cells/ is gitignored.
Usage: python label_line_cells.py [start_page] [end_page]"""
import csv
import os
import sys
from difflib import SequenceMatcher

import numpy as np
import cv2
from PIL import Image
import pypdfium2 as pdfium

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "csp_dashboard"))
import config  # noqa: E402
config.OCR_ENGINE = "doctr"
from core.ocr_table import (_grid_line_positions, detect_angle, _deskew,      # noqa: E402
                            extract_with_image, _clean_digits, _valid_mobile, _ensure_tesseract)
from core import ocr_onnx  # noqa: E402
_ensure_tesseract()
CELLS = os.path.join(HERE, "real_cells")
PDF = os.path.join(os.path.dirname(HERE), "csp_dashboard", "data", "DocScanner Jun 25, 2026 10-04 AM.pdf")


def sim(a, b):
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def main():
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    for f in ("account", "mobile"):
        os.makedirs(os.path.join(CELLS, f), exist_ok=True)
    out = []
    pdf = pdfium.PdfDocument(PDF)
    for pno in range(start - 1, min(end, len(pdf))):
        pil = pdf[pno].render(scale=300 / 72).to_pil().convert("L")
        pil = pil.rotate(-detect_angle(pil), expand=True)
        _, dt_rows, _ = extract_with_image(pil)
        dt = [(r["account_number"], r["mobile"]) for r in dt_rows if r["account_number"]]
        g = _deskew(np.array(pil))
        bw = cv2.adaptiveThreshold(~g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
        xs = _grid_line_positions(bw, axis=1, frac=12, thr=0.15, gap=15)
        ys = _grid_line_positions(bw, axis=0, frac=12, thr=0.15, gap=15)
        if len(xs) < 6 or len(ys) < 8:
            print(f"  page {pno+1}: no ruled grid"); continue
        ncols = len(xs) - 1
        def onnx(ri, ci): return _clean_digits(ocr_onnx.recognize(g[ys[ri]:ys[ri+1], xs[ci]:xs[ci+1]]))
        acc_s = [0]*ncols; mob_s = [0]*ncols
        for ci in range(ncols):
            for ri in range(min(len(ys)-1, 20)):
                d = onnx(ri, ci)
                if 10 <= len(d) <= 16: acc_s[ci] += 1
                if _valid_mobile(d): mob_s[ci] += 1
        acc_col = max(range(ncols), key=lambda c: acc_s[c]); mob_col = max(range(ncols), key=lambda c: mob_s[c])
        kept = 0
        for ri in range(len(ys) - 1):
            rough = onnx(ri, acc_col)
            if not (10 <= len(rough) <= 16):
                continue
            best, bestr = None, 0.0
            for a, m in dt:
                r = sim(rough, a)
                if r > bestr: best, bestr = (a, m), r
            if not best or bestr < 0.6:
                continue
            acc_lbl, mob_lbl = best
            fn = f"L{pno+1:02d}_{ri:03d}"
            Image.fromarray(g[ys[ri]:ys[ri+1], xs[acc_col]:xs[acc_col+1]]).save(os.path.join(CELLS, "account", fn + ".png"))
            out.append({"file": f"account/{fn}.png", "field": "account", "guess": rough, "eng": acc_lbl, "label": acc_lbl})
            Image.fromarray(g[ys[ri]:ys[ri+1], xs[mob_col]:xs[mob_col+1]]).save(os.path.join(CELLS, "mobile", fn + ".png"))
            out.append({"file": f"mobile/{fn}.png", "field": "mobile", "guess": onnx(ri, mob_col), "eng": mob_lbl, "label": _valid_mobile(mob_lbl) or "-"})
            kept += 1
        print(f"  page {pno+1}: docTR rows={len(dt)} clean line-cells={kept}")
    pdf.close()
    with open(os.path.join(CELLS, "labels.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "field", "guess", "eng", "label"]); w.writeheader(); w.writerows(out)
    print(f"labels.csv: {len(out)} clean line-framed cells")


if __name__ == "__main__":
    main()
