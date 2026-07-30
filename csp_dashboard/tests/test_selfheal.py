"""Self-heal engine: the decision logic behind each automatic repair.

Every case here is a failure that actually happened on a live CSP machine."""

import os

import pytest

from core import selfheal


# ── Baileys version gate ─────────────────────────────────────────────────────

@pytest.mark.parametrize("version,ok", [
    ("6.7.24", True),    # patched, CommonJS-loadable via dynamic import
    ("6.7.22", True),    # first patched release
    ("6.7.18", False),   # last CJS build, but carries the spoofing advisory
    ("6.7.9",  False),   # older + vulnerable
    ("7.0.0",  False),   # ESM-only + RC + native bridge
    ("",       False),
    ("garbage", False),
])
def test_baileys_version_gate(version, ok):
    assert selfheal.baileys_version_ok(version) is ok


# ── log -> one plain-English reason ──────────────────────────────────────────

def test_esm_mismatch_is_named():
    r = selfheal.classify_bridge_log("Error [ERR_REQUIRE_ESM]: require() of ES Module")
    assert r and "reinstall" in r.lower()


def test_port_conflict_is_named():
    r = selfheal.classify_bridge_log("Error: listen EADDRINUSE: address already in use")
    assert r and "3000" in r


def test_missing_dependency_names_the_module():
    r = selfheal.classify_bridge_log("Error: Cannot find module 'qrcode'")
    assert r and "qrcode" in r


def test_missing_git_is_named():
    r = selfheal.classify_bridge_log("npm error syscall spawn git")
    assert r and "git" in r.lower()


def test_repeated_connection_failure_reads_as_a_block():
    log = "\n".join(["Error: Connection Failure"] * 4)
    r = selfheal.classify_bridge_log(log)
    assert r and "30 minutes" in r


def test_healthy_log_reports_no_problem():
    assert selfheal.classify_bridge_log("WhatsApp connected.") is None
    assert selfheal.classify_bridge_log("") is None


# ── webhook token parity ─────────────────────────────────────────────────────

def test_webhook_mismatch_detected():
    # Flask requires a token, the bridge has none -> every ACK is rejected
    assert selfheal.webhook_token_mismatch("secret", "") is True
    assert selfheal.webhook_token_mismatch("secret", "other") is True


def test_webhook_match_or_unused_is_fine():
    assert selfheal.webhook_token_mismatch("secret", "secret") is False
    assert selfheal.webhook_token_mismatch("", "") is False
    assert selfheal.webhook_token_mismatch("", "anything") is False


# ── shadow-file repair (the "node exits silently" bug) ───────────────────────

def test_shadow_node_files_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(selfheal, "WHATSAPP_DIR", str(tmp_path))
    (tmp_path / "node").write_text("junk pasted into cmd")
    (tmp_path / "Node.js").write_text("junk")
    (tmp_path / "wa_server.js").write_text("// the real thing")

    report = selfheal.Report()
    assert selfheal.fix_shadow_node_files(report) is True
    assert not (tmp_path / "node").exists()
    assert not (tmp_path / "Node.js").exists()
    assert (tmp_path / "wa_server.js").exists()      # untouched
    assert report.repairs == 1


def test_no_shadow_files_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(selfheal, "WHATSAPP_DIR", str(tmp_path))
    report = selfheal.Report()
    assert selfheal.fix_shadow_node_files(report) is False
    assert report.repairs == 0


# ── a pass must never raise, whatever the machine looks like ─────────────────

def test_run_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(selfheal, "WHATSAPP_DIR", str(tmp_path))
    monkeypatch.setattr(selfheal, "SESSION_DIR", str(tmp_path / ".wa_session"))
    monkeypatch.setattr(selfheal, "LOG_PATH", str(tmp_path / "wa_server.log"))
    monkeypatch.setattr(selfheal, "fix_dependencies",
                        lambda r: r.note("skipped in test"))
    report = selfheal.run(verbose=False)
    assert isinstance(report.render(), str)
