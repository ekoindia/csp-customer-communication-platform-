"""
CSP-side self-updater.

Eko cannot push to a CSP's local PC (it's behind NAT), so updates are PULL-based:
the background sync loop learns a newer version is published (via /sync), then
this module DOWNLOADS + VERIFIES + STAGES the update package. The swap itself is
applied at the NEXT app start by the launcher (`--apply-if-pending`), when the
app's own files aren't loaded/locked yet — so there is no half-updated running
process and no Windows file-lock fight.

Data safety: applying an update copies only CODE into place and SKIPS the CSP's
data/config/session (see _PRESERVE) — the local SQLite DB, config.py (CSP name /
keys), the WhatsApp session, uploads, and secrets are never touched by an update.

An update does NOT rebuild the environment from scratch: Python/Node/Tesseract
and the existing .venv / whatsapp/node_modules are reused. It only swaps the
(small, code-only) package, then runs an INCREMENTAL dependency sync
(refresh_dependencies) so a release that adds a new library still works — a
few-second no-op for the common code-only update.

Package format: a .zip of the app tree (optionally under a single top-level
folder). It should contain a VERSION file with the new version string.
"""
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from urllib.request import urlopen

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Update working files (download, staging, pending marker) live OUTSIDE the
# install folder so C:\CSP_Platform only ever contains the app itself — no
# update clutter. A stable per-user dir (survives reboots, so a staged-but-not-
# yet-applied update isn't lost); falls back to the OS temp dir.
_UPDATE_BASE = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
UPDATE_DIR = os.path.join(_UPDATE_BASE, "CSP_Platform", "update")
STAGING = os.path.join(UPDATE_DIR, "staged")
PENDING = os.path.join(UPDATE_DIR, "pending.json")
# Every apply first COPIES the files it is about to overwrite/remove into a
# timestamped folder here (the "recycle bin"), so a bad release can be rolled
# back instead of being lost. Only the newest _KEEP_BACKUPS are retained.
BACKUPS = os.path.join(UPDATE_DIR, "backups")
_KEEP_BACKUPS = 2

# Top-level paths an update must NEVER overwrite (the CSP's own data/config).
_PRESERVE = {
    "config.py",              # CSP name / phone / API keys / settings
    "database",               # local SQLite (all customer data)
    "uploads",                # transient upload scratch
    "update",                 # this updater's own working dir
    "secret.key",             # Flask session secret
    ".venv", ".git", "__pycache__", ".pytest_cache",
}
# Nested paths (under an otherwise-updatable folder) to also preserve.
# (The admin portal lives in the separate code/admin_dashboard/ tree and is
# never part of a CSP install, so it needs no preserve entry here.)
_PRESERVE_NESTED = {
    os.path.join("whatsapp", ".wa_session"),   # WhatsApp login (don't re-scan QR)
    os.path.join("whatsapp", "node_modules"),  # installed bridge deps
}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: str):
    """Fetch url -> dest. Supports http(s):// and file:// (file:// used in tests)."""
    with urlopen(url, timeout=60) as r, open(dest, "wb") as f:  # nosec - admin-set URL
        shutil.copyfileobj(r, f)


def _is_preserved(rel: str) -> bool:
    top = rel.replace("\\", "/").split("/", 1)[0]
    if top in _PRESERVE:
        return True
    rel_norm = rel.replace("/", os.sep)
    return any(rel_norm == p or rel_norm.startswith(p + os.sep) for p in _PRESERVE_NESTED)


def _zip_root(zpath: str) -> str:
    """If the zip wraps everything in a single top folder, return that folder so
    we extract its CONTENTS as the app root; else return ''."""
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if n and not n.startswith("__MACOSX")]
    tops = {n.replace("\\", "/").split("/", 1)[0] for n in names}
    return tops.pop() if len(tops) == 1 and any("/" in n for n in names) else ""


