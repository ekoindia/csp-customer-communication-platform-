#!/usr/bin/env bash
# ============================================================
#  FAST auto-deploy for the Eko admin portal.
#
#  Pulls the latest GitHub code and restarts the portal ONLY when `main` has
#  actually moved. When nothing changed it is a near-instant no-op (a single
#  `git fetch`), so a cron can run this every minute or two and keep the live
#  portal within ~1-2 min of every push — no more manual SSH per release, which
#  is exactly what let the live portal drift behind GitHub before.
#
#  Data-safe: admin.db (API keys + fleet), secret.key and .env are gitignored,
#  so the fast-forward NEVER touches them — only code advances.
#
#  Run by cron (see setup_autoupdate.sh) or by hand:
#    bash /home/Prateek/csp_platform/admin_dashboard/deploy/auto_update_admin.sh
# ============================================================
set -euo pipefail

# cron gives a minimal PATH — make sure git/flock/lsof/curl/setsid resolve.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

APP_DIR="${1:-/home/Prateek/csp_platform}"
BRANCH="${ADMIN_BRANCH:-main}"
LOG="$APP_DIR/admin_dashboard/_autoupdate.log"
LOCK="/tmp/csp_admin_autoupdate.lock"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# Never let a slow deploy (pip/restart) collide with the next cron tick.
exec 9>"$LOCK"
if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
    exit 0
fi

cd "$APP_DIR" || { log "APP_DIR not found: $APP_DIR"; exit 1; }

# A network hiccup is not an error — just try again on the next tick.
if ! git fetch --quiet origin "$BRANCH" 2>>"$LOG"; then
    log "git fetch failed (network?) — will retry next tick"
    exit 0
fi

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"
if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0        # already current — the common case, silent no-op
fi

log "new code ${LOCAL:0:7} -> ${REMOTE:0:7} : deploying"
if ! git merge --ff-only "origin/$BRANCH" >>"$LOG" 2>&1; then
    log "ff-only merge failed (local repo diverged?) — manual check needed"
    exit 1
fi

# Keep deps in sync (a few-second no-op unless a release added a library).
.venv_linux/bin/pip install -q flask python-dotenv >>"$LOG" 2>&1 || log "pip step warned (continuing)"
# Centralized-OCR service deps. Best-effort: the portal boots WITHOUT them (the
# OCR endpoint lazy-imports its stack and returns 503 if absent), so a failure
# here must NEVER block the deploy/restart of the fleet portal. Normally a
# fast no-op once installed; only the first sync after the OCR release is slow.
.venv_linux/bin/pip install -q -r admin_dashboard/requirements-ocr-server.txt >>"$LOG" 2>&1 \
    || log "OCR deps install warned (OCR endpoint may be 503 until fixed; portal unaffected)"

chmod +x admin_dashboard/deploy/*.sh
if ADMIN_BIND_PORT="${ADMIN_BIND_PORT:-7000}" ./admin_dashboard/deploy/restart_admin.sh >>"$LOG" 2>&1; then
    log "deployed ${REMOTE:0:7} — portal restarted OK"
else
    log "restart FAILED after pulling ${REMOTE:0:7} — check admin_dashboard/_run.log"
    exit 1
fi
