"""Password hashing, verification, and login throttling."""

import hashlib
import time
from core import auth


def test_hash_round_trip():
    h = auth.hash_password("secret123")
    assert h.startswith("pbkdf2_sha256$")
    assert auth.verify_password("secret123", h)
    assert not auth.verify_password("wrong", h)


def test_hash_is_salted():
    """Two hashes of the same password must differ (random salt)."""
    assert auth.hash_password("same") != auth.hash_password("same")


def test_legacy_sha256_accepted():
    legacy = hashlib.sha256("changeme".encode()).hexdigest()
    assert auth.verify_password("changeme", legacy)
    assert not auth.verify_password("nope", legacy)


def test_empty_stored_rejected():
    assert not auth.verify_password("anything", "")


def test_lockout_after_max_attempts():
    uid = "lock_test_user"
    auth.record_success(uid)  # clear any prior state
    for _ in range(auth.MAX_ATTEMPTS):
        assert auth.is_locked(uid) == 0
        auth.record_failure(uid)
    assert auth.is_locked(uid) > 0


def test_success_clears_failures():
    uid = "clear_test_user"
    auth.record_failure(uid)
    auth.record_failure(uid)
    auth.record_success(uid)
    assert auth.is_locked(uid) == 0