def stage_update(version: str, url: str, sha256: str = None) -> dict:
    """Download + verify + extract the update package into STAGING and record a
    pending marker. Does NOT modify the running app. Never raises."""
    try:
        os.makedirs(UPDATE_DIR, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False,
                                          dir=UPDATE_DIR).name
        _download(url, tmp)
        got = _sha256(tmp)
        if sha256 and got.lower() != sha256.strip().lower():
            os.remove(tmp)
            return {"ok": False, "error": f"sha256 mismatch (got {got[:12]}...)"}
        if not zipfile.is_zipfile(tmp):
            os.remove(tmp)
            return {"ok": False, "error": "downloaded file is not a valid zip"}

        if os.path.isdir(STAGING):
            shutil.rmtree(STAGING, ignore_errors=True)
        os.makedirs(STAGING, exist_ok=True)
        root = _zip_root(tmp)
        with zipfile.ZipFile(tmp) as z:
            if root:
                for m in z.namelist():
                    if m.startswith(root + "/") and not m.endswith("/"):
                        target = os.path.join(STAGING, m[len(root) + 1:].replace("/", os.sep))
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with z.open(m) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
            else:
                z.extractall(STAGING)
        os.remove(tmp)

        with open(PENDING, "w", encoding="utf-8") as f:
            json.dump({"version": version, "sha256": got, "source": url}, f)
        return {"ok": True, "version": version, "sha256": got}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def pending_version():
    """Return the staged-but-not-applied version, or None."""
    try:
        with open(PENDING, "r", encoding="utf-8") as f:
            return json.load(f).get("version")
    except Exception:
        return None


def _timestamp() -> str:
    import time
    return time.strftime("%Y%m%d_%H%M%S")


def _prune_old_backups():
    """Keep only the newest _KEEP_BACKUPS backup folders; delete older ones."""
    try:
        if not os.path.isdir(BACKUPS):
            return
        dirs = sorted(d for d in os.listdir(BACKUPS)
                      if os.path.isdir(os.path.join(BACKUPS, d)))
        for d in dirs[:-_KEEP_BACKUPS] if len(dirs) > _KEEP_BACKUPS else []:
            shutil.rmtree(os.path.join(BACKUPS, d), ignore_errors=True)
    except Exception:
        pass


def apply_pending() -> dict:
    """Copy staged CODE over the app, then clear the staging + pending marker.
    Safe to call at every startup — a no-op when nothing is staged. Run this
    BEFORE the app imports its own modules.

    Data safety (never touched): everything in _PRESERVE / _PRESERVE_NESTED —
    config.py, the SQLite DB, uploads, secrets, the WhatsApp session.

    Two safeguards beyond a plain copy:
      • BACKUP ("recycle bin"): every file about to be overwritten OR pruned is
        first copied into BACKUPS/<timestamp>/ so a bad release can be rolled
        back (see rollback_last). Only the newest _KEEP_BACKUPS are kept.
      • PRUNE orphans: a code file that the NEW release no longer ships is
        removed, so a renamed/deleted module can't linger and get imported.
        Pruning is confined to directories the package itself populates, and
        never touches preserved data/config/session paths or *.log files.
    """
    if not os.path.isfile(PENDING) or not os.path.isdir(STAGING):
        return {"ok": True, "applied": False}
    version = pending_version()
    backup_dir = os.path.join(BACKUPS, _timestamp())
    copied = pruned = 0

    def _backup(rel: str):
        cur = os.path.join(APP_ROOT, rel)
        if os.path.isfile(cur):
            b = os.path.join(backup_dir, rel)
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(cur, b)

    try:
        # 1) Index the new package: its files and the directories it manages.
        staged_rel = set()
        staged_dirs = {""}
        for dirpath, _dirs, files in os.walk(STAGING):
            rd = os.path.relpath(dirpath, STAGING)
            rd = "" if rd == "." else rd
            staged_dirs.add(rd)
            for name in files:
                staged_rel.add(os.path.relpath(os.path.join(dirpath, name), STAGING))

        # 2) Copy new code in, backing up each file we overwrite.
        for rel in sorted(staged_rel):
            if _is_preserved(rel):
                continue
            dst = os.path.join(APP_ROOT, rel)
            if os.path.isfile(dst):
                _backup(rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(STAGING, rel), dst)
            copied += 1

        # 3) Prune orphans: code files under a package-managed dir that the new
        #    release no longer contains. Skips preserved subtrees entirely (also
        #    keeps the walk off the huge .venv/.git) and leaves *.log alone.
        for dirpath, dirs, files in os.walk(APP_ROOT):
            rd = os.path.relpath(dirpath, APP_ROOT)
            rd = "" if rd == "." else rd
            dirs[:] = [d for d in dirs
                       if not _is_preserved(os.path.join(rd, d) if rd else d)]
            if rd not in staged_dirs:      # dir the package doesn't manage -> leave
                continue
            for name in files:
                rel = name if rd == "" else os.path.join(rd, name)
                if rel in staged_rel or _is_preserved(rel) or name.endswith(".log"):
                    continue
                _backup(rel)
                try:
                    os.remove(os.path.join(APP_ROOT, rel))
                    pruned += 1
                except Exception:
                    pass

        shutil.rmtree(STAGING, ignore_errors=True)
        os.remove(PENDING)
        _prune_old_backups()
        return {"ok": True, "applied": True, "version": version,
                "files": copied, "pruned": pruned, "backup": backup_dir}
    except Exception as e:
        return {"ok": False, "applied": False, "error": str(e)}


