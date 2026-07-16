"""
Eko-side OFFLINE converter:  scanned bank PDF  ->  clean CSV.

Runs the ACCURATE docTR engine on a CAPABLE Eko workstation (docTR + torch
installed) — NOT the 4 GB CSP box, and NOT any cloud/online service. The output
CSV uses the column names the CSP app's CSV upload understands, so the CSP simply
uploads it (no OCR on their weak box) and gets clean, accurate cases.

    python scripts/pdf_to_csv.py  INPUT.pdf  OUTPUT.csv  [--pages 1-3]  [--dpi 300]

Examples
    python scripts/pdf_to_csv.py "data/scan.pdf" out.csv --pages 1-2   # test 2 pages
    python scripts/pdf_to_csv.py "data/scan.pdf" alamgir.csv           # whole file

DPDP: the input PDF and the output CSV both hold customer PII. Keep them on the
Eko machine, run FULLY OFFLINE, hand the CSV to the CSP over a secure channel,
and delete the working copies afterwards. No cloud, no third party — this is the
same data staying inside the bank -> CSP -> Eko(processor) chain.
"""
import argparse
import csv
import os
import re
import sys

# Resolve the app dir so `import config` / `core.*` work, and remember the
# caller's cwd so relative INPUT/OUTPUT paths still make sense after we chdir.
_ORIG_CWD = os.getcwd()
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)              # .../csp_dashboard
sys.path.insert(0, _APP)
os.chdir(_APP)

import config  # noqa: E402
# FORCE the accurate engine + full DPI regardless of THIS machine's free RAM
# (the RAM-based auto-pick is only for the CSP box; here we always want docTR).
config.OCR_ENGINE = "doctr"
config.OCR_RENDER_DPI = 300

from core.ocr_table import extract_rows_from_pil  # noqa: E402

# (internal field key, CSV header). The CSP upload maps by keyword, so these
# headers map straight back to account_number / name / mobile / balance_band;
# the extra columns are for the human check and are ignored on re-upload.
COLUMNS = [
    ("account_number", "Account Number"),
    ("name",           "Name"),
    ("mobile",         "Mobile"),
    ("balance_band",   "Balance Band"),
    ("father_name",    "Father Name"),
    ("village",        "Village"),
    ("taluka",         "Taluka"),
    ("address",        "Address"),
]


def _abs(p):
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(_ORIG_CWD, p))


def _clean_name(name: str) -> str:
    """Strip a trailing standalone 'X' that a narrow flag-column bleeds onto the
    name (docTR reads it; e.g. 'SHANKAR X' -> 'SHANKAR'). Bare 'X' -> ''."""
    n = re.sub(r"\s+X\s*$", "", str(name or "").strip(), flags=re.IGNORECASE).strip()
    return "" if n.upper() == "X" else n


def _parse_pages(spec, n):
    if not spec:
        return list(range(n))
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a) - 1, int(b)))
        elif part:
            out.add(int(part) - 1)
    return sorted(i for i in out if 0 <= i < n)


def main():
    ap = argparse.ArgumentParser(description="Offline docTR PDF->CSV converter")
    ap.add_argument("input", help="scanned bank PDF")
    ap.add_argument("output", help="CSV to write")
    ap.add_argument("--pages", default="", help="e.g. 1-3 or 1,4,5 (default: all)")
    ap.add_argument("--dpi", type=int, default=300)
    args = ap.parse_args()

    config.OCR_RENDER_DPI = args.dpi
    inp, outp = _abs(args.input), _abs(args.output)

    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(inp)
    n = len(pdf)
    pages = _parse_pages(args.pages, n)
    print(f"docTR converter | {inp}")
    print(f"  {n} pages total, processing {len(pages)} at {args.dpi} DPI\n")

    # Stream to the CSV page-by-page (write header first, flush after every page)
    # so the file APPEARS immediately and GROWS as it goes — no waiting for the
    # whole run before anything is visible, and a partial file is still usable.
    total = 0
    with open(outp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([h for _, h in COLUMNS])
        f.flush()
        try:
            for pi in pages:
                page = pdf[pi]
                img = page.render(scale=args.dpi / 72).to_pil()
                try:
                    r = extract_rows_from_pil(img) or []
                finally:
                    try:
                        img.close()
                    except Exception:
                        pass
                for row in r:
                    row["name"] = _clean_name(row.get("name", ""))
                    w.writerow([str(row.get(k, "") or "").strip() for k, _ in COLUMNS])
                f.flush()
                os.fsync(f.fileno())
                total += len(r)
                print(f"  page {pi + 1}: {len(r)} rows  (total {total})", flush=True)
        finally:
            pdf.close()

    print(f"\nDONE. Wrote {total} rows -> {outp}", flush=True)
    print("Hand this CSV to the CSP -> they upload it in the dashboard "
          "(no OCR on their box). Delete working copies after.", flush=True)


if __name__ == "__main__":
    main()
