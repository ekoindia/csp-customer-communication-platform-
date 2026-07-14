import os
from datetime import timedelta, timezone, datetime

import config
# Cap OMP/BLAS threads for the deploy i3 BEFORE numpy/cv2/tesseract get imported.
from core import hardware
hardware.apply_runtime_caps()
from flask import Flask
from database.db import setup
from core.extraction import purge_stale_uploads
from dashboard.routes import dashboard_bp
from dashboard.webhook_routes import webhook_bp

# Timestamps are stored in UTC. The CSP is in India, so every time shown in the
# UI is converted to IST and split into a readable date and time (instructions
# #11 — date and time must be separate and in the CSP's own time zone).
_IST = timezone(timedelta(hours=5, minutes=30))


def _to_ist(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST)


def _load_secret_key() -> bytes:
    """Persist a random secret key locally so sessions survive restarts.
    Never committed — lives next to the database on the CSP PC."""
    key_path = os.path.join("database", "secret.key")
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read()
    key = os.urandom(32)
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(key)
    return key


app = Flask(__name__, template_folder="dashboard/templates", static_folder="dashboard/static")

# Initialise DB + reference data before serving.
setup()

# First-run credentials are NOT auto-generated anymore. On a fresh install the
# operator sets their OWN login ID + password + branch details on the onboarding
# screen (shown before login — see dashboard.onboarding). Until that is done, the
# onboarding gate redirects every route there. Dev/test seed a default operator
# via config.SEED_DEFAULT_USER, which also marks onboarding complete.

# DPDP self-heal: a crash mid-upload or a Windows file-lock can leave a raw
# customer document sitting in uploads/ even though it's meant to be deleted
# right after processing. Sweep it on every startup so nothing lingers.
_stale = purge_stale_uploads()
if _stale:
    print(f"Startup cleanup: removed {_stale} stale file(s) from uploads/.")

# DPDP reconcile: closing a case updates its status and purges its PII in two
# separate commits, so a crash between them could leave a 'case_closed' case
# still holding PII. Sweep any such straggler on startup so a closed case is
# never left un-purged.
try:
    from database.queries import purge_closed_unpurged_pii
    _repurged = purge_closed_unpurged_pii()
    if _repurged:
        print(f"Startup cleanup: purged PII of {_repurged} closed case(s) that were left un-purged.")
except Exception as _e:
    print(f"Closed-case PII reconcile skipped: {_e}")

# Hardware-aware startup: log the detected profile and the OCR engine picked for
# this machine (docTR on a capable box, Tesseract-only on a small 4 GB CSP PC).
try:
    from core import hardware
    print(hardware.summary_line())
    if hardware.profile()["low_ram"]:
        print("Low-RAM machine detected: scanned-document OCR runs in the "
              "light Tesseract-only mode (no PyTorch). Excel/CSV uploads from "
              "the bank are the preferred, most accurate input on this PC.")
except Exception as _e:
    print(f"Hardware profile check skipped: {_e}")

# Optional: push a PII-free heartbeat to the Eko admin portal. No-op unless
# config.ADMIN_REPORT_ENABLED is True, so default behaviour is unchanged.
try:
    from core import admin_reporter
    admin_reporter.start_background()
except Exception as _e:
    print(f"Admin reporting not started: {_e}")

app.secret_key = _load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,   # JS can't read the session cookie
    SESSION_COOKIE_SAMESITE="Lax",  # mitigate CSRF on top-level navigations
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),  # matches CSP 8AM-8PM hours
    TEMPLATES_AUTO_RELOAD=True,     # pick up template edits without a restart
    MAX_CONTENT_LENGTH=(
        config.MAX_UPLOAD_MB * config.MAX_BATCH_FILES * 1024 * 1024
    ),
)

@app.template_filter("ist_date")
def ist_date(value):
    dt = _to_ist(value)
    return dt.strftime("%d %b %Y") if dt else "—"


@app.template_filter("ist_time")
def ist_time(value):
    dt = _to_ist(value)
    return dt.strftime("%I:%M %p") + " IST" if dt else ""


app.register_blueprint(dashboard_bp)
app.register_blueprint(webhook_bp)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