def list_backups() -> list:
    """Newest-first list of available rollback points (timestamp folder names)."""
    try:
        return sorted((d for d in os.listdir(BACKUPS)
                       if os.path.isdir(os.path.join(BACKUPS, d))), reverse=True)
    except Exception:
        return []


def rollback_last(which: str = None) -> dict:
    """Restore the code saved in a backup folder (default: the most recent) back
    over the app — the escape hatch when an update misbehaves. Only restores
    files that were backed up; never touches preserved data/config/session.
    Never raises."""
    try:
        backups = list_backups()
        if not backups:
            return {"ok": False, "error": "no backups to roll back to"}
        target = which or backups[0]
        root = os.path.join(BACKUPS, target)
        if not os.path.isdir(root):
            return {"ok": False, "error": f"backup not found: {target}"}
        restored = 0
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                src = os.path.join(dirpath, name)
                rel = os.path.relpath(src, root)
                if _is_preserved(rel):
                    continue
                dst = os.path.join(APP_ROOT, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                restored += 1
        return {"ok": True, "version": target, "files": restored}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def refresh_dependencies() -> dict:
    """After an update swaps in new CODE, make sure the installed Python and Node
    libraries still match the (possibly changed) requirements-lite.txt /
    package.json. Both installers are INCREMENTAL — a few-second no-op when
    nothing changed, and the correct action when a release adds a new library.
    Without this, a dependency-adding update would ship code that imports a
    package that was never installed. Best-effort: never blocks app startup, and
    it reuses the existing .venv / node_modules (it does NOT reinstall Python,
    Node, Tesseract, or rebuild the environment from scratch)."""
    import subprocess
    import sys
    out = {"pip": None, "npm": None}
    req = os.path.join(APP_ROOT, "requirements-lite.txt")
    if os.path.isfile(req):
        try:
            print("[updater] syncing Python dependencies after update...")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", req],
                               cwd=APP_ROOT, timeout=1800)
            out["pip"] = r.returncode
        except Exception as e:
            print(f"[updater] pip sync skipped: {e}")
    wa = os.path.join(APP_ROOT, "whatsapp")
    if os.path.isfile(os.path.join(wa, "package.json")):
        try:
            print("[updater] syncing WhatsApp bridge dependencies after update...")
            r = subprocess.run(["npm", "install"], cwd=wa, timeout=1800, shell=True)
            out["npm"] = r.returncode
        except Exception as e:
            print(f"[updater] npm sync skipped: {e}")
    return out


def _version_in_zip(zip_path: str):
    """Read the VERSION file bundled in a local update .zip (root or under a
    single wrapping folder). This is the version the package will report once
    applied, so it is authoritative. Returns None if absent."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            for n in z.namelist():
                if n.endswith("/"):
                    continue
                parts = n.replace("\\", "/").split("/")
                if parts[-1] == "VERSION" and len(parts) <= 2:
                    return z.read(n).decode("utf-8", "replace").strip() or None
    except Exception:
        return None
    return None


def apply_local_zip(zip_path: str) -> dict:
    """MANUAL update path: apply an update package that was handed to the CSP as
    a file (e.g. CSP_Update.zip dropped into the install folder), with no admin
    portal / internet involved. Stages the local zip, applies the code swap
    (preserving config/DB/session/keys — see _PRESERVE), and re-syncs
    dependencies. Returns the apply result. Never partially applies: staging
    verifies it's a valid zip first."""
    if not os.path.isfile(zip_path):
        return {"ok": False, "applied": False, "error": f"file not found: {zip_path}"}
    version = _version_in_zip(zip_path) or "manual"
    # stage_update fetches via urlopen, which supports file:// URLs.
    url = "file:///" + os.path.abspath(zip_path).replace("\\", "/")
    staged = stage_update(version, url, None)
    if not staged.get("ok"):
        return {"ok": False, "applied": False, "error": staged.get("error")}
    res = apply_pending()
    if res.get("applied"):
        refresh_dependencies()
    return res


