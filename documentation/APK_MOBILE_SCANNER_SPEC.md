# CSP Mobile Scanner App (APK) — build spec

**Status:** design agreed 2026-07-20. Desktop side (decrypt + import + passphrase
setting) is built in `csp_dashboard/`. This document is the contract the Android
app must follow so its output opens correctly on the desktop.

---

## 1. Why this exists

On the real 4 GB deploy PC, desktop OCR under-extracts scanned bank documents
(measured). Instead of fighting OCR on weak hardware, the **CSP's phone** does
the scanning + OCR, produces a clean Excel, and hands it to the existing desktop
platform — which is **100 % accurate on Excel with no OCR at all**.

The catch is DPDP: an Excel of the whole bank list is heavy PII (names, mobiles,
account numbers together). The platform's hard rule is **no customer data on any
cloud / foreign server**. So:

- OCR runs **on the phone, offline** — nothing leaves the device during scanning.
- The Excel is **encrypted on the phone** into a `.cspx` file **before** it ever
  touches WhatsApp. WhatsApp only ever carries an opaque encrypted blob.
- Only the paired desktop app (holding the shared passphrase) can decrypt it.

## 2. End-to-end flow

```
📱 APK:  scan pages (camera) → PDF/images
         → ON-DEVICE OCR  (Google ML Kit Text Recognition, offline)
         → build Excel (columns in §3)
         → CSP reviews/fixes in the app  (aim: 100% correct)
         → ENCRYPT to  scan_<date>.cspx   (§4)
         → "Send via WhatsApp"  (to self / anyone)
💻 Desktop: WhatsApp Web → download the .cspx
         → CSP Dashboard → Documents → Upload the .cspx
         → app decrypts → normal Excel ingest → Review gate → messages
```

The desktop **review gate still runs** — every row is shown to the CSP before any
case is created, so an OCR slip is caught by a human, exactly as today. Accuracy
comes from that review + the Excel path, not from the phone being perfect.

## 3. Excel output format

- One sheet, first row = headers, one customer per row after that.
- **MVP: exactly four columns are needed.** The desktop maps headers
  case-insensitively by keyword (`core/column_mapper.py`), so these exact header
  strings are safest:

  | Column header | Meaning | Example |
  |---------------|---------|---------|
  | `account_number` | SBI account number | `34XXXXXXXX` |
  | `name` | Customer name | `RAMESH KUMAR` |
  | `mobile` | 10-digit mobile | `98XXXXXXXX` |
  | `balance_band` | Balance band label | `100<1000` |

- `balance_band` must be one of the four exact labels: `0.1<100`, `100<1000`,
  `1000<10000`, `B>10000`. (These drive template/urgency on the desktop.)
- Extra columns are ignored — harmless, but keep it to these four for clean scans.
- File type: real `.xlsx` (Office Open XML). CSV also works if easier on Android,
  but `.xlsx` is preferred.

## 4. `.cspx` encryption format — EXACT

The desktop reference implementation is `csp_dashboard/core/import_crypto.py`
(`encrypt_package` / `decrypt_package`). **That file is the source of truth** —
match it byte-for-byte. Summary:

Concatenated binary, in this order:

| Field | Size | Value |
|-------|------|-------|
| magic | 4 bytes | ASCII `CSPX` |
| version | 1 byte | `0x01` |
| salt | 16 bytes | random (PBKDF2 salt) |
| nonce | 12 bytes | random (AES-GCM IV) |
| body | rest | AES-256-GCM ciphertext **with the 16-byte tag appended** |

- **Key derivation:** `PBKDF2-HMAC-SHA256(passphrase_utf8, salt, iterations=200000, dkLen=32)`
- **Cipher:** `AES-256-GCM`. Plaintext = the `.xlsx` bytes.
- **AAD (additional authenticated data):** the **first 5 bytes** (`magic+version`)
  are passed as GCM AAD — authenticated but not encrypted. Must match on both sides.
- **Tag:** 128-bit, appended to the ciphertext (this is what `AESGCM.encrypt`
  returns in Python and what `Cipher.doFinal` produces in Java when using GCM).

### Android (Kotlin) reference

```kotlin
val MAGIC = "CSPX".toByteArray(Charsets.US_ASCII)
val header = MAGIC + byteArrayOf(0x01)           // 5-byte AAD

fun encrypt(xlsx: ByteArray, passphrase: String): ByteArray {
    val salt  = SecureRandom().generateSeed(16)
    val nonce = SecureRandom().generateSeed(12)
    val spec  = PBEKeySpec(passphrase.toCharArray(), salt, 200_000, 256)
    val key   = SecretKeySpec(
        SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            .generateSecret(spec).encoded, "AES")
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(128, nonce))
    cipher.updateAAD(header)
    val body = cipher.doFinal(xlsx)              // ciphertext + 16-byte tag
    return header + salt + nonce + body
}
```

## 5. Passphrase pairing (MVP)

- The CSP sets a passphrase (min 6 chars) **once in the app**, and enters the
  **same** passphrase in the desktop dashboard: **Settings → Phone Scanner Import
  → Import passphrase**.
- Same passphrase both sides → the file opens. Wrong passphrase → the desktop
  shows *"Wrong passphrase, or the file is corrupted"* and imports nothing.
- The passphrase is stored **locally on each device only** (desktop keeps it in
  its local config table; never sent anywhere).
- **v2 idea:** replace typing with QR pairing — desktop shows a QR of a random
  256-bit key, the app scans it once. Avoids passphrase mismatch entirely.

## 6. Interop test vector (verify your Android code against the desktop)

Run this on the desktop to produce a known-good file, then confirm your app can
both **produce** and **open** the identical format:

```python
from core import import_crypto
blob = import_crypto.encrypt_package(b"hello-xlsx-bytes", "test-pass-123")
open("sample.cspx", "wb").write(blob)
# round-trip:
assert import_crypto.decrypt_package(blob, "test-pass-123") == b"hello-xlsx-bytes"
```

Your APK must decrypt `sample.cspx` (with `test-pass-123`) back to
`hello-xlsx-bytes`, and a `.cspx` your APK produces must open with
`decrypt_package(..., "test-pass-123")`.

## 7. Hard rules (do not break)

1. **OCR must be on-device / offline.** No cloud OCR, no uploading the PDF/image
   anywhere. (DPDP — customer data never leaves the phone.)
2. **Never send the plaintext Excel over WhatsApp** — only the `.cspx`.
3. **Eko name never appears** in the app UI or output (CSP-facing only), same as
   the rest of the platform.
4. No account balance / no financial detail beyond the band label in the Excel;
   the message itself is generated on the desktop and stays generic.
