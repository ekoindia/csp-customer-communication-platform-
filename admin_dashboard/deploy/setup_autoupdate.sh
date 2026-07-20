#!/usr/bin/env bash
# ============================================================
#  Turn ON fast auto-deploy for the admin portal (one-time setup).
#
#  Installs a cron job that runs auto_update_admin.sh every few minutes, so the
#  live portal auto-tracks GitHub `main` — every push is live within ~1-2 min
#  with no manual SSH. Idempotent: re-running just refreshes the single entry
#  (tagged so it never duplicates and never disturbs your other cron lines).
#
#  Usage (on the RAG server):
#    bash /home/Prateek/csp_platform/admin_dashboard/deploy/setup_autoupdate.sh
#    # optional: change the interval (minutes) or app dir
#    ADMIN_AUTOUPDATE_MIN=1 bash .../setup_autoupdate.sh /home/Prateek/csp_platform
#
#  Turn it OFF later:
#    crontab -l | grep -v '# csp-admin-autoupdate' | crontab -
# ============================================================
set -euo pipefail

APP_DIR="${1:-/home/Prateek/csp_platform}"
EVERY_MIN="${ADMIN_AUTOUPDATE_MIN:-2}"
SCRIPT="$APP_DIR/admin_dashboard/deploy/auto_update_admin.sh"
TAG="# csp-admin-autoupdate"

[ -f "$SCRIPT" ] || { echo "!! Not found: $SCRIPT (is APP_DIR correct?)"; exit 1; }
chmod +x "$APP_DIR/admin_dashboard/deploy/"*.sh

CRON_LINE="*/$EVERY_MIN * * * * bash $SCRIPT $APP_DIR >/dev/null 2>&1 $TAG"

# Rewrite the crontab: drop any prior csp-admin-autoupdate line, keep the rest,
# add the fresh one. (grep -v tolerates an empty/absent crontab.)
( crontab -l 2>/dev/null | grep -vF "$TAG" || true; echo "$CRON_LINE" ) | crontab -

echo "== Fast auto-deploy is ON =="
echo "   every $EVERY_MIN min:  $SCRIPT"
echo "   cron entry:"
crontab -l | grep -F "$TAG" | sed 's/^/     /'
echo
echo "== Running one deploy check now (pulls immediately if behind) =="
bash "$SCRIPT" "$APP_DIR" || true
echo "   log: $APP_DIR/admin_dashboard/_autoupdate.log"
echo
echo "Done. From now on every GitHub push goes live automatically within $EVERY_MIN min."
