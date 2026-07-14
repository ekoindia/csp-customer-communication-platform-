"""
Installer Stage 1 — the "backend check".

Run this FIRST on a target CSP machine (remotely). It probes the hardware and
prints/writes an install PLAN: exactly which modules + models the machine can
run, so the weak 4 GB deploy PC never downloads the ~2 GB PyTorch/docTR stack it
can't use. The installer (Stage 2) reads this plan and installs only what's
listed under "install", fetching the heavy extras only if "fetch_extra_mb" > 0.

    python installer/probe.py                 # human summary
    python installer/probe.py --json          # machine-readable plan
    python installer/probe.py --out plan.json  # also write the plan to a file

Stdlib only (RAM probe has a ctypes fallback), so it runs before any pip deps or
PyTorch are installed. Post-install, run deploy_check.py to confirm GO/NO-GO.
"""

import json
import os
import sys

# Make the repo root importable when run as `python installer/probe.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import hardware  # noqa: E402  (stdlib + config only; torch never imported)


def build_plan() -> dict:
    # Local Baileys bridge is the default today; flip to False once the official
    # WABA HTTP API is wired (then Node is not installed at all).
    local_bridge = True
    try:
        import config
        local_bridge = bool(getattr(config, "WA_LOCAL_BRIDGE", True))
    except Exception:
        pass
    return hardware.install_plan(whatsapp_local_bridge=local_bridge)


def main(argv):
    plan = build_plan()

    out_path = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out_path = argv[i + 1]
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)

    if "--json" in argv:
        print(json.dumps(plan, indent=2))
        return 0

    print("=" * 60)
    print(" Installer Stage 1 - hardware probe & install plan")
    print("=" * 60)
    print(f" RAM            : {plan['ram_gb']} GB ({plan['available_gb']} GB free)")
    print(f" CPU threads    : {plan['cpu_threads']}")
    print(f" NVIDIA GPU     : {'yes' if plan['gpu'] else 'no'}")
    print(f" PROFILE        : {plan['profile'].upper()}")
    print(f" OCR engine     : {plan['ocr_engine']}")
    print(f" Extra download : {plan['fetch_extra_mb']} MB")
    print("-" * 60)
    print(" INSTALL:")
    for c in plan["install"]:
        print("   + " + c)
    if plan["skip"]:
        print(" SKIP (not needed on this machine):")
        for c in plan["skip"]:
            print("   - " + c)
    print("-" * 60)
    for n in plan["notes"]:
        print(" * " + n)
    if out_path:
        print("-" * 60)
        print(f" plan written to: {out_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
