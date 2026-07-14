"""
Eko Admin Portal — Flask entry (runs on Eko's server, NOT the CSP PC).

    python admin_dashboard/app.py  ->  http://127.0.0.1:7000   (login admin/admin123)

It receives PII-free heartbeats from CSP installs and shows fleet status,
campaign progress, earnings, and WhatsApp health. It never receives or stores
any customer data.
"""
import os
import sys

# This portal lives in code/admin_dashboard/ but deliberately reuses the CSP
# app's `config` and `core.auth` (single source of truth for settings and
# password hashing), which live in the sibling code/csp_dashboard/ folder.
# Put BOTH on the import path:
#   - the repo root (code/)         -> so `admin_dashboard.*` resolves
#   - code/csp_dashboard            -> so `config` and `core.*` resolve
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "csp_dashboard"))

from flask import Flask  # noqa: E402
from admin_dashboard.db import setup  # noqa: E402
from admin_dashboard.api import api_bp  # noqa: E402
from admin_dashboard.routes import ui_bp  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(_DIR, "templates"))
setup()


def _secret():
    p = os.path.join(_DIR, "secret.key")
    if os.path.exists(p):
        return open(p, "rb").read()
    k = os.urandom(32)
    open(p, "wb").write(k)
    return k


app.secret_key = _secret()
app.register_blueprint(api_bp)
app.register_blueprint(ui_bp)


class _ProxyPrefixMiddleware:
    """Lets this app be reverse-proxied under a path prefix (e.g. nginx routes
    /csp-admin/ here, alongside OTHER unrelated dashboards on the same shared
    port — see ADMIN_PORTAL_ARCHITECTURE.md). nginx forwards the FULL original
    path + an X-Forwarded-Prefix header (no path stripping on nginx's side);
    this middleware moves that prefix into SCRIPT_NAME so Flask's routing and
    url_for(...) both account for it correctly. A no-op when the header is
    absent (plain local/direct access keeps working unchanged)."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "")
        if prefix and environ.get("PATH_INFO", "").startswith(prefix):
            environ["PATH_INFO"] = environ["PATH_INFO"][len(prefix):] or "/"
            environ["SCRIPT_NAME"] = prefix
        return self.wsgi_app(environ, start_response)


app.wsgi_app = _ProxyPrefixMiddleware(app.wsgi_app)

if __name__ == "__main__":
    # Binding is env-driven so the SAME code runs on a local demo (127.0.0.1)
    # and on Eko's real server (ADMIN_BIND_HOST=0.0.0.0 behind HTTPS). See
    # config.ADMIN_BIND_HOST / ADMIN_BIND_PORT and ADMIN_PORTAL_ARCHITECTURE.md.
    import config
    app.run(host=config.ADMIN_BIND_HOST, port=config.ADMIN_BIND_PORT, debug=False)
