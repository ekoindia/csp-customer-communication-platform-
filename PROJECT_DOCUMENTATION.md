# CSP Account Issue Communication Platform
# Full and Final Project Documentation

This is the single authoritative record of the project: what it is, how it is
built, every major decision and why, the current status, and what remains. For
deep module-level detail see `CLAUDE.md`. For specific topics see the linked
documents in the index at the end.

Last updated: 2026-07-21.

---

## 1. What the project is

An on-premise, campaign-driven communication platform for a CSP (Customer
Service Point) partnered with Eko Bharat Ventures and SBI. The bank shares lists
of accounts that need customer follow-up. The platform automates that outreach
in the CSP's name.

Non-negotiables:
1. The CSP's name goes on all messages. Eko's name never appears anywhere.
2. All customer data stays on the CSP's local machine. No cloud storage.
3. One-way notification only. No replies, no chatbot, no AI-generated messages.
4. DPDP Act compliance at every step.

First and only live campaign: Inoperative accounts (SBI accounts dormant for a
long period). Future campaigns (KYC, Aadhaar linking) reuse the same engine via
a new config folder only.

---

## 2. Architecture (five layers)

1. CSP Dashboard: Flask + Jinja + vendored Bootstrap. Login, campaign select,
   document upload, review, campaign dashboard. Hindi/English toggle.
2. Document processing: accept Excel, CSV, typed PDF, scanned PDF, image.
   Excel/CSV are parsed directly and are 100 percent accurate. Scans need OCR.
   Every extracted row is shown to the CSP next to the source in a review gate
   before any case is created.
3. Message creation: template engine only. No LLM. The message is locked and
   not editable on any screen.
4. Communication: WhatsApp first, SMS (MSG91) as fallback. One-way only.
5. Tracking: communication status (automatic via delivery events) and business
   status (manual, updated by the CSP).

Storage: a single local SQLite file. Customer identifying fields are encrypted
at rest (Fernet) and irreversibly purged when a case is closed.

---

## 3. Components as built

- CSP dashboard and engine: `csp_dashboard/` (Flask app, extraction, message
  engine, dispatcher, tracking, settings, auth). Feature-complete, 80-plus tests.
- Admin portal (Eko side): `admin_dashboard/`. Read-only fleet monitoring using
  only PII-free, aggregate data. Live on the shared RAG server at
  `http://122.176.147.78:8080/csp-admin/`. Auto-deploys from GitHub via a cron
  (see `admin_dashboard/deploy/`).
- Installer and updater: `INSTALL.bat` installs to `C:\CSP_Platform` with all
  dependencies. `UPDATE.bat` and the in-app updater pull the latest code from
  the public GitHub repo. GitHub is the source of truth and the compiler, not a
  runtime dependency.
- Encrypted document import (.cspx): `core/import_crypto.py` on the desktop and
  the client-side scanner page. Lets a scanned Excel be moved to the desktop in
  an encrypted form so customer data never travels in the clear.

---

## 4. The OCR problem and the final decision

OCR (reading the scanned bank lists into a structured table) is the central
technical constraint of the project. This section records what was tried, why
each was dropped, and the final approach.

### 4.1 Why OCR is hard here
The lists are dense, multi-column tables (account number, name, mobile, balance
band) on paper or field-quality scans. OCR must both read each character and
place it in the correct row and column. A single wrong digit in an account or
mobile number makes a row unusable, and those are the fields that must be exact.
Cleaning the image (contrast, resolution, deskew) reduces errors only slightly.

### 4.2 The core trade-off
Any OCR light enough to run on the CSP hardware is not accurate enough for these
tables, and any OCR accurate enough is too heavy to run on the CSP hardware. The
CSP deploy machine is a 4 GB Dell Inspiron with no GPU. This is a compute versus
accuracy trade-off, not a tuning or image-cleaning problem.

### 4.3 What was tried and dropped
1. Custom OCR model (tuning and pruning): abandoned. See
   `documentation/OCR_CUSTOM_MODEL_PLAN.md` (superseded). Model size grew and
   accuracy did not, and it could not run on the 4 GB machine anyway.
2. Desktop OCR (Tesseract, docTR, OnnxTR): under-extracts dense tables on the
   4 GB machine, which cannot run the more accurate models.
3. Mobile phone app (on-device OCR): built and functional end to end, but it
   FAILED on accuracy. It still relied on on-device OCR (Tesseract.js, then ML
   Kit), which carries the same trade-off, just on a different device. The
   accurate document models (for example the 3B Unlimited-OCR class) are too
   large to run on a phone. The phone did not solve the problem, it relocated
   it. See `documentation/APK_MOBILE_SCANNER_SPEC.md` (superseded).

### 4.4 The final decision (two tracks)
Track A, the best fix: obtain the list digitally from SBI (CSV or Excel). No OCR
is needed and accuracy is 100 percent. This is a relationship and process ask,
not a technical one, and is pursued at the leadership level.

