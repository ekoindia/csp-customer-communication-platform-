"""
Deploy preflight — run this ONCE on a CSP's machine after a remote install to
confirm the app will run there, WITHOUT a physical visit.

    python deploy_check.py

It prints a checklist and a final VERDICT: GO / GO-with-notes / NO-GO. Exit code
0 = safe to run, 1 = a blocking problem was found. Pure stdlib + the app's own
light modules — it never imports torch, so it is safe on a 4 GB machine.
"""

import importlib.util
import os
import platform
import shutil
import sys

RESULTS = []


def add(level, label, detail=""):
    RESULTS.append((level, label, detail))
    line = f"[{level:4}] {label}"
    if detail:
        line += f"  ->  {detail}"
    print(line)


print("=" * 64)
print(" CSP Platform - deploy preflight check")
print("=" * 64)

# 1. Python version
v = sys.version_info
add("PASS" if v >= (3, 9) else "FAIL",
    f"Python {v.major}.{v.minor}.{v.micro}",
    "" if v >= (3, 9) else "need Python >= 3.9")

# 2. OS
add("PASS", "Operating system", platform.platform())

# 3. Hardware profile + RAM (drives OCR mode)
try:
    import config
    from core import hardware
    p = hardware.profile()
    # Minimum-spec constants (single source of truth — config.py MIN_*).
    hard = getattr(config, "MIN_RAM_HARD_GB", 3.0)
    ocr_threshold = getattr(config, "OCR_RAM_THRESHOLD_GB", 6)
    min_free_ram = getattr(config, "MIN_FREE_RAM_GB", 0.8)
    add("PASS", "Hardware profile", hardware.summary_line())
    # Floor is 3.0, not 4.0: the confirmed 4 GB deploy PCs report ~3.8 GB total
    # because the integrated Intel GPU reserves shared memory, and a stricter
    # floor would false-FAIL a machine the app actually runs fine on (Tesseract
    # -only mode). Below MIN_RAM_HARD_GB it genuinely can't run reliably.
    if p["ram_gb"] < hard:
        add("FAIL", "Total RAM",
            f"{p['ram_gb']} GB - below the {hard} GB hard minimum")
    elif p["ram_gb"] < ocr_threshold:
        add("WARN", "Total RAM",
            f"{p['ram_gb']} GB - runs in light Tesseract-only OCR (no PyTorch)")
    else:
        add("PASS", "Total RAM", f"{p['ram_gb']} GB - docTR OCR available")
    if p["available_gb"] < min_free_ram:
        add("WARN", "Free RAM right now",
            f"{p['available_gb']} GB - close other apps before a big batch")
    else:
        add("PASS", "Free RAM right now", f"{p['available_gb']} GB")
except Exception as e:
    add("FAIL", "Hardware profile", str(e))

# 4. Disk space on the app drive
try:
    import config as _cfg
    min_disk = getattr(_cfg, "MIN_FREE_DISK_GB", 3.0)
    free = shutil.disk_usage(os.getcwd()).free / 1e9
    add("PASS" if free >= min_disk else "FAIL", "Disk free (app drive)",
        f"{free:.1f} GB" + ("" if free >= min_disk else f" - need ~{min_disk} GB"))
except Exception as e:
    add("WARN", "Disk free", str(e))

# 5. Required Python packages (lite profile - no torch/doctr needed)
deps = ["flask", "pydantic", "openpyxl", "pdfplumber", "PIL", "pytesseract",
        "requests", "pypdfium2", "numpy", "cv2"]
missing = [d for d in deps if importlib.util.find_spec(d) is None]
add("PASS" if not missing else "FAIL", "Python packages",
    "all present" if not missing else "MISSING: " + ", ".join(missing))
add("PASS" if importlib.util.find_spec("psutil") else "WARN", "psutil",
    "present" if importlib.util.find_spec("psutil") else
    "absent - using ctypes RAM fallback (fine)")

# 6. Tesseract engine (the OCR the deploy PC actually uses)
tess_ok = False
try:
    import core.ocr  # noqa: F401  (sets pytesseract path)
    import pytesseract
    add("PASS", "Tesseract OCR engine", f"v{pytesseract.get_tesseract_version()}")
    tess_ok = True
except Exception:
    add("WARN", "Tesseract OCR engine",
        "not found - only needed for SCANNED PDF/image OCR; Excel/CSV uploads "
        "work without it. Install Tesseract-OCR to enable scan reading.")

# 7. End-to-end OCR smoke test (only meaningful if Tesseract/docTR is present;
#    scanned-doc OCR is optional — CSV/Excel is the main, OCR-free path).
if not tess_ok:
    add("WARN", "OCR smoke test", "skipped - Tesseract not installed (scans only)")
else:
    try:
        import numpy as np
        from PIL import Image, ImageDraw
        img = Image.new("L", (720, 120), 255)
        ImageDraw.Draw(img).text((15, 45), "3577864748 RAMESH KUMAR 9876543210", fill=0)
        import core.ocr_table as ot
        words, engine = ot._page_words(np.array(img))
        add("PASS" if words else "WARN", f"OCR smoke test (mode: {engine})",
            f"{len(words) if words else 0} words read")
        try:
            import psutil
            rss = psutil.Process(os.getpid()).memory_info().rss / 1e6
            add("PASS" if rss < 1300 else "WARN", "Process RAM after OCR",
                f"{rss:.0f} MB")
        except Exception:
            pass
    except Exception as e:
        add("WARN", "OCR smoke test", str(e))

# 8. Database can initialise
try:
    from database.db import setup
    setup()
    add("PASS", "Local database init", "ok")
except Exception as e:
    add("FAIL", "Local database init", str(e))

# 9. Node.js (only needed for WhatsApp sending)
node = shutil.which("node")
add("PASS" if node else "WARN", "Node.js (WhatsApp bridge)",
    node or "not found - install Node.js only if sending via WhatsApp")

# ---- verdict ----
fails = [r for r in RESULTS if r[0] == "FAIL"]
warns = [r for r in RESULTS if r[0] == "WARN"]
print("=" * 64)
if fails:
    print("VERDICT: NO-GO  -  fix these before running:")
    for _, label, detail in fails:
        print(f"   - {label}" + (f": {detail}" if detail else ""))
elif warns:
    print("VERDICT: GO (with notes)  -  the app will run; notes above are FYI.")
else:
    print("VERDICT: GO  -  all checks passed. The app will run on this machine.")
print("=" * 64)
sys.exit(1 if fails else 0)
