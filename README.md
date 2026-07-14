# CSP Account Issue Communication Platform

**On-premise, campaign-driven communication platform** for a CSP (Customer
Service Point) operator partnered with **Eko Bharat Ventures** and **SBI
Bank**. It reads a bank document listing customers with account issues and
notifies each one — **in the CSP's own name only** — via WhatsApp, with SMS
fallback, while tracking delivery and the CSP's own follow-up. All customer
data stays on the CSP's PC; no cloud APIs, no LLM/AI anywhere in the pipeline.

> This file merges everything previously spread across `README.md`,
> `CLAUDE.md`, `PROJECT_REPORT.md`, `PRODUCTIZATION_ROADMAP.md`,
> `ADMIN_PORTAL_DESIGN.md`, and `EXTERNAL_DATA_REGISTER.md` into one place.
> Those files still exist (nothing was deleted) but this is now the single
> source of truth. Not merged in: `EMAIL_DATA_REQUEST.md` (literal email
> text, not documentation) and `instructions.md` / `Read claude_operating_
> instructions.md` (the original raw meeting-feedback record — a historical
> input, not project documentation).

**Status:** Feature-complete on the core platform · 80 automated tests
passing · First campaign ("Inoperative Accounts") operational · A
productization phase (Eko Admin Portal + commission tracking) is designed
and partly blocked on data from the product owner (see §12).

---

## Table of contents

