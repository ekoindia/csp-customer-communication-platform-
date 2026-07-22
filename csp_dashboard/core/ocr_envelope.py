"""
Small app-layer encryption envelope for centralized OCR requests.

Tier 1 still runs over HTTPS and validates the per-CSP API key. This envelope
adds authenticated encryption for the document body so the route handler only
sees plaintext after auth succeeds. Tier 2 can replace the shared-secret key
derivation with an attested enclave public key while keeping the API shape.
"""
import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1
AAD = b"CSPOCR1"
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
PBKDF2_ITERS = 200_000


class EnvelopeError(Exception):
    """Raised when an OCR envelope is missing, malformed, or fails auth."""


def _b64e(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def _b64d(value: str, field: str) -> bytes:
    try:
        return base64.b64decode(str(value or "").encode("ascii"), validate=True)
    except Exception as e:
        raise EnvelopeError(f"bad {field}") from e


def derive_key(secret: str, salt: bytes) -> bytes:
    if not secret:
        raise EnvelopeError("missing secret")
    if len(salt) != SALT_LEN:
        raise EnvelopeError("bad salt")
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt,
                               PBKDF2_ITERS, dklen=KEY_LEN)


def encrypt_json(data: dict, secret: str, salt: bytes = None,
                 nonce: bytes = None) -> dict:
    salt = salt or os.urandom(SALT_LEN)
    nonce = nonce or os.urandom(NONCE_LEN)
    if len(nonce) != NONCE_LEN:
        raise EnvelopeError("bad nonce")
    plaintext = json.dumps(data, separators=(",", ":")).encode("utf-8")
    body = AESGCM(derive_key(secret, salt)).encrypt(nonce, plaintext, AAD)
    return {
        "version": VERSION,
        "salt": _b64e(salt),
        "nonce": _b64e(nonce),
        "body": _b64e(body),
    }


def decrypt_json(envelope: dict, secret: str) -> dict:
    if not isinstance(envelope, dict):
        raise EnvelopeError("missing envelope")
    if int(envelope.get("version") or 0) != VERSION:
        raise EnvelopeError("unsupported envelope version")
    salt = _b64d(envelope.get("salt"), "salt")
    nonce = _b64d(envelope.get("nonce"), "nonce")
    body = _b64d(envelope.get("body"), "body")
    if len(nonce) != NONCE_LEN:
        raise EnvelopeError("bad nonce")
    try:
        plain = AESGCM(derive_key(secret, salt)).decrypt(nonce, body, AAD)
        out = json.loads(plain.decode("utf-8"))
    except Exception as e:
        raise EnvelopeError("could not decrypt envelope") from e
    if not isinstance(out, dict):
        raise EnvelopeError("bad plaintext")
    return out
