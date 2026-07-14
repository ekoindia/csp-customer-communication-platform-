#!/usr/bin/env bash
# ============================================================
#  One-time install / structure-migration of the Eko admin portal on the RAG
#  server, INTO the existing path (/home/Prateek/csp_platform).
#
#  Safe by design:
#    - Preserves the live admin.db (issued API keys + fleet data), secret.key,
#      and .env — whether the old dir used the pre-restructure admin_portal/
#      layout or the new admin_dashboard/ one.
#    - Backs the whole old dir up to <dir>.bak-<timestamp> (never deletes) so a
#      rollback is one `mv` away.
#    - Lays down the CURRENT code straight from the public GitHub repo, then
#      restores the preserved state into the new admin_dashboard/ layout.
#
#  Usage (on the RAG server):
#    bash install_admin.sh                 # uses /home/Prateek/csp_platform
#    bash install_admin.sh /custom/path
#  Or one-liner (fetch + run):
#    curl -sL https://raw.githubusercontent.com/ekoindia/csp-customer-communication-platform-/main/admin_dashboard/deploy/install_admin.sh | bash
# ============================================================
set -euo pipefail

REPO="https://github.com/ekoindia/csp-customer-communication-platform-.git"
APP_DIR="${1:-/home/Prateek/csp_platform}"
TS="$(date +%Y%m%d-%H%M%S)"
KEEP="$(mktemp -d)"

echo "== Preserving live state from $APP_DIR (if present) =="
for p in "$APP_DIR/admin_dashboard/admin.db" "$APP_DIR/admin_portal/admin.db" "$APP_DIR/admin.db"; do
    [ -f "$p" ] && { cp "$p" "$KEEP/admin.db"; echo "  kept admin.db   <- $p"; break; }
done
for p in "$APP_DIR/admin_dashboard/secret.key" "$APP_DIR/admin_portal/secret.key" "$APP_DIR/secret.key"; do
    [ -f "$p" ] && { cp "$p" "$KEEP/secret.key"; echo "  kept secret.key <- $p"; break; }
done
for p in "$APP_DIR/csp_dashboard/.env" "$APP_DIR/.env"; do
    [ -f "$p" ] && { cp "$p" "$KEEP/.env"; echo "  kept .env       <- $p"; break; }
done

echo "== Backing up old dir -> ${APP_DIR}.bak-$TS =="
[ -e "$APP_DIR" ] && mv "$APP_DIR" "${APP_DIR}.bak-$TS"

echo "== Cloning current code from GitHub =="
git clone --depth 1 "$REPO" "$APP_DIR"
cd "$APP_DIR"

echo "== Restoring preserved state into the new layout =="
[ -f "$KEEP/admin.db" ]   && cp "$KEEP/admin.db"   admin_dashboard/admin.db   && echo "  restored admin.db (API keys + fleet)"
[ -f "$KEEP/secret.key" ] && cp "$KEEP/secret.key" admin_dashboard/secret.key && echo "  restored secret.key"
[ -f "$KEEP/.env" ]       && cp "$KEEP/.env"       csp_dashboard/.env         && echo "  restored .env -> csp_dashboard/.env"

echo "== Python venv + deps (flask + python-dotenv) =="
python3 -m venv .venv_linux
.venv_linux/bin/pip install -q --upgrade pip
.venv_linux/bin/pip install -q flask python-dotenv

echo "== Start the portal =="
chmod +x admin_dashboard/deploy/*.sh
ADMIN_BIND_PORT="${ADMIN_BIND_PORT:-7000}" ./admin_dashboard/deploy/restart_admin.sh

echo
echo "== Done. Old dir preserved at ${APP_DIR}.bak-$TS (delete after you verify) =="
echo "   From now on, update with:  bash $APP_DIR/admin_dashboard/deploy/update_admin.sh"
