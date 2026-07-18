"""
Hardware-aware runtime profile.

The same build ships to very different CSP machines — from a 16 GB dev box with
a discrete GPU down to a 4 GB Dell Inspiron with an i3 and no GPU. This module
detects the machine it is actually running on and picks safe defaults so the
software never has to be hand-tuned per install (and never OOMs a small box):

  • OCR engine   — docTR (deep learning, ~1 GB resident) needs headroom. On a
                   machine below OCR_RAM_THRESHOLD_GB we fall back to a
                   Tesseract-only word reader (~150 MB, no PyTorch) that feeds
                   the SAME accurate grid logic. The review gate + digit
                   cross-check still catch misreads.
  • docTR model  — the accurate "parseq" backbone is practical only on a GPU;
                   on CPU we use the lighter, much faster "crnn_vgg16_bn".
  • Torch threads— capped so a 2-core/4-thread i3 isn't oversubscribed.

Everything can be forced from config.py (set OCR_ENGINE / DOCTR_RECO_ARCH to an
explicit value instead of "auto"); this module only decides when they're "auto".

Deliberately light: NO torch import here (that alone costs ~450 MB) — CUDA
detection is passed in from ocr_table, which imports torch lazily anyway.
"""

import os

import config

_RAM_GB = None


def total_ram_gb() -> float:
    """Total physical RAM in GB. Cached. psutil first, ctypes fallback so it
    still works if psutil isn't installed on a stripped-down deploy PC."""
    global _RAM_GB
    if _RAM_GB is not None:
        return _RAM_GB
    ram = None
    try:
        import psutil
        ram = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        try:  # Windows without psutil
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            ms = _MS()
            ms.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            ram = ms.ullTotalPhys / (1024 ** 3)
        except Exception:
            # Unknown -> assume a SMALL box (4 GB), so the OCR engine falls back
            # to the light Tesseract-only path. Erring "capable" here would load
            # docTR/PyTorch (~1 GB) on a machine we couldn't measure and could
            # OOM a 4 GB CSP PC — the exact failure this project must avoid.
            ram = 4.0
    _RAM_GB = round(ram, 1)
    return _RAM_GB


def available_ram_gb() -> float:
    """Currently-FREE RAM in GB (NOT cached — it changes). Used as a safety
    valve: even a big machine won't load docTR if little is free right now."""
    try:
        import psutil
        return round(psutil.virtual_memory().available / (1024 ** 3), 1)
    except Exception:
        # Can't measure free RAM -> don't over-restrict; total already gates.
        return total_ram_gb()


def cpu_threads() -> int:
    return os.cpu_count() or 4


def has_nvidia_gpu() -> bool:
    """Cheap CUDA hint for INSTALL planning WITHOUT importing torch (that alone
    costs ~450 MB): is the `nvidia-smi` tool on PATH? The runtime still confirms
    with torch.cuda at OCR time; this only decides which build to install."""
    import shutil
    return shutil.which("nvidia-smi") is not None


def torch_threads() -> int:
    """Cap OCR threads so a small CPU isn't oversubscribed (which on a 2-core
    i3 actually slows things down and spikes memory)."""
    cap = getattr(config, "TORCH_MAX_THREADS", 4)
    return max(1, min(cap, cpu_threads()))


def resolve_ocr_engine() -> str:
    """Return the OCR engine for scanned pages: "doctr" | "onnxtr" | "tesseract"
    | "paddle". An explicit config.OCR_ENGINE wins; "auto" picks by RAM.

    "auto" policy:
      • big box with RAM free  -> docTR (PyTorch, dev/high-end machine);
      • otherwise (the 4 GB CPU-only CSP box) -> ONNXTR when its bundled models
        are present — deep-learning accuracy at ~700 MB, NO PyTorch — else the
        light Tesseract reader as a last resort.
    """
    engine = str(getattr(config, "OCR_ENGINE", "auto")).lower()
    if engine in ("doctr", "tesseract", "paddle", "onnxtr"):
        return engine  # explicit override wins, guards skipped
    threshold = getattr(config, "OCR_RAM_THRESHOLD_GB", 6)
    min_free = getattr(config, "DOCTR_MIN_FREE_RAM_GB", 2.5)
    # docTR only when the machine is BOTH big enough overall AND has enough free
    # RAM right now — so a low-RAM box (or a bigger box under memory pressure)
    # never OOMs on PyTorch.
    if total_ram_gb() >= threshold and available_ram_gb() >= min_free:
        return "doctr"
    # Low-RAM / CPU box: prefer the accurate ONNX Runtime engine (fits 4 GB, no
    # PyTorch) when its models are bundled; fall back to Tesseract otherwise.
    try:
        from core import ocr_table
        if ocr_table.onnxtr_available():
            return "onnxtr"
    except Exception:
        pass
    return "tesseract"


