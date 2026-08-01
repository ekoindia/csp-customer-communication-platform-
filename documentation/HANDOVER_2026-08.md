# CSP Platform — Handover & Current Condition

Prepared: 2026-08-01, at the close of the internship period, for handover to
whoever continues this project at Eko.

This document is a snapshot. For the deep technical reference see `CLAUDE.md`
(core platform) and `PROJECT_DOCUMENTATION.md` (full history of decisions,
dated 2026-07-21 — this handover doc supersedes its "Current status" section
with what changed since).

---

## 1. What is live and working

- CSP dashboard (`csp_dashboard/`): feature-complete. Upload → OCR/parse →
  review gate → case creation → message generation → WhatsApp/SMS send →
  two-level tracking. 252 automated tests passing as of this handover.
- Admin portal (`admin_dashboard/`): live, auto-deploying from GitHub `main` to
  Eko's RAG server via cron. Fleet view only — receives no customer PII by
  design (see schema comments in `admin_dashboard/schema.sql`).
- Mobile scanner (`csp_dashboard/mobile_scanner/`) + Android APK
  (`mobile_app/`, built by `.github/workflows/build-apk.yml`): client-side,
  on-device capture/OCR/encryption; no upload.
- Encrypted desktop import path (`.cspx`, `core/import_crypto.py`): lets a
  phone-scanned Excel reach the desktop without ever being sent in the clear.

## 2. DPDP remediation carried out 2026-07-31 / 2026-08-01

A review turned up real-customer-data traces that should never have existed in
a DPDP-governed codebase. All of the following were found and fixed in this
pass (full detail: `documentation/OCR_CUSTOM_MODEL_PLAN.md` §4a):

| Issue found | Fix applied |
|---|---|
| OCR model fine-tuned on ~1,600 real customer account/mobile digit crops (real bank scan), deployed to production | Fine-tune checkpoint, harvest script, harvest logs, and the crops themselves deleted. Deployed `crnn.onnx` reverted to the synthetic-only export. **Consequence: local OCR digit accuracy drops from 66.5% to 39.2% on real cells** — acceptable only because scanned uploads now default to server OCR, with local ONNX as fallback. Retraining must use `ocr_training/synth.py` output only, never a real document. |
| Real customer name + account number + mobile number triples hardcoded into two committed test files, plus quoted in a source comment and a doc | Replaced with synthetic values (same series as `scripts/make_dummy_data.py`); full test suite re-verified green after the change |
| Hardcoded path to a real scanned bank document (`data/DocScanner ... .pdf`) in a training script and a doc | Removed/reworded; the file itself no longer exists |
| Mobile scanner page (`mobile_scanner/scan.html`) loaded Tesseract.js / SheetJS / PDF.js from `cdn.jsdelivr.net` and language data from `tessdata.projectnaptha.com` | All libraries vendored locally under `mobile_scanner/lib/` (integrity: `lib/SHA256SUMS`); page now ships a Content-Security-Policy blocking every external origin; `build-apk.yml` fails the build if a CDN URL reappears. The Android APK was already safe (no `INTERNET` permission) — this closed the gap on the browser/PWA copy. |

**As of this handover, these fixes exist as uncommitted working-tree changes
(and possibly a rewritten commit history, depending on what was decided when
this doc was finalized — see the "Repository / GitHub state" section below for
the actual outcome).**

## 3. Still outstanding — not fixed, needs a decision from the new owner

These were found during the same review and are **real, but deliberate
tradeoffs or bigger decisions**, not something to silently patch:

- `csp_dashboard/.env` contains a **plaintext SSH password** for the RAG
  server (`rag_server_pass`). Rotate this credential once handover access is
  settled — it should not continue to be known by someone leaving the project.
- `database/pii.key` sits in the same folder as `csp_platform.db`. If the CSP
  machine itself is physically seized, the key and the ciphertext are seized
  together, which weakens the "encrypted at rest" guarantee in CLAUDE.md §15
  rule 13. Needs the key path made configurable (env var / external media).
- The admin heartbeat (`core/admin_reporter.py`) uploads a full Windows
  DxDiag report per CSP machine. It's meant as machine diagnostics, but a full
  DxDiag can carry the Windows account/machine name — worth a second look
  before scaling the fleet.
- Server-side OCR (`documentation/SERVER_OCR_PIPELINE_DESIGN.md`) is live in
  code (`core/server_ocr_client.py`, `admin_dashboard/api.py`
  `/api/v1/ocr/extract`) but its own design doc requires mTLS + an attested
  enclave + a signed Data Processing Agreement with SBI before this is
  actually DPDP-safe at scale. None of those three exist yet — today the
  envelope is only as strong as the per-CSP API key. Do not expand this beyond
  pilot use until the DPA is signed (`documentation/SBI_DPA_PROPOSAL.md`).
- SBI digital export (Track A in `PROJECT_DOCUMENTATION.md` §4.4), the
  official WhatsApp Business API move (ban-risk docs in `documentation/`), and
  MSG91/DLT activation are all still pending, as recorded there — nothing
  changed on those since 2026-07-21.

## 4. Repository / GitHub state

This repo has a single contributor in its commit history:
`PRATEEK638 <187721848+PRATEEK638@users.noreply.github.com>`, 101 commits,
2026-07-14 → 2026-07-30. 99 of those carry a `Co-Authored-By: Claude ...`
trailer (this was written using Claude Code, an AI pair-programming tool, for
the duration of the internship).

*(Fill in here, after the fact, whichever of these actually happened:)*
- [ ] Trailers left as-is; new commits going forward use a different identity.
- [ ] All 101 commits' trailers were stripped and the history was
      force-pushed to `origin/main` on 2026-08-0_. A pre-rewrite backup ref
      was kept locally as `backup-before-history-rewrite` in case anything
      needed to be recovered.
- [ ] Not touched; left for the new owner to decide.

## 5. How to run this

See `README.md` "Quick start" — unchanged by this handover. Tests:
`cd csp_dashboard && python -m pytest` (252 passing at handover time).

## 6. Document index

- `CLAUDE.md` — full technical reference, core platform.
- `PROJECT_DOCUMENTATION.md` — full project history and decisions (through
  2026-07-21).
- `README.md` — quick start.
- `documentation/OCR_CUSTOM_MODEL_PLAN.md` §4a — the DPDP remediation record.
- `documentation/SERVER_OCR_PIPELINE_DESIGN.md`, `SBI_DPA_PROPOSAL.md` —
  gating items for scaling server OCR.
- `documentation/GO_LIVE_RUNBOOK.md`, `INSTALL_GUIDE.md` — deployment.
