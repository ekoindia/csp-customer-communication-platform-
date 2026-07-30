r"""
Self-heal engine for the WhatsApp bridge (and its webhook link).

Every failure encoded here is one that ACTUALLY happened on a live CSP machine and
cost hours to find by hand. The point of this module is that the machine finds and
fixes them itself, and when it genuinely cannot, it says exactly what is wrong in
one line instead of leaving the dashboard on "server not running".

What it checks / repairs:

  1. SHADOW FILES  — stray files named `node` / `Node.js` inside whatsapp\ make cmd
     run them instead of the real Node (current directory comes first on PATH), so
     `node wa_server.js` exits silently with no output at all. Deleted.
  2. DEPENDENCIES  — node_modules missing, or Baileys installed at a version this
     bridge cannot use: 7.x / <6.7.22 (the last CommonJS build, 6.7.18, carries a
     published message-spoofing advisory). Reinstalled to the pinned range.
  3. PORT 3000     — a stale bridge process still holding the port while not
     answering /status, so the new one can never bind. Killed.
  4. DEAD SESSION  — a half-written .wa_session that leaves Baileys stuck at
     "attempting registration" and never emits a QR. Cleared (only when the bridge
     is NOT connected — a working session is never touched).
  5. WEBHOOK LINK  — Flask requires the X-Webhook-Token header when
     config.WEBHOOK_TOKEN is set, and the bridge only sends it when it has the env
     var. A mismatch silently rejects every delivery ACK, so cases sit at "sent"
     while actually being delivered. Reported (and the launcher now passes it).

Run standalone:  python -m core.selfheal
Returns 0 when everything is healthy or was repaired, 1 when something needs a
human. Never raises — a self-heal pass must not be able to break an install.
"""
import os
import re
import shutil
import subprocess
import sys

WHATSAPP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "whatsapp"))
SESSION_DIR = os.path.join(WHATSAPP_DIR, ".wa_session")
LOG_PATH = os.path.join(WHATSAPP_DIR, "wa_server.log")
BRIDGE_PORT = 3000

# Baileys must be >= 6.7.22 (patched) and < 7 (7.x is ESM-only + release-candidate
# + pulls a native rust bridge). See whatsapp/package.json for the full reasoning.
MIN_BAILEYS = (6, 7, 22)


# ── pure helpers (unit-tested; no side effects) ───────────────────────────────

def parse_version(text: str):
    """'6.7.24' -> (6, 7, 24). None when unparseable."""
    m = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return tuple(int(g) for g in m.groups()) if m else None


def baileys_version_ok(version: str) -> bool:
    """True only for a version this bridge can actually load AND is patched."""
    v = parse_version(version)
    if not v:
        return False
    return MIN_BAILEYS <= v < (7, 0, 0)


def classify_bridge_log(text: str):
    """Turn the bridge's log tail into ONE plain-English reason, or None.

    Ordered most-specific first: the interesting line is usually not the last one
    (Baileys prints a lot after a failure)."""
    t = str(text or "")
    if "ERR_REQUIRE_ESM" in t:
        return ("the WhatsApp library version doesn't match this bridge "
                "(ERR_REQUIRE_ESM) — dependencies need reinstalling")
    if "EADDRINUSE" in t:
        return f"port {BRIDGE_PORT} is already in use by another process"
    if "Cannot find module" in t:
        m = re.search(r"Cannot find module '([^']+)'", t)
        return f"a dependency is missing ({m.group(1)})" if m else "a dependency is missing"
    if "spawn git ENOENT" in t or "syscall spawn git" in t:
        return "npm needs git to install the WhatsApp library"
    if "temporary block" in t or t.count("Connection Failure") >= 3:
        return ("WhatsApp kept refusing the connection — usually a temporary block "
                "from too many attempts; wait ~30 minutes, then Reset & New QR")
    if "WhatsApp connected" in t:
        return None
    return None


def webhook_token_mismatch(flask_token: str, bridge_env_token: str) -> bool:
    """Flask enforces the header only when IT has a token; the bridge sends it only
    when it has one. Mismatch => every delivery ACK is silently rejected."""
    return bool(flask_token) and (flask_token != (bridge_env_token or ""))


# ── side-effecting checks ────────────────────────────────────────────────────

def last_bridge_error(lines: int = 60):
    """Plain-English reason from the tail of the bridge log, or None."""
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            tail = "".join(f.readlines()[-lines:])
    except OSError:
        return None
    return classify_bridge_log(tail)


