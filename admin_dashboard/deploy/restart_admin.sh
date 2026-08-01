#!/usr/bin/env bash
# Restart the Eko admin portal on the RAG server. Frees port 7000 (kills the old
# process — whether it was the pre-restructure admin_portal/app.py or this
# admin_dashboard.app) and starts the current code fresh via nohup.
# nginx already proxies /csp-admin/ -> 127.0.0.1:7000, so nothing else changes.
set -euo pipefail

# repo root = two levels up from this script (admin_dashboard/deploy/..)
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PORT="${ADMIN_BIND_PORT:-7000}"
PY=".venv_linux/bin/python"
[ -x "$PY" ] || PY="python3"

# Centralized OCR runs in this process on a 40-vCPU box. We now favour PARALLEL
# pages over threads-per-page: several pages OCR at once (SERVER_OCR_MAX_CONCURRENCY)
# with a moderate per-job thread count, which uses the cores far better than one
# page hogging 16 threads. ~6 threads x ~8 concurrent jobs ~= 48 ~= the 40 cores.
export TORCH_MAX_THREADS="${TORCH_MAX_THREADS:-6}"

# Stronger ROW DETECTION on the server only. db_resnet50.onnx (100 MB) is already
# committed in csp_dashboard/core/models/, so this needs no download and cannot
# fail closed — core/ocr_table._build_bundled_onnxtr falls back to db_mobilenet if
# the file is ever absent. Recognition stays crnn_vgg16_bn (measured best on
# account/mobile digits), so this only fixes rows that db_mobilenet misses on
# dense pages. The 4 GB CSP box never sets this and keeps db_mobilenet.
export OCR_ONNXTR_DET="${OCR_ONNXTR_DET:-resnet50}"

# This box (and ONLY this box) may fetch OCR model weights it doesn't carry —
# needed if OCR_ONNXTR_HEAVY is ever switched on, since parseq is not bundled.
# Weights are software, not customer data, so DPDP is unaffected. A CSP box must
# never download models on its own, which is why the default stays 0 in config.py.
export OCR_ALLOW_MODEL_DOWNLOAD="${OCR_ALLOW_MODEL_DOWNLOAD:-1}"

# OCR_ONNXTR_HEAVY (db_resnet50 + parseq) is deliberately NOT exported yet: parseq
# recognition on CPU is much slower and must be latency-benchmarked on a real
# multi-page scan first (csp_dashboard/scripts/ocr_benchmark.py) so a big scan
# can't exceed SERVER_OCR_TIMEOUT_SEC. Flip it here once that is measured.

echo "Freeing port $PORT ..."
if command -v lsof >/dev/null 2>&1; then
    lsof -ti:"$PORT" | xargs -r kill 2>/dev/null || true
else
    pkill -f "admin_dashboard.app"  2>/dev/null || true
    pkill -f "admin_portal/app.py" 2>/dev/null || true   # legacy pre-restructure
fi
sleep 1

echo "Starting admin portal (admin_dashboard.app) on 127.0.0.1:$PORT ..."
# setsid + </dev/null fully detaches the server into its OWN session, so it
# SURVIVES the SSH session closing (a plain `nohup ... &` launched over SSH gets
# killed when the channel closes — that caused a 502 after deploys). nohup too,
# belt-and-suspenders.
setsid nohup "$PY" -m admin_dashboard.app < /dev/null > admin_dashboard/_run.log 2>&1 &
sleep 3

if lsof -ti:"$PORT" >/dev/null 2>&1 || curl -s -o /dev/null "http://127.0.0.1:$PORT/login"; then
    echo "OK - admin portal is up on 127.0.0.1:$PORT"
    echo "Public: http://122.176.147.78:8080/csp-admin/login"
else
    echo "!! Did not come up - check admin_dashboard/_run.log"
    tail -n 20 admin_dashboard/_run.log 2>/dev/null || true
    exit 1
fi
