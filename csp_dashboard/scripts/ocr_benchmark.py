"""
Local OCR engine benchmark — PaddleOCR vs docTR, head to head.

WHY THIS EXISTS
    The real bank scan must never leave this PC (DPDP), so no cloud tool can
    tell us which OCR engine reads it more accurately. This script runs BOTH
    engines on the SAME page(s) entirely on the local machine and prints a
    cell-by-cell comparison, so the CSP/operator can decide which engine to keep
    (config.OCR_ENGINE) based on their own document — nothing is uploaded.

USAGE
    python scripts/ocr_benchmark.py path/to/document.pdf
    python scripts/ocr_benchmark.py path/to/scan.jpg
    python scripts/ocr_benchmark.py doc.pdf --from 1 --to 2      # PDF page range
    python scripts/ocr_benchmark.py doc.pdf --rows 15            # show N rows

WHAT IT REPORTS
    - Per engine: rows found, and how many have a valid mobile / valid account /
      a name / a village / an address (higher = more fields recovered).
    - A side-by-side table of the key fields per row, marking (!) where the two
      engines DISAGREE — those are exactly the cells to eyeball on the review
      screen.

    This is a decision aid, not ground truth. The review screen is still the
    final accuracy gate: every field is editable before any case is created.
"""

import argparse
import os
import sys

# Allow running as `python scripts/ocr_benchmark.py` from the project root.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.ocr_table as ot  # noqa: E402


def _pages(path, page_from, page_to):
    """Yield oriented grayscale np arrays for each page/image to test."""
    from core.ocr_table import extract_with_image, _deskew
    import numpy as np

    ext = os.path.splitext(path)[1].lower()
    ot._ensure_tesseract()

    if ext == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(path)
        try:
            total = len(pdf)
            lo = max(1, page_from or 1)
            hi = min(total, page_to or total)
            if lo > hi:
                lo, hi = 1, total
            angle = None
            for pno in range(lo - 1, hi):
                pil = pdf[pno].render(scale=400 / 72).to_pil()
                oriented, _, angle = extract_with_image(pil, angle)
                yield pno + 1, _deskew(np.array(oriented.convert("L")))
        finally:
            pdf.close()
    else:
        from PIL import Image
        oriented, _, _ = extract_with_image(Image.open(path))
        yield 1, _deskew(np.array(oriented.convert("L")))


def _extract_one_engine(gray_np, engine):
    """Force one engine (no fallback) and return its grid rows, or None if the
    engine isn't installed / produced nothing."""
    ot._ENGINE_OVERRIDE = engine
    try:
        rows = ot._extract_grid(gray_np)
    except Exception as e:
        print(f"  [{engine}] error: {e}")
        rows = None
    finally:
        ot._ENGINE_OVERRIDE = None
    return rows or []


def _summary(name, rows):
    valid_mob = sum(1 for r in rows if r["mobile"])
    valid_acc = sum(1 for r in rows if 10 <= len(r["account_number"]) <= 16)
    have_name = sum(1 for r in rows if r["name"].strip())
    have_vill = sum(1 for r in rows if r["village"].strip())
    have_addr = sum(1 for r in rows if r["address"].strip())
    print(f"  {name:8s}  rows={len(rows):3d}  mobile={valid_mob:3d}  "
          f"account={valid_acc:3d}  name={have_name:3d}  "
          f"village={have_vill:3d}  address={have_addr:3d}")


def _side_by_side(paddle_rows, doctr_rows, limit):
    n = max(len(paddle_rows), len(doctr_rows))
    disagree = 0
    print(f"\n  {'#':>3}  {'field':8s}  {'paddle':<34}  {'doctr':<34}")
    print("  " + "-" * 84)
    for i in range(min(n, limit)):
        p = paddle_rows[i] if i < len(paddle_rows) else {}
        d = doctr_rows[i] if i < len(doctr_rows) else {}
        for field in ("account_number", "mobile", "name", "village", "address"):
            pv = (p.get(field) or "")[:33]
            dv = (d.get(field) or "")[:33]
            mark = "" if pv == dv else "  (!)"
            if pv != dv:
                disagree += 1
            print(f"  {i+1:>3}  {field:8s}  {pv:<34}  {dv:<34}{mark}")
        print()
    return disagree


def main():
    ap = argparse.ArgumentParser(description="Compare PaddleOCR vs docTR locally.")
    ap.add_argument("path", help="PDF or image file to test")
    ap.add_argument("--from", dest="page_from", type=int, default=None)
    ap.add_argument("--to", dest="page_to", type=int, default=None)
    ap.add_argument("--rows", type=int, default=12, help="rows to show side by side")
    args = ap.parse_args()

    if not os.path.exists(args.path):
        print(f"File not found: {args.path}")
        sys.exit(1)

    print(f"\nBenchmarking OCR engines on: {args.path}")
    print("(fully local — nothing is uploaded)\n")

    total_disagree = 0
    for pno, gray_np in _pages(args.path, args.page_from, args.page_to):
        print(f"── Page {pno} ─────────────────────────────────────────────")
        paddle_rows = _extract_one_engine(gray_np, "paddle")
        doctr_rows = _extract_one_engine(gray_np, "doctr")

        if not paddle_rows:
            print("  [paddle] no rows — PaddleOCR not installed, or nothing read.")
        if not doctr_rows:
            print("  [doctr]  no rows — docTR not installed, or nothing read.")

        print("\n  Field-recovery counts (higher is better):")
        _summary("paddle", paddle_rows)
        _summary("doctr", doctr_rows)

        if paddle_rows and doctr_rows:
            total_disagree += _side_by_side(paddle_rows, doctr_rows, args.rows)

    print(f"\nTotal (!) cell disagreements shown: {total_disagree}")
    print("Pick the engine that recovers more correct fields, then set "
          "OCR_ENGINE in config.py.\n")


if __name__ == "__main__":
    main()