Track B, build now and works regardless: run the accurate OCR on Eko's own
secure server, not on the CSP machines. The CSP machine does only capture,
review, and the WhatsApp campaign. This removes the hardware bottleneck and
gives high accuracy. It is designed so that no customer data is retained or
exposed by Eko, in line with the DPDP Act. See
`documentation/SERVER_OCR_PIPELINE_DESIGN.md` and, for the SBI agreement,
`documentation/SBI_DPA_PROPOSAL.md`.

On-device OCR is retained only as an optional offline fallback, not the primary
path.

---

## 5. Server-side OCR pipeline (Track B, summary)

- The CSP client encrypts the document, sends it over mutual TLS to Eko's OCR
  service, and receives back the extracted rows, also encrypted.
- The service decrypts in memory only, runs a high-accuracy table OCR model,
  returns the rows, and wipes memory. Nothing (image, text, identifiers) is ever
  written to disk, logged, cached, or reused. Only anonymous operational metrics
  are kept.
- Roles under DPDP: SBI is the Data Fiduciary, Eko is the Data Processor acting
  only on instruction, under a Data Processing Agreement.
- Two tiers: Tier 1 is a stateless zero-retention microservice (build now).
  Tier 2 moves the OCR into a hardware enclave (confidential computing) with
  remote attestation so even Eko operators cannot read the data. Same client API.
- The WhatsApp campaign stays entirely on the CSP machine.

Status: design finalized; Tier-1 service implementation in progress
(`server_ocr/`); DPA proposal drafted for SBI; go-live is gated on the DPA.

---

## 6. Communication channel

- Current: WhatsApp via a local Baileys server (no browser). SMS via MSG91 as
  fallback (DLT registration pending as an operational step).
- Planned move: to the official WhatsApp Business API via a BSP (CERF), because
  the unofficial route carries a ban risk. This changes the "WhatsApp is free"
  cost assumption. See the WhatsApp documents in the index.

---

## 7. DPDP compliance model

- Local-first: all customer data processing and storage on the CSP machine.
- Encryption at rest: customer identifying fields encrypted (Fernet); purged on
  case closure.
- On-screen masking: names and mobiles masked by default, revealed briefly on
  click.
- Message locked: never freely editable, so no non-compliant text can be typed.
- Uploads deleted after processing; a startup check clears any leftovers.
- Minimal data to third parties: WhatsApp and SMS receive only a phone number
  and generic message text.
- Server-side OCR (Track B): encryption in transit, zero retention, no secondary
  use, India-only processing, Eko as Data Processor under a DPA. Confidential
  computing (Tier 2) for the strongest guarantee.

---

## 8. Deployment

- CSP install: `INSTALL.bat` to `C:\CSP_Platform`. Updates via `UPDATE.bat` or
  the in-app updater pulling from GitHub. See `documentation/INSTALL_GUIDE.md`
  and `documentation/GO_LIVE_RUNBOOK.md`.
- Admin portal: live on the RAG server behind nginx at `/csp-admin/`, auto-
  deploying from GitHub main via cron (`admin_dashboard/deploy/`).
- Server OCR service: to be deployed on a GPU host in India (Tier 1 first).

---

## 9. Current status

Done and verified:
- CSP platform feature-complete, 80-plus tests passing.
- Admin portal live and auto-deploying.
- Encrypted `.cspx` import path on the desktop, with tests.

In progress:
- Server-side OCR Tier-1 service (`server_ocr/`).

Pending or decision-gated:
- Track A: SBI to share lists digitally (leadership ask).
- DPA with SBI (legal, blocks server-OCR go-live).
- WhatsApp move to official WABA via CERF (blocked on CERF API docs).
- MSG91 or DLT activation (operational).
- Tier-2 confidential computing upgrade for server OCR.

Abandoned:
- Custom OCR model.
- On-device OCR as the primary path (kept only as optional offline fallback).
- Mobile app as an OCR device (may remain only as a capture client).

---

## 10. Document index

Current and authoritative:
- `CLAUDE.md` — deep module-level platform reference.
- `README.md` — quick overview.
- `documentation/SERVER_OCR_PIPELINE_DESIGN.md` — server OCR architecture.
- `documentation/SBI_DPA_PROPOSAL.md` — DPA proposal for SBI.
- `documentation/ADMIN_PORTAL_ARCHITECTURE.md` — admin portal design.
- `documentation/INSTALL_GUIDE.md`, `documentation/GO_LIVE_RUNBOOK.md` — deploy.
- `documentation/WHATSAPP_TEMPLATES_FOR_META.md` and the WhatsApp docx files —
  WhatsApp channel and ban-risk material.
- `documentation/AI_CALLING_INTEGRATION_REVIEW.md` — voice-channel review.
- `documentation/PENDING.md`, `documentation/PROGRESS_AND_RAM_ISSUE_2026-07.md`
  — running status notes.

Superseded (kept for history, do not implement from these):
- `documentation/OCR_CUSTOM_MODEL_PLAN.md` — custom OCR model, abandoned.
- `documentation/APK_MOBILE_SCANNER_SPEC.md` — on-device phone OCR, superseded
  by server-side OCR (Section 4).