1. [What this is, and the problem it solves](#1-what-this-is-and-the-problem-it-solves)
2. [How it works — architecture](#2-how-it-works--architecture)
3. [How a case is actually stored (data flow)](#3-how-a-case-is-actually-stored-data-flow)
4. [Balance bands, classification, and message templates](#4-balance-bands-classification-and-message-templates)
5. [Communication layer & approval workflow](#5-communication-layer--approval-workflow)
6. [Two-level tracking](#6-two-level-tracking)
7. [Dashboard walkthrough](#7-dashboard-walkthrough)
8. [Tech stack](#8-tech-stack)
9. [DPDP Act 2023 compliance — in full detail](#9-dpdp-act-2023-compliance--in-full-detail)
10. [Hardware & portability](#10-hardware--portability)
11. [Security](#11-security)
12. [Current status — done, tested, and honestly pending](#12-current-status--done-tested-and-honestly-pending)
13. [Productization — becoming an Eko service](#13-productization--becoming-an-eko-service)
14. [Eko Admin Portal — roles & design (proposal)](#14-eko-admin-portal--roles--design-proposal)
15. [What's blocked — data/decisions needed from the product owner](#15-whats-blocked--datadecisions-needed-from-the-product-owner)
16. [Roadmap](#16-roadmap)
17. [Setup, daily use, and testing](#17-setup-daily-use-and-testing)
18. [Project layout](#18-project-layout)
19. [Cost](#19-cost)
20. [Design principles — never violate](#20-design-principles--never-violate)
21. [Scope boundaries — what not to build](#21-scope-boundaries--what-not-to-build)

---

## 1. What this is, and the problem it solves

The bank periodically sends the CSP a list of customers with an account
issue (e.g. an "inoperative account" — no customer-initiated transaction for
a long period). Until now the CSP called each one manually.

| Before (manual) | After (this platform) |
|---|---|
| CSP reads the bank document by hand | Auto-extracts every record (Excel/CSV/PDF/scan) |
| Calls each customer one by one | Sends a compliant WhatsApp message to the whole batch |
| No script, inconsistent wording | Deterministic templates, always compliant |
| No record of who was contacted | Full delivery + business tracking per customer |
| Hours of repetitive work | One upload, reviewed and dispatched in minutes |

One upload turns into an entire batch (~200–250 customers) reviewed,
messaged, and tracked on a single dashboard, well under an hour. The CSP
stays in control at two human checkpoints: a **data-review gate** before any
case is created, and an **approval decision** before any message is sent.

**"Inoperative Accounts" is the only campaign built and live today.** The
platform itself is **campaign-independent** — a future campaign (KYC
Pending, Aadhaar Linking) plugs in as configuration, with no change to the
core engine (see §20).

---

## 2. How it works — architecture

Five independent, loosely-coupled layers. Each can be understood, tested,
and replaced on its own; the core (layers 2–5) never changes for a new
campaign — only the campaign's own configuration does.

```text
┌──────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — CSP DASHBOARD (Flask + Jinja2 + Bootstrap)                │
│  Login → Welcome (campaign select) → Documents (upload+history)      │
│        → Review (compare/zoom/undo) → Campaign Dashboard             │
│        (Overview · Cases · Reports · Settings)                       │
└───────────────┬────────────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────────────┐
│  LAYER 2 — DOCUMENT PROCESSING                                        │
│  Format detect → parse (Excel/CSV, 100% exact) OR OCR (PDF/scan/image)│
│  → validate (Pydantic) → classify by balance band → REVIEW GATE       │
└───────────────┬────────────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────────────┐
│  LAYER 3 — MESSAGE CREATION (template engine — no AI)                 │
│  Balance band → template → fill {name, CSP name, phone, address}      │
│  Message is LOCKED (non-editable) once generated                      │
└───────────────┬────────────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────────────┐
│  LAYER 4 — COMMUNICATION                                              │
│  Approval decision → WhatsApp (Baileys) → SMS fallback (MSG91)        │
│  → escalate. Batch dispatch with pause / resume / stop.                │
└───────────────┬────────────────────────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────────────────────────┐
│  LAYER 5 — TRACKING & CASE MANAGEMENT                                 │
│  (a) Communication tracking — automatic (delivery events)             │
│  (b) Business tracking — manual (CSP marks Visited → Done → Closed)   │
└──────────────────────────────────────────────────────────────────────┘

              All data in local SQLite — nothing in a cloud database
```

---

## 3. How a case is actually stored (data flow)

An earlier design considered storing each case as a pair of JSON files
("Initial JSON" → "Final JSON" on disk). **That was superseded during
implementation** by storing everything directly in the local SQLite
database — simpler, atomic, query-able — while keeping the same guarantee:
one customer = one case record, and the count never inflates (N rows
uploaded → exactly N cases, never more, never fewer).

```text
Upload document
   │
   ▼
Detect format ──► Excel / CSV  ─────────────► parse directly (100% exact, no OCR)
   │              PDF / scan / image ───────► OCR pipeline (docTR + Tesseract)
   ▼
Validate each row (Pydantic) + classify by balance band
   │
   ▼
REVIEW GATE  ── CSP sees every row beside the source (scanned page, or a
   │            read-only table for CSV/Excel), both zoomable, with
   │            Ctrl+Z/Ctrl+Y undo/redo — edits any cell, then confirms.
   │            Nothing becomes a real case until this step. The temporary
   │            draft (and any page images) is discarded once confirmed.
   ▼
Case created — one row in `customer_cases`, keyed by case_id
   │
   ▼
Message generated — one row in `messages` (CSP name only, locked/non-editable)
   │
   ▼
APPROVAL DECISION ── "Automate" (send all) OR "Manual review" (approve
   │                 case-by-case / in bulk, on the review-and-approve sheet,
   │                 undoable before sending) — creates a `pending` row in
   │                 `communication_attempts`
   ▼
Dispatch: WhatsApp → (on failure) SMS → (on both failing) escalate to CSP
   │                 `communication_attempts` updated IN PLACE as status changes
   ▼
Track: delivery status (automatic) + business status (CSP-driven, `business_tracking`)
```

So "a case" is really a join across four SQLite tables — `customer_cases`,
`messages`, `communication_attempts`, `business_tracking` — all keyed by the
same `case_id`, not two JSON file stages. On screen, the customer's **name
and mobile number are masked by default** (shown as initials/dots) and only
revealed briefly on the CSP's own click (§9).

**Logical shape of a case** (for reference — this is the data, not a file):
```json
{
  "case_id": "CASE_001",
  "campaign": "inoperative_accounts",
  "batch_id": "BATCH_2026_06_25",
  "customer": {
    "name": "RAMESH KUMAR", "mobile": "98XXXXXXXX",
    "account_number": "34XXXXXXXX", "father_name": "RAJU KUMAR",
    "balance_band": "100<1000", "village": "Ahiraule",
    "taluka": "Tamkuhi Raj", "address": "VILL-AHIRAULI DIST-KUSHINAGAR 274302"
  },
  "classification": { "band_label": "100<1000", "tone": "normal",
                       "template_id": "template_1", "is_sensitive": false },
  "csp": { "name": "Dudahi CSP", "phone": "98XXXXXXXX",
           "address": "Dudahi, Tamkuhi Raj, Kushinagar" },
  "communication": { "wa_message": "...", "sms_message": "...",
                      "channel": "whatsapp", "status": "wa_delivered",
                      "sent_at": "2026-06-25T08:05:00" },
  "tracking": { "business_status": "pending", "is_escalated": false,
                "message_sent_at": "2026-06-25T08:05:00",
                "visited_at": null, "closed_at": null }
}
```

---

## 4. Balance bands, classification, and message templates

| Band | Meaning | Tone | Template | Sensitive? |
|---|---|---|---|---|
| 0.1 < 100 | Near zero — account never used or drained | Normal | template_1 | No |
| 100 < 1000 | Low balance — account went dormant | Normal | template_1 | No |
| 1000 < 10000 | Real money sitting idle — urgent | Urgent | template_3 | No |
| B > 10000 | Large idle balance — urgent | Urgent | template_3 | No |

The classifier supports flagging a band `is_sensitive` (e.g. "possible
deceased account holder — verify before sending"); this was used for the top
band early on but is **off for every band today** — the review gate (§3) and
the case-detail page already give two independent checks before anything
sends, so no extra "verify first" flag is shown. The mechanism remains in
`campaigns/inoperative/config.json` for a future band if ever needed.

Internal-only reasons behind each band (never shown to the customer, used
only for classification): 0.1<100 → account never used/drained/moved to UPI;
100<1000 → migrant worker left / seasonal farmer / elderly can't visit;
1000<10000 and B>10000 → MNREGA/subsidy not collected, remittance from a
migrant family, or the owner's own idle savings.

**There are exactly two WhatsApp templates** (plus one SMS template), shared
across the four bands above. All messages are in the CSP's name only — no
bank name, no Eko Bharat name, mentioned explicitly.

**Template 1 — normal (0.1<100, 100<1000):**
```
Namaste {name} ji,

Aapke SBI bank account mein kaafi samay se koi
len-den nahi hua hai jiske karan account band
pada hai. Ise dobara chalu karwane ke liye
kripya humse sampark karein.

{csp_name}
{branch_address}
{csp_phone}

- {csp_name}
```

**Template 3 — urgent (1000<10000, B>10000):**
```
Namaste {name} ji,

Aapke SBI bank account mein kaafi samay se koi
len-den nahi hua hai jiske karan account band
pada hai. Kripya jald humse sampark karein.

{csp_name}
{branch_address}
{csp_phone}

- {csp_name}
```

**SMS (all bands, 160 chars max, Hinglish Roman script):**
```
Namaste {name} ji, aapka SBI account band pada
hai. Sampark karein: {csp_name} {csp_phone}
```

Variables: `{name}` from the reviewed customer data; `{csp_name}`,
`{branch_address}`, `{csp_phone}` from `config.py` / Settings. **The message
is locked** — never freely editable on any screen. A wording-change request
is routed through Eko (Help & Support in the productized version), not
edited directly.

**No AI, no LLM, no external API writes any message.** It is always the
same fixed, deterministic wording, filled in with the customer's name and
the CSP's own details — no risk of a wrong or non-compliant message.

---

## 5. Communication layer & approval workflow

- **Priority 1 — WhatsApp** via a local **Baileys** HTTP server
  (`whatsapp/wa_server.js`, Node.js) that talks to WhatsApp directly over the
  multi-device WebSocket protocol — **no browser, no Chromium, no visible
  window.** QR is scanned once from the dashboard; the session persists to
  `whatsapp/.wa_session/`. On logout (device unlinked from the phone), the
  server auto-clears the stale session and generates a fresh QR by itself; a
  "Reset & New QR" button in Settings forces this manually if ever needed.
- **Priority 2 — SMS fallback** via MSG91, only when WhatsApp fails (path
  fully built; MSG91 account/DLT activation is a pending **operational**
  step, not a code gap).
- **Escalation** — if both channels fail, the case is flagged for a manual
  CSP visit.
- **Batch dispatch controls** — the CSP picks how many to send, then
  **Pause / Resume / Stop** at any time, responsive within about a second
  (the inter-message delay is checked in 1-second slices). A safe delay (12s)
  and a daily limit (200) protect the WhatsApp number.
- **One-way only** — the system sends and listens only for delivery status;
  it never reads, stores, or replies to customer messages.

**Approval — the CSP always decides.** Clicking **Start Messaging** opens an
attention dialog with two paths:
- **Automate** — queue and send the whole batch automatically (most urgent
  first).
- **Manual review** — opens a **review-and-approve sheet** listing every
  *reachable* customer (not-reachable ones are excluded here — they appear
  in the Escalations panel instead). The CSP can open any case to see and
  **edit** its full record (until it's queued/sent), **Approve** it (a tick
  appears), **Undo** an approval, **Approve All Remaining**, and finally
  **Send Approved Now**. Nothing sends until that click.

Any future `is_sensitive`-flagged band is never auto-sent — it always
requires an individual, explicit approval, enforced in the backend, not just
the UI (see §4).

---

## 6. Two-level tracking

| Communication tracking (automatic) | Business tracking (manual, CSP-driven) |
|---|---|
| Updated by WhatsApp message-status events / MSG91 webhooks | Updated by CSP clicks on the dashboard |
| pending → wa_attempted → wa_delivered → wa_read (or wa_failed → sms_sent → sms_delivered/sms_failed → escalated) | pending → customer_not_visited → customer_visited_in_progress → process_completed → case_closed |
| WhatsApp and SMS shown **separately**, delivered vs read distinguished | `pending` can also jump straight to `customer_visited_in_progress` (a customer may walk in before/without a message being sent) |

Baileys message-status codes: 1=pending, 2=sent (server ack), 3=delivered,
4=read, 5=played (voice note, treated as read). Read receipts never arrive if
the recipient has them turned off — that's expected, not an error.

---

## 7. Dashboard walkthrough

**Page 1 — Login.** CSP ID + password; lockout after 5 failed attempts;
Hindi/English toggle available here too.

**Page 2 — Welcome.** Campaign selection only (Inoperative Accounts = active,
others = "coming soon"). No upload here.

**Page 3 — Documents.** Upload zone (drag-drop or click) for
Excel/CSV/PDF/scanned PDF/image; a page-range input appears **only when a
PDF is selected** (hidden for CSV/Excel/image, since those never have
"pages"); upload history with Open Dashboard / Delete per batch.

**Review screen** (after upload, before the dashboard). Extracted rows next
to the source (scanned page, or a read-only table for CSV/Excel), both
panels zoomable (−/+/Reset); every field editable; Ctrl+Z/Ctrl+Y to
undo/redo; a live "segregation" summary (see below); nothing is created
until **Confirm & Create Cases**.

> **What "segregation" means:** each extracted row is classified, before any
> case is created, into **normal** (routine low-balance bands), **urgent**
> (higher balance — send sooner), **not reachable** (no valid mobile — never
> messaged, kept for manual follow-up), or **needs a fix** (name missing or
> band unreadable — must be corrected before use). It gives the CSP an
> at-a-glance shape of the batch, and drives which template/tone a case gets
> and whether it may be auto-sent.

**Page 4 — Campaign Dashboard**, four tabs:
- **Overview** — metric cards (Total / Reached / Failed); a mini-metrics row
  (visited, pending visit, reach rate, WA delivered, SMS delivered); progress
  bars (reached / visited / closed); separate **WhatsApp** and **SMS**
  channel-breakdown cards (sent/delivered/read/failed); category-breakdown
  bars per balance band; an Escalations panel; a flagged (`is_sensitive`)
  panel — not shown today since no band is currently flagged (§4).
- **Cases** — full table, masked name/mobile (click to reveal both
  together, briefly), filters (band / comm status / business status),
  search, spreadsheet-style keyboard navigation (arrows, Home/End, Enter to
  open a row), and a per-row action button (Visited → Done → Close).
- **Reports** — batch summary, escalation list, visit log, downloadable CSV.
- **Settings** — CSP name/phone/address, WhatsApp status + QR + Reset button,
  SMS status, link to the audit log.

**Case detail page** (open from anywhere). Full record, editable until
queued/sent; Approve for Sending; communication history (channel, status,
date, time in IST — shown separately, not run together — and detail);
Prev/Next through the batch (arrow keys too).

---

## 8. Tech stack

| Component | Tool | Why |
|---|---|---|
| Language / backend | Python 3.11 | Single-language backend |
| Web dashboard | Flask + Jinja2 + Bootstrap 5 (vendored locally, no CDN) | Pure Python, no frontend framework |
| WhatsApp | **Baileys** (`@whiskeysockets/baileys`, Node.js) | Free, no browser/Chromium, real delivery-status events |
| SMS fallback | MSG91 | India-native, DLT-compliant (activation pending) |
| OCR | **docTR** (deep-learning, PyTorch, local; GPU-accelerated when available) + **Tesseract** (orientation detection + digit-only cross-check) + OpenCV (grid/deskew) | Strong on scanned tables, fully local; docTR is markedly more accurate than Tesseract alone |
| PDF / image handling | pypdfium2 (render scans), pdfplumber (typed-PDF text), Pillow | Local rendering |
| Spreadsheet parsing | openpyxl (Excel), Python `csv` | Lightweight, exact — the preferred input when the bank can provide it |
| Validation | Pydantic | Type-safe per-row validation |
| Database | SQLite (single local file) | Zero-setup, on-premise, fast on SSD |
| Python ↔ Node bridge | Local HTTP, `127.0.0.1:3000` only | Python calls `wa_server.js` over loopback |
| LLM/Agent | **NONE** — templates only | DPDP-safe, no external API, 100% local, deterministic |

**No cloud. No foreign servers for customer data. No AI/LLM API.**

---

## 9. DPDP Act 2023 compliance — in full detail

**On the CSP's computer today (always true, right now):**
- All customer/bank data — name, mobile, account number, father's name,
  address, balance band — is stored and processed only on the CSP's PC.
- The uploaded document is deleted immediately after it's read, and a
  startup check removes anything accidentally left behind (a Windows
  file-lock retry + sweep — a real bug found and fixed during testing).
- The database is one local SQLite file, never uploaded anywhere.
- Customer **names and mobile numbers are masked on screen everywhere**
  (initials/dots), revealed only briefly on the CSP's own click, then
  automatically re-masked — both reveal/re-mask together as one group.
- OCR runs on-device (docTR + Tesseract) — no cloud OCR, no Google Vision,
  no Gemini, no OpenAI, no AI/LLM anywhere in reading the document or
  writing the message.
- Messaging is strictly one-way — WhatsApp/SMS receive **only** the phone
  number and the message text, nothing else; customer replies are never
  read or stored.
- No OTP, PIN, or password is ever sent in any message, in any context.
- Any future `is_sensitive`-flagged band is never auto-sent (§4).
- Servers bind to `127.0.0.1` (loopback) only — not reachable from the
  network.

**Today, because there is no server, no cloud, and no Admin Portal yet,
literally no data of any kind currently leaves the CSP's machine.**

**When the Eko Admin Portal is added** (see §13–14), the boundary that will
apply is enforced by an **allow-list, not a denylist**: only fields
explicitly on the approved list can ever be transmitted — anything not on
the list is physically incapable of leaving the CSP's machine, so no future
change or mistake can leak personal data.

**Decision (locked, deliberately maximal-safety):** even *masked* or
*ID-referenced* customer/business data is treated as carrying residual DPDP
risk — an interpretation could always argue some link back to a customer
exists (a case ID, a per-batch count, a login timestamp correlated with a
batch). So the northbound channel is cut down to the absolute minimum
needed for Eko to run the business, and nothing else:

*Will go to Eko, campaign/business data — and this is the ENTIRE list:*
1. A **unique campaign/batch ID** (an opaque token — not derived from and not
   reversible to any customer, account, or case data).
2. The **earnings/commission figure** for that campaign/batch ID.

That's it. No customer/batch counts, no delivery/read statistics, no audit
events, no session/login activity, no case IDs, no timestamps tied to any
customer action — none of that leaves the CSP's PC, not even in masked or
aggregated form. (Separately, and unrelated to any campaign or customer
data: the software's own install/update mechanism may register an
installation ID + version number with Eko purely to check for updates — this
is standard app-update telemetry about the *software*, not the business, and
carries no campaign or customer linkage at all.)

*Will NEVER go to Eko, under any flow:* names, mobile numbers, account
numbers, father's names, addresses, balance figures, message text, the
uploaded document, the database itself, delivery/visit statistics, audit
logs, or session activity. Even Eko's highest admin role will be able to
reset a CSP's login but never see that CSP's customer data or any activity
detail beyond the campaign ID → earnings figure.

---

## 10. Hardware & portability

The platform is being **built and tested on the developer's machine** (16 GB
RAM, AMD Ryzen 7, NVIDIA RTX 4060 — used because the team was directed to
build on the available hardware first), where the OCR runs on the **GPU**
with the more accurate recognition model (`parseq`).

The **actual deployment PC** (confirmed from its DxDiag) is a **Dell Inspiron
3268: 4 GB RAM, Intel Core i3-7100 (2 cores / 4 threads), Intel HD 630 (no
discrete GPU), 128 GB SSD, Windows 10 Pro 19045** — weaker than the earlier
"~8 GB" assumption, and already running an Aadhaar fingerprint RD service plus
a browser, so only ~1.5–2 GB is realistically free. The same build runs there
with **no code change**: `core/hardware.py` detects the machine at startup and
picks safe defaults automatically (the "hardware-aware auto-config" goal).

**Measured footprints (this stack, machine-independent):**

| Path | Resident RSS | Notes |
| --- | --- | --- |
| Base app (Flask + SQLite) | ~95 MB | always |
| **CSV / Excel / typed-PDF** (no OCR) | **~90 MB** | preferred bank input — torch never imported |
| **Scanned OCR, Tesseract-only mode** | **~90 MB** | 🟢 low-RAM path — `import torch` never happens |
| Scanned OCR, docTR mode | ~1.0–1.6 GB | deep-learning path, capable machines only |

**How `auto` mode decides** (all overridable in `config.py`):
- **RAM < `OCR_RAM_THRESHOLD_GB` (6 GB)** → scanned pages read by a
  **Tesseract-only** word reader that feeds the *same* accurate grid logic, so
  **PyTorch is never loaded** (~90 MB). This is where the real 4 GB deploy PC
  lands — it fits comfortably, no swap/OOM. Accuracy is held by the mandatory
  review gate + digit cross-check, and best of all by using the bank's
  Excel/CSV export (no OCR at all).
- **RAM ≥ 6 GB** → docTR; `parseq` on a GPU, the lighter/faster
  `crnn_vgg16_bn` on CPU. Torch threads are capped (`TORCH_MAX_THREADS`) so a
  2-core i3 isn't oversubscribed, and the model is released after each batch.

Baileys having no Chromium keeps WhatsApp at ~100 MB (vs the ~600 MB a headless
browser would have cost with the originally-considered `whatsapp-web.js`).

*Honest limit:* releasing the docTR model frees the reference and CUDA cache but
does not hand CPU RSS back to the OS mid-process; full reclamation for the
6–8 GB tier needs the planned short-lived OCR worker process. It doesn't affect
the 4 GB target, which never loads docTR.

---

## 11. Security

- Passwords stored as salted PBKDF2-HMAC-SHA256; change the default password.
- 5 failed logins → 5-minute lockout.
- Both servers bind to `127.0.0.1` only (not reachable from the network).
- The Flask session key is generated on first run and stored in
  `database/secret.key` (git-ignored — never commit it).
- Optional `WEBHOOK_TOKEN` in `config.py` to authenticate inbound webhooks.
- Audit log of logins, dispatch, settings, and case changes — view at `/audit`.

---

## 12. Current status — done, tested, and honestly pending

**Delivered and working (80 automated tests passing):**
- Full five-layer platform, campaign-independent architecture.
- "Inoperative Accounts" campaign live end-to-end.
- Excel/CSV intake (100% accurate) and scanned-document OCR
  (GPU-accelerated, review-gated, with side-by-side compare + zoom + undo/redo).
- Message engine (locked/non-editable); WhatsApp dispatch with
  pause/resume/stop; delivery tracking with the WhatsApp QR/reconnect bug
  fixed (auto-clears a stale session and regenerates a fresh QR).
- Manual review-and-approve workflow (edit, approve/undo, approve-all, send)
  — not-reachable cases correctly excluded from it.
- Two-level tracking, reports, upload-history management (with delete),
  audit log, on-screen name/mobile masking, Hindi/English toggle on every
  page, simplest-English wording throughout.

**Still pending, stated honestly:**
- A real phone scanning the QR and a live end-to-end WhatsApp send with
  delivery tracking on a production run — the reconnect bug is fixed and
  verified server-side (a fresh QR reliably regenerates), but a live scan is
  the final verification step.
- Hindi wording currently covers the main labels on every page, not
  literally every string yet (mechanism is fully built and working).
- SMS fallback: fully built in code; MSG91 account + DLT registration is a
  pending **operational** step.
- Entering the CSP's real name/phone/address in Settings (currently
  placeholder values).
- Obtaining the customer list as Excel/CSV from the bank, for 100% accuracy
  on large batches (OCR remains the fallback for paper-only situations).
- The productization pieces in §13–15 are designed but not built — blocked
  on data/decisions from the product owner.

---

## 13. Productization — becoming an Eko service

The MVP proved the workflow; the board decided to **productize it as an Eko
service**:
- **Eko owns the code/IP** and delivers it to CSPs as an installable,
  self-updating application.
- **Eko never holds customer/bank data — and not even masked/ID-referenced
  campaign activity data either** (decision, see §9): the only business data
  Eko ever receives is a campaign's unique ID plus its earnings figure.
- A **centralized Eko admin/control plane** manages software updates, help &
  support, credential recovery, and commission tracking over the internet,
  so Eko never has to physically visit a CSP.

**The two-plane architecture (the core design decision):**

```text
┌──────────────────────────── CSP PLANE (local PC) ─────────────────────────┐
│  The application described in §1–11: Dashboard · OCR · Message engine ·   │
│  WhatsApp/SMS dispatch · SQLite (ALL customer/account data) · local audit  │
│  log · sessions · earnings computed locally                               │
└───────────────▲─────────────────────────────────────────────────┬─────────┘
                │ SOUTHBOUND (Eko → CSP)          NORTHBOUND (CSP → Eko)     │
                │ • signed software updates       • unique campaign ID       │
                │ • admin-approved template change • earnings figure for it │
                │ • credential reset / OTP          (that is the ENTIRE      │
                │ • support responses                northbound data list)  │
                │            (install ID + version, for update-checking      │
                │             only, is separate and carries no campaign/     │
                │             customer link)                                │
                │        ── NEVER, not even masked/ID-referenced: name,     │
                │           mobile, account, address, balance, message      │
                │           text, counts, audit events, session activity ── │
┌───────────────┴─────────────────────────────────────────────────▼─────────┐
│                 EKO CONTROL PLANE (Eko domain/server)                     │
│  Update distribution · fleet/version registry (by install ID only) ·     │
│  help & support · credential recovery ·                                  │
│  commission & cashflow dashboard (campaign-ID + earnings only) ·         │
│  template management                                                     │
└────────────────────────────────────────────────────────────────────────────┘
```

The DPDP contract is enforced by an **allow-list, not a denylist** (§9) — and
that allow-list is now deliberately as small as it can possibly be (a
campaign ID and its earnings figure, nothing else), precisely because any
broader field — even one that looks harmless or is only ID-referenced —
could be argued under some interpretation to trace back to a customer. The
narrower the list, the fewer ways there are to ever be found in violation.

**Requirement classification from the first demo's feedback** (25 points
raised; A = ready to build, B = engineering judgment call, C = needs data
from the product owner — see §15):

| # | Requirement | Category | Status |
|---|---|---|---|
| 1 | Faster working: parallel OCR + hardware-aware auto-config | B / C | Partly done (GPU/CPU auto-detect); parallel-page OCU and full auto-installer pending Phase 3 |
| 2 | Eko owns code; data local; centralized self-update; in-app help | C | Designed (§14), blocked on hosting/roles |
| 3 | Real-vs-extracted side-by-side compare + zoom, all input types | A | **Done** |
| 4 | Page-range control shown for PDF only | A | **Done** |
| 5 | Campaign progress %; page-by-page PDF stitching; per-campaign commission | B / C | Progress % done; stitching + commission pending |
| 6 | Admin portal (commission, updates, support, reset, DPDP, telemetry, installer) | C | Designed (§14), blocked on data |
| 10 | WhatsApp send control works well | A | **Done** (responsive pause/resume/stop; QR bug fixed) |
| 11 | Communication history: correct IST time, separated from date, all events logged | A | **Done** |
| 12 | Message fixed/non-editable; template change via admin request | A / C | Lock done; admin-request flow pending |
| 13 | CSP per-campaign earning tracking, monitored by Eko | B / C | UI slot ready; blocked on commission formula |
| 14 | Bug: non-approved/escalated cases showed approved+sent | A | **Fixed** |
| 15 | Remove "verify" badge for B>10000 | — | **Resolved** — declassified (badge removed, no special gate) |
| 16, 19 | Mask names/mobiles, reveal briefly | A | **Done** |
| 17 | Undo (Ctrl+Z) / Redo (Ctrl+Y) | A | **Done** (review-screen edits) |
| 18 | Compact session/usage/money telemetry to Eko, no PII | C | Designed (§9, §14), blocked on sign-off |
| 20, 23 | Specific label/control fixes on two screenshots | B | Blocked on exact specifics |
| 21 | Filters work correctly | A | **Done** |
| 22 | Simplest-English rewrite + Hindi toggle | A / B | English done; Hindi mechanism done, wording partly covered |
| 24 | Admin portal architecture proposal | C | Proposed (§14), awaiting confirmation |

**Performance strategy for #1:** parallel page OCR (multiprocessing bounded
by cores − 2); hardware-aware auto-tuning (GPU→`parseq`, CPU→`crnn`, DPI,
worker count); streamed per-page progress so the CSP never feels stuck; the
CSV/Excel fast path (already built) remains the fastest, most accurate
option whenever the bank can provide a digital file.

---

## 14. Eko Admin Portal — roles & design (proposal)

**Hard rule:** the admin/Eko side never holds or sees bank-account or
customer personal data. It manages **software, access, money figures, and
support only.**

```text
   EKO ADMIN (control plane)                 CSP APP (local PC)
   ─ installs & updates software    ──────►  runs locally, all customer data here
   ─ manages CSP logins/reset       ──────►
   ─ sees campaign ID + earnings     ◄──────  sends ONLY {campaign ID, earnings figure}
     only — nothing else                     — no counts, no audit, no session data
   ─ help & support                 ◄──────► tickets in/out
        (NEVER receives customer/bank PII — not even masked or ID-referenced
         activity data; see §9 for why the line is drawn this tightly)
```

**Admin roles (responsibilities):**
- **R1 — Software installation & updates.** A per-CSP, signed installer tied
  to a licence key; hardware-aware (detects OS/CPU/GPU/RAM and installs the
  right package set automatically — no manual setup, no Eko visit); publishes
  version updates the CSP app self-checks and applies ("update available →
  update", like any desktop app); sees each install's version/hardware/last-seen.
  This is pure software-distribution telemetry (install ID + version), with
  no campaign or customer linkage at all.
- **R2 — CSP fleet & licensing.** Registry of every installation; activate/
  deactivate (protects Eko's code from misuse).
- **R3 — Access & credential management.** Create CSP logins; "forgot
  password" issues a new password or OTP to the CSP's registered mobile;
  admin can never see anything inside the CSP's customer database.
- **R4 — Commission & cashflow** (why Eko wants this). A simple rollup of
  **campaign ID → earnings figure**, per CSP — that is the entire dataset
  this view is built from (§9). No per-account breakdown, no reached/failed/
  visited counts, nothing beyond the campaign ID and its earnings number.
- **R5 — Help & support.** In-app tickets from CSPs; handles
  template-change requests (approves, then pushes the new template).
- **R6 — Templates & campaign config.** Centrally managed; pushed to CSP
  installs without a reinstall.
- ~~R7 — Telemetry & audit oversight~~ — **removed by decision.** An earlier
  draft of this design considered sending masked/ID-referenced session and
  audit activity to Eko for support/oversight purposes. That has been
  deliberately dropped: even ID-referenced activity data was judged to carry
  residual DPDP interpretation risk, so Eko gets no activity/audit visibility
  at all — only the campaign-ID → earnings figure (R4) and whatever a CSP
  chooses to put in a support ticket themselves (R5).

**Installation flow (admin-driven, hardware-aware):**
```text
1. Eko onboards a CSP  →  generates a licence key + installer link
2. CSP runs the installer on their PC
3. Installer probes hardware (OS/CPU/GPU/RAM)
      → installs the right packages automatically (fast, verified correct)
      → sets OCR model tier + performance settings for that machine
4. App registers with the Eko server (licence key, version, hardware profile)
5. CSP logs in and starts working — all customer data stays on that PC
6. Later: Eko publishes an update → CSP app shows "update available" → one-click
```
No Eko field visit needed for install, update, or most support.

**Proposed permission tiers (to confirm):**

| Role | Can do |
|---|---|
| Super Admin (Eko owner) | Everything: publish updates, manage licences, all views |
| Support Agent | Tickets, credential reset/OTP, view the fleet (install ID/version/last-seen only — no campaign or earnings data) |
| Finance / Ops | Campaign-ID → earnings views, reports (no code/licence control) |
| Auditor (read-only) | Read earnings summaries only (campaign ID → earnings) — there is no activity/audit data for anyone to read, since none flows to Eko (§14, R7) |

The northbound data boundary is the same allow-list described in §9/§13:
`{campaign unique ID, earnings figure}` for business data, and a separate
`{install ID, version}` for the software-update channel — nothing else, for
any role.

---

## 15. What's blocked — data/decisions needed from the product owner

These cannot be implemented correctly without the information below — no
guessing at business rules. Grouped so they can be answered together;
everything else in this repo proceeds independently.

| ID | Feature | Why it's blocked | Exact info needed | Suggested format |
|---|---|---|---|---|
| **EDR-1** | Commission / earnings engine | Cannot compute CSP earnings without the rule | How is commission calculated? Per resolved/closed account? Flat + variable? Per campaign? Per balance band? Caps/slabs? | Formula + 3 worked examples (input → payout) |
| **EDR-2** | Admin portal roles & hosting | Can't build the control plane without the permission model | Admin roles & permission matrix; which Eko domain hosts the admin portal; same host as the update server or separate? | Role→permission table; domain name(s) |
| **EDR-4** | Erase labels, keep function (specific screenshot) | Don't know which text to remove | Mark exactly which text/labels to erase (functionality stays) | Annotated screenshot |
| **EDR-5** | Fix specific controls (specific screenshot) | Don't know which controls fail / expected behaviour | Which controls don't work + what each should do | Screenshot + expected behaviour per control |
| **EDR-6** | Hindi language toggle | Need approved translations + scope | Full-dashboard Hindi, or key screens first? Who supplies/approves the Hindi strings? | Confirm scope; approve/edit the drafted wording |
| **EDR-7** | Undo/Redo scope | "Undo everything" is unsafe without bounds | Which actions must be undoable beyond review-screen edits (already done) — case edits? approvals? tracking transitions? (a sent message cannot be undone) | List of undoable actions, priority order |
| **EDR-8** | Template change workflow | Admin-mediated flow needs the process | Confirm templates are fixed at install; define how a CSP requests a change and how admin pushes the approved new template | Short workflow description |
| **EDR-9** | Credential recovery channel | OTP/reset needs a delivery channel | OTP via SMS (MSG91) or another channel? Who authorizes a reset — admin manually, or automated? | Channel + authorization rule |
| **EDR-10** | Eko policies / licensing | Need the actual policies to build against | Data-retention rules for the telemetry Eko keeps; licensing/anti-misuse terms; branding constraints (the document Abhishek Sir referred to) | Policy doc or bullet list |
| **EDR-11** | ~~Telemetry scope confirmation~~ — **RESOLVED, not blocked** | — | Decided: the northbound allow-list is locked to exactly `{campaign unique ID, earnings figure}` — nothing else, not even masked/ID-referenced counts, audit events, or session activity (§9/§13/§14). This is a deliberate maximal-safety-margin choice, not something awaiting sign-off. If Eko later wants more visibility, that would be a conscious trade-off to raise separately, not a default to assume. | — |

**For EDR-1 specifically:** whatever the commission formula turns out to be,
only the resulting campaign ID + earnings figure ever reaches Eko — the
formula's inputs (per-account outcomes, balance bands, etc.) are computed
and stay entirely on the CSP's PC.

---

## 16. Roadmap

**Phase 0 — CSP-app fixes & polish (no external data needed).** Mostly
complete — see §12 for what's done vs. still in progress here.

**Phase 1 — Local earnings foundation.** A local earnings ledger per
campaign/case, computed and kept entirely on the CSP's PC (needs the
commission formula, EDR-1, for real numbers; the structure is ready now); a
northbound payload builder that emits **only** `{campaign unique ID,
earnings figure}` per campaign — nothing else, by construction — built and
unit-tested to prove that, but not yet transmitted anywhere (no endpoint
exists until Phase 2).

**Phase 2 — Eko Control Plane (Admin Portal).** Fleet/version registry,
signed update distribution, help & support ticketing, credential recovery,
the commission & cashflow dashboard, template management, licensing/
anti-misuse. Blocked on EDR-1, EDR-2, EDR-9, EDR-10.

**Phase 3 — Hardware-aware installer & auto-update client.** A setup
bootstrap that probes OS/CPU/GPU/RAM and installs the correct package set,
picks the OCR model tier/worker count/DPI, verified correct before first
run; the in-app "update available → update" flow.

**Phase 4 — Page-by-page campaign stitching for large PDFs.** A physical PDF
gets a content fingerprint (file hash); each page-range batch of that PDF
links to the same document group; the dashboard aggregates progress +
earnings across all page-batches so the CSP sees ONE result for e.g. a
29-page PDF, not 29 separate dashboards. Per-campaign commission rolls up to
the group.

**Cross-cutting — Hindi.** All UI strings externalized; English (simplest
wording) + Hindi. Extracted customer data is never translated — only fixed
UI labels are.

**Also on the original report's roadmap (core platform, not productization):**
1. Activate SMS fallback — complete MSG91 + DLT (path already built).
2. Onboard a second campaign purely as configuration, proving zero-core-change
   extensibility.
3. Next campaigns — KYC Pending, Aadhaar Linking, as configuration folders.
4. Pilot metrics — reach, delivery, and visit-conversion on a live batch.

**Future scope, hardware-gated:** no AI is used today, by design (cloud AI
would break DPDP; a lightweight CSP PC can't run a live model beside the
batch). A future option is a **local, self-hosted, open-source oversight
assistant** that summarises each batch and flags anomalies for the
CSP/manager — fully on-premise, so DPDP still holds — adopted only when
hardware permits (first offline/after-hours, later live on upgraded hardware).

---

## 17. Setup, daily use, and testing

### Requirements

| Component | Needed for |
|---|---|
| Python 3.11+ | Dashboard + processing |
| Node.js 18+ | WhatsApp server (Baileys — no browser/Chromium needed) |
| Tesseract OCR | Orientation detection + numeric cross-check on scanned documents (optional if only Excel/CSV) |
| PyTorch (CPU or CUDA) | docTR OCR engine for scanned PDFs/images (optional if only Excel/CSV) |

Install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki and
ensure `tesseract.exe` is on PATH. See `requirements.txt` for the exact
docTR/PyTorch install line for GPU (dev machine) vs CPU (deployment CSP PC) —
the same code runs on both; only the install command differs.

### First-time setup

1. **Configure CSP details** — edit `config.py`:
   `CSP_NAME`, `CSP_PHONE`, `CSP_ADDRESS` (appear in every message);
   `LOGIN_ID`, `LOGIN_PASSWORD` (change from defaults); `MSG91_*` keys (only
   for SMS fallback); `OCR_ENGINE` / `DOCTR_RECO_ARCH` (accuracy/speed
   tradeoff, see the comments in `config.py`).
   > After first run you can also edit CSP details from **Settings** in the
   > dashboard — those are stored in the database and take precedence.
2. **Install dependencies** (the launchers do this automatically on first run).
   The CSP application lives in `csp_dashboard/`, so run these from there:
   ```
   cd csp_dashboard
   pip install -r requirements.txt
   cd whatsapp && npm install && cd ..
   ```
3. **Start everything:** `csp_dashboard/run.bat` — opens two windows:
   - **WhatsApp Server** — first run shows a QR code; scan it from your
     phone (WhatsApp → Linked Devices → Link a Device). If the QR ever fails
     to appear (e.g. after a logout), use **Reset & New QR** in Settings.
   - **Dashboard** — opens http://127.0.0.1:5000.

### Daily use

1. Run `csp_dashboard/run.bat` (or `csp_dashboard/start_dashboard.bat` if WhatsApp is already linked).
2. Log in.
3. Pick **Inoperative Accounts** → upload the bank document (page-range
   option appears only for a PDF).
4. Review the extracted rows next to the source (zoom, Ctrl+Z/Ctrl+Y), fix
   anything wrong, then **Confirm & Create Cases**.
5. Click **Start Messaging** → **Automate** (send the whole batch) or **Open
   Review Sheet** (approve/undo individually or in bulk, then **Send
   Approved Now**).
6. Watch live progress; Pause/Stop anytime.
7. As customers visit, update each case: **Visited → Done → Close**.
8. Download reports from the **Reports** tab.

### Run the tests

```
pip install pytest
python -m pytest
```

80 tests cover extraction/OCR column-splitting, classification, validation,
templates (incl. DPDP content rules), the approval workflow (automatic and
manual, including undo), the processing pipeline, the tracking state
machine, webhooks, auth, and settings.

---

## 18. Project layout

The repository is organised into three top-level trees — the CSP application
(`csp_dashboard/`), the Eko admin portal (`admin_dashboard/`), and the docs
(`documentation/`) — with only `README.md` and `CLAUDE.md` kept at the root.

```
code/
├── README.md                    ← This file (single source of truth)
├── CLAUDE.md                    ← Auto-loaded technical project context
│
├── documentation/               ← All other .md docs + internal .docx + DxDiag
│   ├── ADMIN_PORTAL_ARCHITECTURE.md
│   ├── WHATSAPP_TEMPLATES_FOR_META.md
│   ├── PENDING.md
│   ├── *.docx                   ← WhatsApp Business / API / ban-risk process docs
│   └── DxDiag.txt               ← Confirmed deploy-PC hardware dump
│
├── csp_dashboard/               ← THE CSP APPLICATION (runs on the CSP PC)
│   ├── app.py                   ← Flask entry point, session hardening, IST time filters
│   ├── config.py                ← CSP details, credentials, limits, OCR engine settings
│   ├── VERSION · requirements.txt · requirements-lite.txt · deploy_check.py
│   ├── .env / .env.example      ← Local config (loaded by config.py by absolute path)
│   ├── run.bat                  ← Full launcher (WhatsApp server + dashboard)
│   ├── start_dashboard.bat · start_whatsapp.bat
│   ├── INSTALL.bat · CSP_Setup.bat · MAKE_ZIP.ps1   ← Installer + packaging
│   │
│   ├── campaigns/inoperative/
│   │   ├── config.json          ← Campaign bands + template IDs + is_sensitive
│   │   ├── classifier.py        ← Balance band → template/tone/sensitivity mapping
│   │   └── templates.py         ← WA templates (template_1, template_3) + SMS template
│   │
│   ├── core/
│   │   ├── extraction.py        ← Upload → review draft → CSP confirm → case creation
│   │   ├── ocr_table.py         ← docTR/Tesseract table OCR (grid detection, column split)
│   │   ├── ocr.py               ← Tesseract path/setup helper
│   │   ├── parser.py / parser_ocr_helper.py / column_mapper.py
│   │   ├── validator.py         ← Pydantic row validation
│   │   ├── message_engine.py    ← Fills templates; never auto-queues for sending
│   │   ├── approval.py          ← Automatic batch send + manual review/approve/undo
│   │   ├── dispatcher.py        ← WhatsApp + SMS sender logic
│   │   ├── comm_runner.py       ← Batch dispatch loop (pause/resume/stop)
│   │   ├── webhooks.py          ← Delivery-status event handling
│   │   ├── tracking.py          ← Business-status state machine
│   │   ├── settings.py / auth.py / crypto.py / hardware.py / updater.py
│   │   └── processor.py         ← Direct (non-review-gated) commit path — used by tests
│   │
│   ├── whatsapp/
│   │   ├── wa_server.js         ← Baileys HTTP server (no Chromium)
│   │   └── package.json
│   │
│   ├── database/
│   │   ├── db.py / schema.sql / queries.py / seed.py
│   │   └── csp_platform.db, secret.key
│   │
│   ├── dashboard/
│   │   ├── routes.py / webhook_routes.py
│   │   ├── templates/           ← login, welcome, documents, review, campaign,
│   │   │                           case_detail, approve_sheet, audit
│   │   └── static/
│   │       ├── css/ops.css
│   │       ├── js/mask.js        ← Name/mobile masking (click to reveal briefly)
│   │       ├── js/i18n.js        ← Hindi/English toggle
│   │       └── vendor/           ← Bootstrap, vendored locally
│   │
│   ├── installer/               ← App icon + build helpers
│   ├── scripts/                 ← OCR-engine benchmark, dummy-data generator
│   ├── tests/                   ← pytest suite (all passing)
│   ├── data/                    ← Synthetic/dummy test data
│   └── uploads/drafts/          ← Temp storage; deleted right after processing
│
└── admin_dashboard/             ← EKO ADMIN PORTAL (runs on Eko's server, NOT the CSP PC)
    ├── app.py                   ← Flask entry; reuses csp_dashboard's config + core.auth
    ├── api.py / routes.py / db.py / schema.sql
    ├── templates/ · releases/
    └── admin.db, secret.key
```

> **Note on the split:** `admin_dashboard/` deliberately reuses the CSP app's
> `config` and `core.auth` (one source of truth for settings + password
> hashing). `admin_dashboard/app.py` adds both the repo root and
> `csp_dashboard/` to `sys.path` at startup, so run it from the repo root:
> `python admin_dashboard/app.py`.

---

## 19. Cost

| Component | Cost |
|---|---|
| Baileys (WhatsApp) | ₹0 |
| docTR + Tesseract OCR | ₹0 |
| Templates (no LLM) | ₹0 |
| SQLite / Flask | ₹0 |
| MSG91 SMS | ₹300–500/month |
| **Total** | **₹300–500/month** (only once SMS fallback is activated) |

---

## 20. Design principles — never violate

1. **Campaign independent** — core system never changes for new campaigns.
   Only campaign config changes.
2. **Case oriented** — each customer = one case record in the database.
   Independent. Atomic.
3. **N stays N** — total cases = confirmed rows from the reviewed upload.
   Never more, never less.
4. **On-premise** — all customer data processing on the CSP PC. No cloud.
5. **No LLM** — pure template engine. Deterministic. Safe. Fast. Free.
6. **Graceful fallback** — WhatsApp fail → SMS. SMS fail → escalate to CSP.
7. **Two-level tracking** — comm tracking automatic, business tracking manual.
8. **One-way communication** — system sends only, never receives/processes replies.
9. **DPDP safe** — minimum data in messages, on-screen masking, no financial
   details, local storage only, allow-list boundary for any future telemetry.
10. **CSP name only** — Eko Bharat Ventures never appears anywhere.

---

## 21. Scope boundaries — what not to build

- No LLM integration (considered, rejected for DPDP + cost reasons).
- No cloud hosting for customer data (on-premise mandate).
- No Google Vision OCR / Gemini API (cloud — rejected).
- No reply handling, no auto-reply (no-reply policy).
- No KYC / Aadhaar-linking campaign yet (future, as configuration).
- No multi-CSP support in the core dashboard itself (single CSP PC) —
  multi-CSP fleet management is addressed separately by the Eko Admin
  Portal (§14), which never touches customer data.
- No mobile app (browser dashboard only).
- No email channel (not authorized).

---

*On-premise · No cloud · Deterministic · CSP name only.*
