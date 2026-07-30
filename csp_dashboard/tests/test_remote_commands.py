"""Remote maintenance commands (core/commands.py).

These run on a CSP PC with nobody watching, triggered from Eko's portal, so the
things that MUST hold are: only the fixed allow-list ever executes, a batch
mid-send is never killed, and every outcome is reported back (and audited
locally) rather than lost.
"""
import pytest

from core import commands


def test_only_allowlisted_names_execute(monkeypatch):
    """The portal sends a NAME. Anything not built into this module is refused —
    this is the whole security boundary of the remote channel."""
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    for bad in ("rm -rf /", "exec", "update_now; restart_app", "", None,
                "SEND_REPORT",                     # matching is case-sensitive
                "send_report()", "send_report&selfheal"):
        result, detail = commands.execute(bad)
        assert result == "error", bad
        assert "unknown command" in detail, bad
    # and the list itself is exactly the documented five
    assert set(commands.ALLOWED) == {"update_now", "restart_app", "selfheal",
                                     "reset_whatsapp", "send_report"}


def test_surrounding_whitespace_is_trimmed_not_a_new_name(monkeypatch):
    """A stray space in a queued row shouldn't silently do nothing — it is
    trimmed and then still has to match the allow-list exactly."""
    ran = []
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setitem(commands._HANDLERS, "send_report",
                        lambda p: (ran.append(1), ("ok", "sent"))[1])
    assert commands.execute(" send_report ")[0] == "ok"
    assert ran == [1]


