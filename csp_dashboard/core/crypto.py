"""
Field-level PII encryption at rest.

Why: an RBI inspector (or anyone else) can physically visit a CSP and look at
the machine. Customer identifying fields (name, mobile, account number,
father's name, address) must not be sitting in the local SQLite file as plain,
human-readable text. This module encrypts those fields before they are
written, and decrypts them transparently when the app reads them back — the
dashboard, message engine, and dispatcher all keep working exactly as before;
only the bytes on disk change.

Key: a local Fernet key, generated on first use and stored as `pii.key` next
to the database (same folder as config.DB_PATH — NEVER committed, see
.gitignore). Read fresh from disk on every call rather than cached in a module
global — the file is a few dozen bytes, and reading it directly means the key
always matches whatever config.DB_PATH currently points at (important for
tests, which each use an isolated temp DB).

Losing pii.key makes any STILL-OPEN case's PII permanently unrecoverable —
accepted, because a case's PII is purged anyway once it reaches case_closed
(see database.queries.purge_case_pii): the encryption key only needs to
survive for a case's active lifetime, not forever.

Account-number dedup: Fernet is non-deterministic (a fresh nonce every call),
so two encryptions of the same account number never match with a SQL `=`
lookup. account_hash() is a separate, deterministic, one-way HMAC-SHA256 index
used ONLY for the exact-match dedup lookup (database.queries.account_exists) —
it cannot be reversed back into the account number, so it is deliberately KEPT
even after a case's PII is purged (dedup must keep working for the account's
whole lifetime, independent of any one case's retention).
"""
import hashlib
import hmac
import os

from cryptography.fernet import Fernet, InvalidToken

import config


def _key_path() -> str:
    return os.path.join(os.path.dirname(config.DB_PATH) or ".", "pii.key")


def _load_key() -> bytes:
    path = _key_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(key)
    return key


def encrypt_field(value) -> str | None:
    """Encrypt a PII string for storage. None/'' -> None (nothing to protect)."""
    if value is None or value == "":
        return None
    token = Fernet(_load_key()).encrypt(str(value).encode("utf-8"))
    return token.decode("ascii")


def decrypt_field(token) -> str | None:
    """Decrypt a stored PII value. None/'' -> None. A corrupted/foreign token
    (wrong key, damaged data) returns None instead of raising, so a crypto
    problem degrades to a blank field in the UI rather than crashing the
    dashboard."""
    if token is None or token == "":
        return None
    try:
        return Fernet(_load_key()).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


def account_hash(normalized_account) -> str | None:
    """Deterministic, one-way blind index for exact-match account-number dedup.
    Cannot be reversed back to the account number."""
    if not normalized_account:
        return None
    return hmac.new(_load_key(), str(normalized_account).encode("utf-8"),
                    hashlib.sha256).hexdigest()
