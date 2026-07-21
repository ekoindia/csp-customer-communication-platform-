"""
Decrypt the encrypted Excel package (.cspx) produced by the CSP mobile scanner
app (Android APK).

WHY a scheme separate from core/crypto.py:
  core/crypto.py uses Fernet, which is Python-specific. A .cspx file is produced
  on an Android phone, so both sides must agree on a plain, cross-platform
  format. We use AES-256-GCM with a key derived from a shared passphrase via
  PBKDF2-HMAC-SHA256 — available identically in Python (`cryptography`) and
  Android (`javax.crypto`), so the two implementations interoperate.

WHY it exists at all (DPDP):
  On the 4 GB deploy PC, desktop OCR under-extracts, so scanning moves to the
  CSP's phone. The phone does the OCR ON-DEVICE (nothing leaves the phone),
  produces an Excel, and the CSP moves that Excel to the PC over WhatsApp (their
  familiar flow). An Excel of the whole bank list is heavy PII — sending it as
  plaintext through WhatsApp would put customer names/mobiles/accounts on Meta's
  servers, breaking the platform's "no cloud / no foreign server" rule. So the
  phone ENCRYPTS the Excel into a .cspx before it ever touches WhatsApp; only
  this desktop app (holding the shared passphrase) can read it back. WhatsApp
  only ever carries an opaque blob.

FILE FORMAT (.cspx) — all binary, concatenated in this order:
    magic     4 bytes   b"CSPX"
    version   1 byte    0x01
    salt     16 bytes   random  (PBKDF2 salt)
    nonce    12 bytes   random  (AES-GCM nonce)
    body      n bytes   AES-256-GCM ciphertext WITH the 16-byte tag appended
                        (i.e. exactly what AESGCM.encrypt returns)
  KDF: PBKDF2-HMAC-SHA256, 200_000 iterations, 32-byte key.
  AAD: the 5-byte header (magic+version) is authenticated but not encrypted.

The passphrase is set ONCE by the CSP in the phone app and mirrored in the
desktop dashboard Settings (stored locally in the config table, never leaves the
PC). Same passphrase on both sides -> the file opens; wrong passphrase -> a clean
DecryptError, never a crash.
"""
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"CSPX"
VERSION = 1
_HEADER = MAGIC + bytes([VERSION])      # 5 bytes, used as GCM AAD
_PBKDF2_ITERS = 200_000
_SALT_LEN = 16
_NONCE_LEN = 12
_TAG_LEN = 16
_KEY_LEN = 32
_MIN_LEN = len(_HEADER) + _SALT_LEN + _NONCE_LEN + _TAG_LEN

# Canonical extension for the encrypted package.
EXT = ".cspx"


class DecryptError(Exception):
    """Raised for any problem opening a .cspx: no passphrase, wrong passphrase,
    truncated/corrupt file, or an unknown format version. Callers turn this into
    a friendly on-screen message — it never propagates as a 500."""


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt,
                               _PBKDF2_ITERS, dklen=_KEY_LEN)


def looks_like_cspx(blob: bytes) -> bool:
    """Cheap sniff: does this start with the CSPX magic? Used to give a clearer
    error than 'wrong passphrase' when the wrong kind of file is uploaded."""
    return len(blob) >= len(_HEADER) and blob[:len(MAGIC)] == MAGIC


def decrypt_package(blob: bytes, passphrase: str) -> bytes:
    """Decrypt a .cspx blob -> the original file bytes (an .xlsx). Never trusts
    the input length; raises DecryptError (not a low-level crypto error) on any
    failure so the upload route can show a friendly message."""
    if not passphrase:
        raise DecryptError("No import passphrase is set. Set it in Settings first.")
    if not looks_like_cspx(blob):
        raise DecryptError("This is not a valid .cspx file (bad header).")
    if len(blob) < _MIN_LEN:
        raise DecryptError("This .cspx file is truncated or corrupted.")
    version = blob[len(MAGIC)]
    if version != VERSION:
        raise DecryptError(f"Unsupported .cspx version ({version}); update the app.")
    off = len(_HEADER)
    salt = blob[off:off + _SALT_LEN]; off += _SALT_LEN
    nonce = blob[off:off + _NONCE_LEN]; off += _NONCE_LEN
    body = blob[off:]
    key = _derive_key(passphrase, salt)
    try:
        return AESGCM(key).decrypt(nonce, body, _HEADER)
    except Exception:
        # AES-GCM authentication failed: wrong passphrase or tampered bytes.
        raise DecryptError("Wrong passphrase, or the file is corrupted. "
                           "Check the passphrase matches the phone app.")


def encrypt_package(plaintext: bytes, passphrase: str,
                    salt: bytes = None, nonce: bytes = None) -> bytes:
    """Reference encoder — the SAME format the Android app must produce. Kept
    here so the desktop test-suite can round-trip against the real code path
    (and so the APK author has an exact, runnable reference). `salt`/`nonce` are
    injectable only for deterministic tests; production uses fresh random."""
    if not passphrase:
        raise DecryptError("passphrase required to encrypt")
    salt = salt or os.urandom(_SALT_LEN)
    nonce = nonce or os.urandom(_NONCE_LEN)
    if len(salt) != _SALT_LEN or len(nonce) != _NONCE_LEN:
        raise DecryptError("bad salt/nonce length")
    key = _derive_key(passphrase, salt)
    body = AESGCM(key).encrypt(nonce, plaintext, _HEADER)
    return _HEADER + salt + nonce + body
