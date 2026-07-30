"""
Remote maintenance commands — the CSP side.

WHY THIS EXISTS
    Eko cannot reach into a CSP PC (it is behind NAT, no inbound access), and a
    CSP is not a computer operator — asking them to close windows, run a .bat or
    scan something is how an install stays broken for days. So the app polls the
    admin portal, and if Eko has queued one of a FIXED set of maintenance
    actions, the app performs it ITSELF and reports what happened.

    Net effect: Eko publishes an update or a repair from the portal, and the CSP
    does nothing and notices nothing except that it works.

THE SECURITY BOUNDARY — read before adding anything here
    The portal sends a NAME, never code. This module holds the only list of names
    that mean anything (_HANDLERS below); anything else is refused and reported
    as such. There is deliberately:
      • no eval/exec, no shell string from the server, no "run this command",
      • no file path, URL or SQL taken from the server payload,
      • no handler that reads, exports, or transmits customer data — the two-field
        northbound boundary (aggregate counts only) is untouched by all of this.
    A compromised portal can therefore restart this app, re-run its own repair
    logic, or make it fetch the update package it already advertises via /sync —
    and nothing else.

    Every executed command is written to the CSP's OWN audit trail (action
    `remote_<name>`), so the CSP can always see what Eko did on their machine.

WHAT EACH COMMAND DOES
    update_now      Stage the version the portal has published (bypassing the
                    "is it newer" check, so a deliberate rollback works too),
                    then restart so it is applied. No-op if nothing is published.
    restart_app     Restart the dashboard (and the bridge, which the launcher
                    leaves to the CSP). This is what turns "staged" into
                    "applied", because Windows will not let a running process
                    overwrite its own files.
    selfheal        Run core.selfheal's full diagnose-and-repair pass.
    reset_whatsapp  Clear a dead WhatsApp session so a fresh QR is produced.
                    Skipped when the link is currently healthy — it would log the
                    CSP out for no reason and cost them a QR scan.
    send_report     Push an immediate heartbeat so the portal is up to date.

MID-SEND SAFETY
    Anything that restarts checks core.comm_runner first. If a batch is being
    sent, the command is DEFERRED (reported as such and retried on a later poll)
    rather than killing a dispatch loop halfway through a customer list.
"""
import os
import subprocess
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _audit(name: str, detail: str):
    try:
        from database import queries
        queries.insert_system_audit(f"remote_{name}", detail)
    except Exception:
        pass


def _busy_sending():
    """A batch mid-dispatch — restarting now would abandon it."""
    try:
        from core import comm_runner
        return comm_runner.is_running()
    except Exception:
        return False


# ── individual commands ────────────────────────────────────────────────────────

def _cmd_send_report(payload=None):
    from core import admin_reporter
    res = admin_reporter.report_once()
    return ("ok", "report sent") if res.get("ok") else \
           ("error", f"report failed: {res.get('error') or res.get('status')}")


def _summarise(rep) -> str:
    """First line of a selfheal Report — the verdict, without the step detail."""
    return rep.render().splitlines()[0][:300]


def _cmd_selfheal(payload=None):
    from core import selfheal
    rep = selfheal.run(verbose=False)
    return ("error" if rep.problems else "ok"), _summarise(rep)


def _cmd_reset_whatsapp(payload=None):
    """Only when the link is already dead — a healthy session must never be
    thrown away remotely, because re-linking needs the CSP to scan a QR."""
    from core import selfheal
    if selfheal._bridge_connected():
        return "skipped", "WhatsApp is connected — session left alone"
    rep = selfheal.Report()
    selfheal.fix_dead_session(rep)
    return ("error" if rep.problems else "ok"), _summarise(rep)


def _cmd_update_now(payload=None):
    """Fetch + verify whatever the portal currently publishes, then restart to
    apply it. Deliberately does NOT require the published version to be newer:
    the admin publishing it IS the decision, which is also how a rollback
    reaches an install that already moved ahead."""
    import config
    from core import admin_reporter, updater
    info = admin_reporter.fetch_sync()
    if not info.get("ok"):
        return "error", f"could not reach the portal: {info.get('error')}"
    version = info.get("latest_version")
    url = info.get("update_url")
    if not version or not url:
        return "skipped", "no update is published"
    local = getattr(config, "APP_VERSION", "0")
    if updater.pending_version() == version:
        return _restart("apply", f"{version} already staged")
    if version == local:
        return "skipped", f"already running {version}"
    res = updater.stage_update(version, url, info.get("update_sha256"))
    if not res.get("ok"):
        return "error", f"staging {version} failed: {res.get('error')}"
    return _restart("apply", f"staged {version}")


