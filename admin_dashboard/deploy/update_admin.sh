#!/usr/bin/env bash
# ============================================================
#  Update an already-installed admin portal (see install_admin.sh) to the
#  latest GitHub code and restart it.
#
#  Zero data loss: admin.db (API keys + fleet), secret.key and .env are all
#  gitignored, so `git pull` NEVER touches them. Only code advances.
#
#  Usage (on the RAG server):
#    bash /home/Prateek/csp_platform/admin_dashboard/deploy/update_admin.sh
# ============================================================
set -euo pipefail

APP_DIR="${1:-/home/Prateek/csp_platform}"
cd "$APP_DIR"

echo "== Pulling latest code =="
git pull --ff-only

echo "== Ensuring deps =="
.venv_linux/bin/pip install -q flask python-dotenv
# Centralized-OCR service deps (numpy/opencv/onnxtr/pypdfium2/cryptography/...).
# Best-effort: the portal boots WITHOUT them — /api/v1/ocr/extract lazy-imports
# the OCR stack and returns 503 if absent — so a failure here must NOT abort the
# deploy or take the fleet-heartbeat endpoints down.
.venv_linux/bin/pip install -q -r admin_dashboard/requirements-ocr-server.txt \
    || echo "  (OCR deps install failed — OCR endpoint will 503 until fixed; portal still runs)"

echo "== Restarting =="
chmod +x admin_dashboard/deploy/*.sh
ADMIN_BIND_PORT="${ADMIN_BIND_PORT:-7000}" ./admin_dashboard/deploy/restart_admin.sh
