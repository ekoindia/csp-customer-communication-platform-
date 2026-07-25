# Self-Healing & Self-Diagnosing — full lifecycle

Goal (owner directive, 2026-07-25): the platform must **self-check, self-heal,
and clearly self-diagnose** across its whole life — install → upload → OCR →
review → cases → messaging → tracking → case closure. No silent failures; when
something can't proceed, it says exactly why (and fixes what it can itself).
Extraction must be **format-agnostic**: whatever is in the document comes
through — no dependency on a fixed column layout.

## Stage-by-stage

### 1. Installation / deployment — SELF-HEALING ✅ (done)
- `CSP_Setup.bat` downloads a **~300 KB slim package** from the Eko server (the
  same reachable host used for OCR), not the 83 MB GitHub zip that failed on CSP
  networks.
- `INSTALL.bat` installs **Python 3.11 / Node LTS by direct python.org /
  nodejs.org HTTPS** (winget hangs on CSP networks) — and skips them if already
  present.
- Dependency install is **self-healing**: pip retries once on failure, then
  **verifies every critical library actually imports**, and if any is missing
  does a clean `--force-reinstall` and re-verifies before giving up.
- `deploy_check.py` prints a final readiness verdict (GO / NO-GO) and is
  server-OCR aware.
- **Remaining:** auto-detect + surface an offline/blocked-network state with a
  one-line fix hint; optional auto-launch-on-failure diagnostics bundle.

### 2. Upload → OCR → extraction — SELF-DIAGNOSING + FORMAT-AGNOSTIC ✅ (done)
- **No silent empty drafts.** Every reason a draft has 0 rows is captured into
  `meta.ocr_diag` and shown as a **red banner on the review screen** with the
  exact cause: server OCR unreachable / bad-or-missing API key / server returned
  0 rows (poor/rotated scan) / typed-file-with-no-table / empty file — plus the
  suggested fix (clearer scan, or upload the bank Excel/CSV). See
  `core/extraction.py` (`_diag`) and `dashboard/templates/review.html`.
- **Format-agnostic.** Column mapping is two-pass (exact header wins over
  substring, so `NAME` beats `BRANCH_NAME`) and covers every real layout seen
  (standard, Khusrupur `A/C No`, Sanjeev Excel `ACNO`, Sanjeev PDF
  `MEMB_CUST_AC`/`MOBILE_NBR`). Missing balance band → safe default (normal /
  template_1). Blank mobile → kept as "not reachable". **Every original column
  is carried through** (`row["_all"]`) so nothing in the document is dropped.
- CSV / Excel / typed-PDF are parsed **locally** (never sent to the server);
  only scanned PDF/image go to server OCR (encrypted, RAM-only, zero retention).
- **Remaining:** render the carried `_all` columns as a dynamic table on the
  review screen (data is already there; UI still shows the fixed editable grid).
  Orientation auto-fix for rotated scans on the server OCR (verify + tune).

### 3. Review → case creation — GRACEFUL ✅
- Pydantic validator defaults everything optional; a garbled band is flagged
  (not dropped); duplicate accounts dedup by blind index; `commit_draft` ignores
  the new `_all` field. One customer = one case ("N stays N").

### 4. Messaging / dispatch — GRACEFUL FALLBACK ✅ (by design)
- WhatsApp fail → SMS → escalate to CSP. Blank mobile is never dialled. Pause/
  Stop honoured within ~1 s.
- **Remaining:** surface per-message failure reasons in the UI the same way
  extraction now does.

### 5. Tracking → case closure — GRACEFUL ✅
- Business-status state machine; on `case_closed` the identifying PII is
  **irreversibly purged** (`purge_case_pii`); a startup sweep clears any
  leftover uploads. DPDP-safe.

## Cross-cutting: fresh operational reset (owner request, pending confirm)
- Revoke ALL existing per-CSP API keys and re-issue fresh ones; serve ONE clean
  `CSP_Setup.bat` per CSP; delete stale/error-prone setups. This is DESTRUCTIVE
  (every current install must be re-keyed) — do only on explicit go-ahead. The
  admin **API Keys** page already supports issue/rotate/revoke per CSP; a
  "revoke all" bulk action is the only missing piece.

## Test coverage
`tests/test_extraction.py` (empty-upload self-diagnosis, format-agnostic
all-columns carry), `tests/test_column_mapper.py` (all real header layouts),
`tests/test_classifier.py`, `tests/test_validator.py`. Full suite: 144 passing.