def _bridge_answers() -> bool:
    try:
        import requests
        r = requests.get(f"http://127.0.0.1:{BRIDGE_PORT}/status", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _bridge_connected() -> bool:
    try:
        import requests
        r = requests.get(f"http://127.0.0.1:{BRIDGE_PORT}/status", timeout=3)
        return bool(r.json().get("ready"))
    except Exception:
        return False


def _port_busy() -> bool:
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", BRIDGE_PORT))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def fix_shadow_node_files(report):
    """Remove stray `node` / `Node.js` entries in whatsapp\\ — cmd runs those
    instead of the real Node, and the bridge then exits with no output at all."""
    removed = []
    for name in ("node", "node.exe.lnk", "Node.js", "node.js"):
        p = os.path.join(WHATSAPP_DIR, name)
        if not os.path.exists(p):
            continue
        # never touch the real thing if node.exe somehow lives here
        if name.lower() == "node.exe":
            continue
        try:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            removed.append(name)
        except OSError:
            pass
    if removed:
        report.fixed(f"removed stray file(s) shadowing Node: {', '.join(removed)}")
    return bool(removed)


def installed_baileys_version():
    """Version string from node_modules, or None when not installed."""
    pj = os.path.join(WHATSAPP_DIR, "node_modules", "@whiskeysockets",
                      "baileys", "package.json")
    try:
        import json
        with open(pj, encoding="utf-8") as f:
            return json.load(f).get("version")
    except Exception:
        return None


def fix_dependencies(report):
    """Reinstall the bridge's dependencies when they are missing or at an
    unusable/vulnerable Baileys version."""
    version = installed_baileys_version()
    if version and baileys_version_ok(version):
        report.ok(f"WhatsApp library {version}")
        return False
    why = "not installed" if not version else f"version {version} is not usable/patched"
    report.note(f"WhatsApp dependencies {why} — reinstalling")
    npm = shutil.which("npm") or ("npm.cmd" if os.name == "nt" else "npm")
    env = os.environ.copy()
    # Baileys pulls one dependency over git+https; a CSP box may only have the
    # portable git the installer placed inside the app folder.
    mingit = os.path.abspath(os.path.join(WHATSAPP_DIR, "..", "tools", "mingit", "cmd"))
    if os.path.isdir(mingit):
        env["PATH"] = mingit + os.pathsep + env.get("PATH", "")
    try:
        for extra in ([], ["--no-package-lock"]):
            subprocess.run([npm, "install", "--no-audit", "--no-fund"] + extra,
                           cwd=WHATSAPP_DIR, env=env, timeout=900,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if baileys_version_ok(installed_baileys_version() or ""):
                report.fixed(f"reinstalled WhatsApp library "
                             f"({installed_baileys_version()})")
                return True
    except Exception as e:
        report.fail(f"could not install the WhatsApp library: {e}")
        return False
    report.fail("WhatsApp library still not usable after reinstall "
                "(check the internet connection and that git is available)")
    return False


def fix_stuck_port(report):
    """Free port 3000 when something holds it but does NOT answer /status."""
    if not _port_busy():
        return False
    if _bridge_answers():
        report.ok("bridge is listening on port 3000")
        return False
    report.note(f"port {BRIDGE_PORT} is held by a process that isn't responding "
                f"— stopping stale Node processes")
    for image in ("node.exe", "node"):
        try:
            subprocess.run(["taskkill", "/F", "/IM", image] if os.name == "nt"
                           else ["pkill", "-f", image],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30)
        except Exception:
            pass
    if _port_busy():
        report.fail(f"port {BRIDGE_PORT} is still busy — another application is "
                    f"using it; close it and try again")
        return False
    report.fixed(f"freed port {BRIDGE_PORT}")
    return True


def fix_dead_session(report):
    """Clear a half-written session that keeps Baileys stuck before the QR.

    Only when the bridge is NOT connected — a working session is never touched, so
    a linked CSP is never forced to re-scan.
    """
    if not os.path.isdir(SESSION_DIR):
        return False
    if _bridge_connected():
        report.ok("WhatsApp session is live")
        return False
    reason = last_bridge_error() or ""
    creds = os.path.join(SESSION_DIR, "creds.json")
    stuck = ("refusing" in reason or "temporary block" in reason
             or not os.path.exists(creds))
    if not stuck:
        return False
    try:
        shutil.rmtree(SESSION_DIR)
    except OSError as e:
        report.fail(f"could not clear the stale WhatsApp session: {e}")
        return False
    report.fixed("cleared a stale WhatsApp session so a fresh QR can be generated")
    return True


def check_webhook_link(report):
    """Delivery statuses depend on the bridge being able to POST ACKs to Flask."""
    try:
        import config
        flask_token = getattr(config, "WEBHOOK_TOKEN", "") or ""
    except Exception:
        flask_token = ""
    if not flask_token:
        report.ok("delivery-status webhook needs no token (localhost only)")
        return False
    if webhook_token_mismatch(flask_token, os.environ.get("WEBHOOK_TOKEN", "")):
        report.note("delivery-status webhook token is set in the app but not in "
                    "this shell — the dashboard passes it when it starts the "
                    "bridge, so start WhatsApp from Settings (not by hand)")
        return False
    report.ok("delivery-status webhook token matches")
    return False


class Report:
    """Collects a short, readable summary of the pass."""

    def __init__(self):
        self.lines = []
        self.problems = 0
        self.repairs = 0

    def ok(self, msg):
        self.lines.append(f"  [ok]    {msg}")

    def note(self, msg):
        self.lines.append(f"  [..]    {msg}")

    def fixed(self, msg):
        self.repairs += 1
        self.lines.append(f"  [FIXED] {msg}")

    def fail(self, msg):
        self.problems += 1
        self.lines.append(f"  [!]     {msg}")

    def render(self):
        head = "WhatsApp self-check"
        if self.problems:
            tail = (f"{self.problems} problem(s) need attention"
                    + (f", {self.repairs} repaired" if self.repairs else ""))
        elif self.repairs:
            tail = f"{self.repairs} problem(s) found and REPAIRED"
        else:
            tail = "everything healthy"
        return "\n".join([f"{head}: {tail}"] + self.lines)


def run(verbose: bool = True) -> Report:
    """One self-heal pass. Never raises."""
    report = Report()
    for step in (fix_shadow_node_files, fix_dependencies, fix_stuck_port,
                 fix_dead_session, check_webhook_link):
        try:
            step(report)
        except Exception as e:              # a check must never break the install
            report.fail(f"{step.__name__} could not run: {e}")
    if verbose:
        print(report.render())
    return report


if __name__ == "__main__":
    sys.exit(1 if run().problems else 0)