def render_dpi() -> int:
    """DPI to render a scanned PDF page before OCR. Peak RAM for a page image
    grows with DPI SQUARED, so on the 4 GB deploy PC (often only ~400-500 MB
    free) a 300-DPI full-page render is the single biggest OCR memory spike and
    pushes the box into swap. Below OCR_RAM_THRESHOLD_GB — or when very little
    RAM is free right now — render lower (default 220 DPI ≈ 46% less pixels) so
    it fits; a capable box keeps 300 for max sharpness. Explicit
    config.OCR_RENDER_DPI (an int) overrides the auto choice entirely."""
    override = getattr(config, "OCR_RENDER_DPI", "auto")
    try:
        return int(override)  # explicit pin wins
    except (TypeError, ValueError):
        pass  # "auto"
    low = int(getattr(config, "OCR_LOW_RAM_DPI", 220))
    high = int(getattr(config, "OCR_HIGH_RAM_DPI", 300))
    threshold = getattr(config, "OCR_RAM_THRESHOLD_GB", 6)
    if total_ram_gb() < threshold or available_ram_gb() < 1.0:
        return low
    return high


def resolve_reco_arch(cuda_available: bool) -> str:
    """docTR recognition backbone. Explicit config.DOCTR_RECO_ARCH wins; "auto"
    uses the accurate "parseq" only on a GPU, else the light CPU-friendly one."""
    arch = str(getattr(config, "DOCTR_RECO_ARCH", "auto")).lower()
    if arch and arch != "auto":
        return arch
    return "parseq" if cuda_available else "crnn_vgg16_bn"


def apply_runtime_caps():
    """Cap OpenMP/BLAS thread counts to the CPU budget so a 2-core/4-thread i3
    isn't oversubscribed. Mainly reins in Tesseract (read as OMP_THREAD_LIMIT by
    its subprocess at exec time, so this works whenever it's set before OCR).
    Best-effort for numpy/OpenBLAS — those read their env at import, which may
    already have happened; call this as early as possible at startup."""
    n = str(torch_threads())
    for var in ("OMP_THREAD_LIMIT", "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, n)


def profile() -> dict:
    return {
        "ram_gb": total_ram_gb(),
        "available_gb": available_ram_gb(),
        "cpu_threads": cpu_threads(),
        "torch_threads": torch_threads(),
        "ocr_engine": resolve_ocr_engine(),
        "low_ram": total_ram_gb() < getattr(config, "OCR_RAM_THRESHOLD_GB", 6),
    }


def install_plan(whatsapp_local_bridge: bool = True) -> dict:
    """Stage-1 installer brain: probe this machine and return WHAT to install,
    so a weak PC never downloads the heavy ML stack it can't run.

    Rule: below OCR_RAM_THRESHOLD_GB -> "lite" (Tesseract-only, PyTorch + docTR
    SKIPPED, ~2 GB saved, no OOM). At/above -> docTR added (GPU build + accurate
    "parseq" if an NVIDIA GPU is seen, else CPU build + light "crnn_vgg16_bn").
    `whatsapp_local_bridge` adds Node only if the local Baileys bridge is used;
    with the official WABA HTTP API it stays pure-Python (no Node)."""
    p = profile()
    ram = p["ram_gb"]
    threshold = getattr(config, "OCR_RAM_THRESHOLD_GB", 6)
    base = ["python-runtime", "flask-web", "numpy", "opencv-headless",
            "pypdfium2", "pytesseract", "pillow", "psutil", "tesseract-ocr-binary"]
    plan = {
        "ram_gb": ram, "available_gb": p["available_gb"],
        "cpu_threads": p["cpu_threads"], "gpu": has_nvidia_gpu(),
        "ocr_engine": p["ocr_engine"],
        "install": list(base), "skip": [], "fetch_extra_mb": 0, "notes": [],
    }
    if ram >= threshold:
        gpu = plan["gpu"]
        plan["profile"] = "full-gpu" if gpu else "standard-cpu"
        reco = "parseq" if gpu else "crnn_vgg16_bn"
        plan["install"] += [("torch-cuda" if gpu else "torch-cpu"),
                            "python-doctr", "doctr-model:" + reco]
        plan["fetch_extra_mb"] = 2500 if gpu else 2000
        plan["notes"].append(
            f"RAM {ram} GB >= {threshold} GB -> docTR deep-learning OCR "
            f"({'GPU/parseq' if gpu else 'CPU/crnn_vgg16_bn'}).")
    else:
        plan["profile"] = "lite"
        plan["skip"] += ["torch", "torchvision", "python-doctr", "doctr-models"]
        plan["notes"].append(
            f"RAM {ram} GB < {threshold} GB -> LITE: Tesseract-only OCR; "
            f"PyTorch + docTR skipped (~2 GB saved, no OOM risk).")
    if whatsapp_local_bridge:
        plan["install"] += ["nodejs-runtime", "whatsapp-baileys-bridge"]
        plan["notes"].append(
            "Local Baileys bridge -> Node.js bundled. Switching to official WABA "
            "HTTP API drops Node entirely (pure-Python install).")
    else:
        plan["notes"].append("WhatsApp via WABA HTTP API -> no local Node needed.")
    return plan


def summary_line() -> str:
    p = profile()
    mode = ("Tesseract-only (low-RAM, no PyTorch)"
            if p["ocr_engine"] == "tesseract" else p["ocr_engine"])
    return (f"Hardware profile: {p['ram_gb']} GB RAM ({p['available_gb']} GB "
            f"free), {p['cpu_threads']} CPU threads -> OCR engine: {mode}; "
            f"torch threads capped at {p['torch_threads']}.")
