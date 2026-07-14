"""
Authentication helpers — password hashing, verification, and login throttling.

Passwords are stored as salted PBKDF2-HMAC-SHA256 (100k iterations).
Stored format:  pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

A legacy bare-SHA-256 hash (64 hex chars, no '$') is still accepted on login
so databases seeded before this change keep working; on next successful login
the password could be re-hashed by the caller if desired.

Login throttling is in-memory (single-process desktop app): after
MAX_ATTEMPTS failures for a login id, that id is locked for LOCKOUT_SECONDS.
"""

import hashlib
import hmac
import os
import time

_ITERATIONS = 100_000
_ALGO = "pbkdf2_sha256"

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5 minutes

# login_id → {"fails": int, "locked_until": float}
_attempts = {}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False

    # Legacy bare SHA-256 (no salt) — accept for backward compatibility.
    if "$" not in stored:
        legacy = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(legacy, stored)

    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def is_locked(login_id: str) -> float:
    """Return remaining lockout seconds (0 if not locked)."""
    rec = _attempts.get(login_id)
    if not rec:
        return 0
    remaining = rec.get("locked_until", 0) - time.monotonic()
    return remaining if remaining > 0 else 0


def record_failure(login_id: str):
    rec = _attempts.setdefault(login_id, {"fails": 0, "locked_until": 0})
    rec["fails"] += 1
    if rec["fails"] >= MAX_ATTEMPTS:
        rec["locked_until"] = time.monotonic() + LOCKOUT_SECONDS
        rec["fails"] = 0


def record_success(login_id: str):
    _attempts.pop(login_id, None)