def _cmd_restart_app(payload=None):
    return _restart("restart", "restart requested")


# ── restart ───────────────────────────────────────────────────────────────────

def _restart(kind: str, why: str):
    """Relaunch the app and exit this process.

    The launcher (CSP_Platform.vbs) applies any staged update BEFORE starting the
    app, so "restart" and "apply the update" are the same operation. The relaunch
    is done by a detached helper that waits for THIS process to exit first —
    otherwise the new instance would fight the old one for port 5000 and, on
    Windows, for the very files an update is trying to overwrite.
    """
    if _busy_sending():
        return "deferred", f"{why}, but a batch is sending — will retry after it finishes"
    launcher = os.path.join(APP_ROOT, "CSP_Platform.vbs")
    if not os.path.isfile(launcher):
        return "error", "CSP_Platform.vbs not found — cannot relaunch safely"
    try:
        _spawn_relauncher(launcher)
    except Exception as e:
        return "error", f"could not schedule the relaunch: {e}"
    # Ack BEFORE exiting, or the portal would never learn this ran.
    return f"restarting:{kind}", why


def _spawn_relauncher(launcher: str):
    """Start a detached, windowless waiter: it polls until this PID is gone, then
    runs the launcher. Uses PowerShell because it ships with Windows — no extra
    dependency on the 4 GB deploy box."""
    ps = (f"$p={os.getpid()}; "
          "for($i=0;$i -lt 120;$i++){ "
          "  if(-not (Get-Process -Id $p -ErrorAction SilentlyContinue)){ break }; "
          "  Start-Sleep -Milliseconds 500 }; "
          "Start-Sleep -Seconds 1; "
          f"Start-Process -FilePath 'wscript.exe' -ArgumentList '\"{launcher}\"' "
          f"-WorkingDirectory '{APP_ROOT}'")
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                      "-Command", ps],
                     cwd=APP_ROOT, creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL)


def _exit_now():
    """Leave the process. os._exit skips atexit/threads on purpose: a hung
    daemon thread must not keep the old instance holding port 5000 (and, during
    an update, holding its own files open)."""
    sys.stdout.flush()
    os._exit(0)


# The ONLY commands that exist. A name not in here is refused.
_HANDLERS = {
    "update_now": _cmd_update_now,
    "restart_app": _cmd_restart_app,
    "selfheal": _cmd_selfheal,
    "reset_whatsapp": _cmd_reset_whatsapp,
    "send_report": _cmd_send_report,
}

ALLOWED = tuple(_HANDLERS)


def execute(name: str, payload=None) -> tuple:
    """Run one allow-listed command. Returns (result, detail) where result is
    ok | error | skipped | deferred | restarting:<kind>. Never raises — a bad
    command must not take the dashboard down with it."""
    handler = _HANDLERS.get((name or "").strip())
    if handler is None:
        return "error", f"unknown command '{name}' — refused"
    try:
        result, detail = handler(payload)
    except Exception as e:
        result, detail = "error", f"{type(e).__name__}: {e}"[:300]
    _audit(name, f"{result}: {detail}")
    print(f"[remote] {name} -> {result}: {detail}")
    return result, detail


def _ack_result(result: str) -> str:
    """`restarting:*` is this module's internal signal to leave the process; to
    the portal it is simply a command that succeeded."""
    return "ok" if str(result).startswith("restarting") else str(result)


def run_queued(commands: list, ack=None) -> list:
    """Execute the commands handed over by one /sync poll, acking each outcome.

    A command that restarts the app is always executed LAST and only after every
    other one has been acked, so nothing is lost to the process exiting: the ack
    for the restart itself is sent first, then the process leaves and the relauncher
    (already scheduled) brings the app back up with the update applied.
    """
    results = []
    restart_later = None
    for cmd in commands or []:
        name = (cmd or {}).get("command")
        if name in ("update_now", "restart_app") and restart_later is None:
            restart_later = cmd          # defer to the end of this batch
            continue
        result, detail = execute(name, (cmd or {}).get("payload"))
        results.append({"id": (cmd or {}).get("id"), "result": result, "detail": detail})
        if ack:
            ack(cmd.get("id"), _ack_result(result), detail)

    if restart_later is not None:
        name = restart_later.get("command")
        result, detail = execute(name, restart_later.get("payload"))
        results.append({"id": restart_later.get("id"), "result": result, "detail": detail})
        if ack:
            ack(restart_later.get("id"), _ack_result(result), detail)
        if str(result).startswith("restarting"):
            _exit_now()
    return results