def apply_from_github(url: str = None) -> dict:
    """EASIEST update path: pull the latest app straight from the public GitHub
    repo — no zip to build/host/send, Eko just `git push`. The repo zip wraps
    everything in <repo>-<branch>/ with the CSP app under csp_dashboard/, so we
    stage THAT subfolder's contents as the app root, then apply (code-only;
    config/DB/keys/WhatsApp session preserved via _PRESERVE) + refresh deps.
    Never raises / never partially applies (staging validates the zip first)."""
    if not url:
        try:
            import config
            url = config.GITHUB_APP_ZIP_URL
        except Exception:
            return {"ok": False, "applied": False, "error": "no GitHub URL configured"}
    try:
        os.makedirs(UPDATE_DIR, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=UPDATE_DIR).name
        _download(url, tmp)
        if not zipfile.is_zipfile(tmp):
            os.remove(tmp)
            return {"ok": False, "applied": False, "error": "download is not a valid zip"}
        # Locate the app root inside the repo zip = the folder holding csp_dashboard/app.py
        with zipfile.ZipFile(tmp) as z:
            names = [n.replace("\\", "/") for n in z.namelist() if not n.endswith("/")]
        approot = next((n[:-len("app.py")] for n in names
                        if n.endswith("/csp_dashboard/app.py") or n == "csp_dashboard/app.py"), None)
        if not approot:
            os.remove(tmp)
            return {"ok": False, "applied": False, "error": "csp_dashboard/ not found in repo zip"}
        if os.path.isdir(STAGING):
            shutil.rmtree(STAGING, ignore_errors=True)
        os.makedirs(STAGING, exist_ok=True)
        with zipfile.ZipFile(tmp) as z:
            for m in z.namelist():
                mm = m.replace("\\", "/")
                if mm.startswith(approot) and not mm.endswith("/"):
                    target = os.path.join(STAGING, mm[len(approot):].replace("/", os.sep))
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(m) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        os.remove(tmp)
        with open(PENDING, "w", encoding="utf-8") as f:
            json.dump({"version": "github-latest", "source": url}, f)
        res = apply_pending()
        if res.get("applied"):
            refresh_dependencies()
        return res
    except Exception as e:
        return {"ok": False, "applied": False, "error": str(e)}


if __name__ == "__main__":
    import sys
    if "--from-github" in sys.argv:
        print("[updater] pulling the latest app from GitHub...")
        res = apply_from_github()
        if res.get("applied"):
            print(f"[updater] updated from GitHub ({res.get('files')} files)")
            refresh_dependencies()
        elif res.get("ok"):
            print("[updater] already up to date (nothing changed).")
        else:
            print(f"[updater] GitHub update FAILED: {res.get('error')}")
    elif "--apply-if-pending" in sys.argv:
        res = apply_pending()
        if res.get("applied"):
            print(f"[updater] applied update -> {res.get('version')} "
                  f"({res.get('files')} files, {res.get('pruned', 0)} orphan(s) "
                  f"pruned; backup: {res.get('backup')})")
            # New code is in place; bring its dependencies up to date before the
            # app starts (cheap no-op unless this release added/changed a dep).
            refresh_dependencies()
        elif not res.get("ok"):
            print(f"[updater] update apply FAILED: {res.get('error')}")
    elif "--rollback" in sys.argv:
        res = rollback_last()
        if res.get("ok"):
            print(f"[updater] rolled back to {res.get('version')} "
                  f"({res.get('files')} files restored)")
        else:
            print(f"[updater] rollback FAILED: {res.get('error')}")
            sys.exit(1)
    elif "--apply-zip" in sys.argv:
        i = sys.argv.index("--apply-zip")
        zpath = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        res = apply_local_zip(zpath)
        if res.get("applied"):
            print(f"[updater] applied update -> {res.get('version')} "
                  f"({res.get('files')} files)")
        else:
            print(f"[updater] update FAILED: {res.get('error')}")
            sys.exit(1)
    elif "--pending" in sys.argv:
        print(pending_version() or "")
