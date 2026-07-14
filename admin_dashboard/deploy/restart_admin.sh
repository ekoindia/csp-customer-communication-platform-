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

echo "Freeing port $PORT ..."
if command -v lsof >/dev/null 2>&1; then
    lsof -ti:"$PORT" | xargs -r kill 2>/dev/null || true
else
    pkill -f "admin_dashboard.app"  2>/dev/null || true
    pkill -f "admin_portal/app.py" 2>/dev/null || true   # legacy pre-restructure
fi
sleep 1

echo "Starting admin portal (admin_dashboard.app) on 127.0.0.1:$PORT ..."
nohup "$PY" -m admin_dashboard.app > admin_dashboard/_run.log 2>&1 &
sleep 2

if lsof -ti:"$PORT" >/dev/null 2>&1 || curl -s -o /dev/null "http://127.0.0.1:$PORT/login"; then
    echo "OK - admin portal is up on 127.0.0.1:$PORT"
    echo "Public: http://122.176.147.78:8080/csp-admin/login"
else
    echo "!! Did not come up - check admin_dashboard/_run.log"
    tail -n 20 admin_dashboard/_run.log 2>/dev/null || true
    exit 1
fi