def test_a_handler_that_raises_is_reported_not_crashed(monkeypatch):
    """A broken repair must not take the dashboard down with it."""
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setitem(commands._HANDLERS, "selfheal",
                        lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    result, detail = commands.execute("selfheal")
    assert result == "error"
    assert "boom" in detail


def test_restart_is_deferred_while_a_batch_is_sending(monkeypatch):
    """Restarting mid-dispatch would abandon a customer list halfway. The command
    must report 'deferred' (which the portal re-queues) and NOT relaunch."""
    spawned = []
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(commands, "_busy_sending", lambda: True)
    monkeypatch.setattr(commands, "_spawn_relauncher", lambda p: spawned.append(p))
    result, detail = commands.execute("restart_app")
    assert result == "deferred"
    assert "batch is sending" in detail
    assert spawned == [], "must not relaunch while sending"


def test_restart_schedules_a_relaunch_when_idle(monkeypatch, tmp_path):
    spawned = []
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(commands, "_busy_sending", lambda: False)
    monkeypatch.setattr(commands, "_spawn_relauncher", lambda p: spawned.append(p))
    monkeypatch.setattr(commands, "APP_ROOT", str(tmp_path))
    (tmp_path / "CSP_Platform.vbs").write_text("' launcher", encoding="utf-8")
    result, detail = commands.execute("restart_app")
    assert result.startswith("restarting")
    assert len(spawned) == 1, "the relaunch must be scheduled before we exit"


def test_restart_refuses_without_a_launcher(monkeypatch, tmp_path):
    """Exiting without a way back up would leave the CSP with a dead app."""
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(commands, "_busy_sending", lambda: False)
    monkeypatch.setattr(commands, "APP_ROOT", str(tmp_path))     # no .vbs inside
    result, detail = commands.execute("restart_app")
    assert result == "error"
    assert "not found" in detail


def test_run_queued_acks_everything_and_restarts_last(monkeypatch):
    """A restart ends the process, so it has to run AFTER the other commands and
    its own ack has to be sent before the exit."""
    order, acks = [], []
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(commands, "_exit_now", lambda: order.append("exit"))
    monkeypatch.setitem(commands._HANDLERS, "send_report",
                        lambda p: (order.append("report"), ("ok", "sent"))[1])
    monkeypatch.setitem(commands._HANDLERS, "selfheal",
                        lambda p: (order.append("heal"), ("ok", "healthy"))[1])
    monkeypatch.setitem(commands._HANDLERS, "restart_app",
                        lambda p: (order.append("restart"), ("restarting:restart", "ok"))[1])

    commands.run_queued(
        [{"id": 1, "command": "restart_app"},
         {"id": 2, "command": "send_report"},
         {"id": 3, "command": "selfheal"}],
        ack=lambda i, r, d: acks.append((i, r)))

    assert order == ["report", "heal", "restart", "exit"]
    assert [i for i, _ in acks] == [2, 3, 1]
    # the internal 'restarting:*' signal is reported to the portal as plain success
    assert dict(acks)[1] == "ok"


def test_deferred_restart_does_not_exit(monkeypatch):
    order, acks = [], []
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(commands, "_exit_now", lambda: order.append("exit"))
    monkeypatch.setattr(commands, "_busy_sending", lambda: True)
    commands.run_queued([{"id": 7, "command": "restart_app"}],
                        ack=lambda i, r, d: acks.append((i, r)))
    assert order == [], "a deferred restart must leave the app running"
    assert acks == [(7, "deferred")]


def test_update_now_skips_when_nothing_published(monkeypatch):
    from core import admin_reporter
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(admin_reporter, "fetch_sync",
                        lambda: {"ok": True, "latest_version": None, "update_url": None})
    result, detail = commands.execute("update_now")
    assert result == "skipped"
    assert "no update" in detail


def test_update_now_skips_when_already_on_that_version(monkeypatch):
    import config
    from core import admin_reporter, updater
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(config, "APP_VERSION", "1.2.3", raising=False)
    monkeypatch.setattr(updater, "pending_version", lambda: None)
    monkeypatch.setattr(admin_reporter, "fetch_sync",
                        lambda: {"ok": True, "latest_version": "1.2.3",
                                 "update_url": "http://x/y.zip"})
    result, detail = commands.execute("update_now")
    assert result == "skipped"
    assert "1.2.3" in detail


def test_update_now_stages_an_older_version_too(monkeypatch, tmp_path):
    """Publishing IS the decision, so update_now must also move an install
    BACKWARDS — that is how a rollback reaches a CSP that already updated."""
    import config
    from core import admin_reporter, updater
    staged = {}
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(commands, "_busy_sending", lambda: False)
    monkeypatch.setattr(commands, "_spawn_relauncher", lambda p: None)
    monkeypatch.setattr(commands, "APP_ROOT", str(tmp_path))
    (tmp_path / "CSP_Platform.vbs").write_text("' launcher", encoding="utf-8")
    monkeypatch.setattr(config, "APP_VERSION", "1.5.0", raising=False)
    monkeypatch.setattr(updater, "pending_version", lambda: None)
    monkeypatch.setattr(updater, "stage_update",
                        lambda v, u, s: staged.update(version=v, url=u, sha=s) or {"ok": True})
    monkeypatch.setattr(admin_reporter, "fetch_sync",
                        lambda: {"ok": True, "latest_version": "1.4.0",
                                 "update_url": "http://x/old.zip",
                                 "update_sha256": "abc"})
    result, detail = commands.execute("update_now")
    assert result.startswith("restarting")
    assert staged["version"] == "1.4.0"
    assert staged["sha"] == "abc", "the package hash must still be verified"


def test_update_now_reports_a_staging_failure(monkeypatch):
    import config
    from core import admin_reporter, updater
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(config, "APP_VERSION", "1.0.0", raising=False)
    monkeypatch.setattr(updater, "pending_version", lambda: None)
    monkeypatch.setattr(updater, "stage_update",
                        lambda v, u, s: {"ok": False, "error": "sha256 mismatch"})
    monkeypatch.setattr(admin_reporter, "fetch_sync",
                        lambda: {"ok": True, "latest_version": "1.0.1",
                                 "update_url": "http://x/y.zip"})
    result, detail = commands.execute("update_now")
    assert result == "error"
    assert "sha256 mismatch" in detail


def test_reset_whatsapp_leaves_a_healthy_session_alone(monkeypatch):
    """Clearing a live session costs the CSP a QR scan for nothing."""
    from core import selfheal
    monkeypatch.setattr(commands, "_audit", lambda *a: None)
    monkeypatch.setattr(selfheal, "_bridge_connected", lambda: True)
    result, detail = commands.execute("reset_whatsapp")
    assert result == "skipped"
    assert "left alone" in detail


def test_every_executed_command_is_audited_locally(db, monkeypatch):
    """The CSP must be able to see what Eko did on their own machine."""
    from database import queries
    monkeypatch.setitem(commands._HANDLERS, "send_report", lambda p: ("ok", "sent"))
    commands.execute("send_report")               # the db fixture seeds the operator
    actions = [r["action"] for r in queries.list_audit_logs(10)]
    assert "remote_send_report" in actions
