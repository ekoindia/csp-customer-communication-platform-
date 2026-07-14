# CSP Account Issue Communication Platform
# CLAUDE.md — Full Project Context
# Read this completely before writing any code.

> **Note on this document:** this describes the core CSP platform as it is
> actually built and running today (verified against the code, not the
> original plan). Where the original design changed during implementation
> (e.g. the storage mechanism, the WhatsApp engine, the OCR engine, the page
> flow), this file has been corrected to match reality. For the newer
> productization layer being added on top (Eko Admin Portal, commission
> tracking, centralized updates) — which does NOT change anything in this
> file — see `instructions.md`, `PRODUCTIZATION_ROADMAP.md`, and
> `ADMIN_PORTAL_DESIGN.md`.

---

## 1. What is this project

This is an **on-premise, campaign-driven communication platform** for a CSP (Customer Service Point) operator who is partnered with Eko Bharat Ventures and SBI Bank.

The CSP receives bank documents containing lists of customers with account issues. The system automates customer communication on behalf of the CSP.

**The CSP's name goes on all messages. Eko Bharat Ventures name never appears anywhere — not in messages, not in the UI, not in logs.**

---

## 2. The problem being solved

Currently the CSP manually:
- Reads the bank document
- Calls each customer one by one
- Explains the account issue
- Tells them to visit the branch

This is slow, inconsistent, and untracked.

This system automates the communication while keeping the CSP in control.

---

## 3. First campaign — Inoperative accounts

This is the ONLY campaign built and live right now.

Future campaigns (KYC Pending, Aadhaar Linking) will use the same platform but are NOT built yet — the platform is designed so a new campaign only needs a new config folder (see §25), no changes to the core engine.

**What is an inoperative account:**
An SBI bank account where no customer-initiated transaction has happened for a long period. The bank sends a list of such accounts to the CSP to follow up.

**What the bank has authorized:**
- CSP can send a message to customers saying "your account has an issue, please visit the CSP"
- Message must be in CSP's name only
- No financial details in the message
- No account details in the message
- Generic message only

---

## 4. Architecture — 5 layers

### Layer 1: CSP Dashboard (Flask frontend)
- 4-page flow: Login → Welcome (campaign select only) → Documents (upload + upload history, per campaign) → Campaign Dashboard
- Campaign independent UI
- No CSP name hardcoded anywhere in code — comes from config/settings
- Hindi/English language toggle on every page (see §16)

### Layer 2: Document Processing
- Accept any format: Excel (.xlsx, .csv), typed PDF, scanned PDF, image
- Auto-detect format
- Excel/CSV parsed directly — **100% accurate, no OCR at all**
- Scanned PDF/image → OCR (docTR — deep-learning, local; GPU-accelerated when
  available, falls back to CPU — plus Tesseract for page-orientation detection
  and a digit-only cross-check on account/mobile numbers). Fully local, no
  cloud OCR.
- Extract customer records
- **Review gate**: every extracted row is shown to the CSP next to the source
  (with zoom in/out, and Ctrl+Z/Ctrl+Y undo/redo for corrections) — nothing is
  created as a real case until the CSP reviews and confirms
- Validate each row (Pydantic)
- Classify by balance band
- Create one case record per confirmed customer row (see §5 — stored directly
  in SQLite, not as a JSON file)

### Layer 3: Message Creation
- Template engine only — NO LLM, NO AI API
- Pure Python string replacement
- Select template based on balance band
- The generated message is stored against the case and is **locked
  (non-editable)** on every screen — a CSP-requested wording change is routed
  through Eko, not edited directly (see §9, §15)

### Layer 4: Communication
- Priority 1: WhatsApp, via a local Baileys-based HTTP server (`whatsapp/wa_server.js`) — Baileys talks to WhatsApp directly over the multi-device WebSocket protocol; **no browser/Chromium is used**
- Priority 2: SMS fallback (MSG91) — only if WhatsApp fails (path built; MSG91/DLT activation is a pending operational step, not a code gap)
- No reply policy — one-way notification only
- No chatbot, no conversational AI
- Delivery status tracked via Baileys message-status events and MSG91 webhooks
- The CSP either sends the whole batch automatically, or opens a manual review-and-approve sheet to approve customers one by one or in bulk (approvals can be undone before sending) — see §16, §19

### Layer 5: Tracking & Case Management
- TWO independent tracking systems:
  1. Communication tracking (automatic — API/webhook updates)
  2. Business case tracking (manual — CSP clicks on dashboard)

---

## 5. Data flow — how a case is actually stored

**There is no JSON-file-based case storage.** (An earlier design considered a
2-stage "Initial JSON / Final JSON" file scheme; it was superseded during
implementation by storing everything directly in the local SQLite database —
this is simpler, atomic, and query-able, while keeping the same guarantee: one
customer = one case record, and the count never inflates.)

The real flow:

1. **Upload** → the file is parsed (Excel/CSV) or OCR'd (scanned PDF/image)
   into a temporary, in-memory/on-disk **review draft** (not a database case
   yet). Page images (for a scan) are saved alongside the draft so the CSP can
   compare extracted data against the source.
2. **Review** → the CSP sees every draft row next to the source, edits any
   wrong value, and only then clicks **Confirm & Create Cases**.
3. **Case created** → one row is inserted into the `customer_cases` table per
   confirmed row (case_id, batch_id, campaign_id, account/name/mobile/etc.,
   classification fields). The draft is discarded — DPDP hygiene, nothing
   extra is kept on disk once the case exists in the database.
4. **Message generated** → a row is inserted into the `messages` table (linked
   by case_id) containing the filled WhatsApp + SMS text for that case.
5. **Approval** → a case becomes eligible to send only once the CSP explicitly
   approves it (automatically as part of a batch send, or individually/bulk on
   the review sheet) — approval creates a `pending` row in
   `communication_attempts`.
6. **Send + track** → `communication_attempts` rows are updated in place as
   delivery status changes (sent → delivered → read/failed); `business_tracking`
   rows are updated as the CSP marks their own progress (visited → completed →
   closed). Nothing is ever duplicated into a second file/row per status
   change — the same row is updated.

So "the case" is really a join across four tables (`customer_cases`,
`messages`, `communication_attempts`, `business_tracking`), all keyed by the
same `case_id` — not two JSON file stages. The **logical shape** of a case
(customer fields, classification, csp info, communication, tracking) is shown
below for reference; it maps directly onto those table columns.

---

## 6. Case data — logical shape (as stored in SQLite, not as a file)

> `name`, `mobile`, `account_number`, `father_name`, `address` are stored
> **encrypted at rest** (§15 rule 13) — the JSON shape below is the *logical*
> view the app works with (after transparent decryption in
> `database/queries.py`), not the literal on-disk bytes.

Customer + classification fields (from `customer_cases`):
```json
{
  "case_id": "CASE_001",
  "campaign": "inoperative_accounts",
  "batch_id": "BATCH_2026_06_25",
  "customer": {
    "name": "RAMESH KUMAR",
    "mobile": "98XXXXXXXX",
    "account_number": "34XXXXXXXX",
    "father_name": "RAJU KUMAR",
    "balance_band": "100<1000",
    "village": "Ahiraule",
    "taluka": "Tamkuhi Raj",
    "address": "VILL-AHIRAULI DIST-KUSHINAGAR 274302"
  },
  "classification": {
    "band_label": "100<1000",
    "tone": "normal",
    "template_id": "template_1",
    "is_sensitive": false
  }
}
```

---

## 7. Case data — communication + tracking (added once generated/sent)

Message fields (from `messages`) + delivery fields (from
`communication_attempts`) + business fields (from `business_tracking`), all
linked by the same `case_id`:
```json
{
  "csp": {
    "name": "Dudahi CSP",
    "phone": "98XXXXXXXX",
    "address": "Dudahi, Tamkuhi Raj, Kushinagar"
  },
  "communication": {
    "wa_message": "Namaste Ramesh ji, aapke SBI bank account mein kaafi samay se koi len-den nahi hua hai jiske karan account band pada hai. Ise dobara chalu karwane ke liye kripya humse sampark karein.\n\nDudahi CSP\nDudahi, Tamkuhi Raj\n98XXXXXXXX\n\n- Dudahi CSP",
    "sms_message": "Namaste Ramesh ji, aapka SBI account band pada hai. Sampark karein: Dudahi CSP 98XXXXXXXX",
    "template_id": "template_1",
    "channel": "whatsapp",
    "status": "wa_delivered",
    "sent_at": "2026-06-25T08:05:00"
  },
  "tracking": {
    "business_status": "pending",
    "is_escalated": false,
    "created_at": "2026-06-25T08:00:00",
    "message_sent_at": "2026-06-25T08:05:00",
    "visited_at": null,
    "closed_at": null
  }
}
```
On screen, the customer's name and mobile number are **masked by default**
(shown as initials/dots) and only revealed briefly on the CSP's own click —
see §15.

---

## 8. Balance band classification — Inoperative accounts campaign

| Band | Meaning | Tone | Template | Sensitive? |
|------|---------|------|----------|-----------|
| 0.1 < 100 | Near zero — account never used or drained | Normal | template_1 | No |
| 100 < 1000 | Low balance — account went dormant | Normal | template_1 | No |
| 1000 < 10000 | Real money sitting idle — urgent | Urgent | template_3 | No |
| B > 10000 | Large idle balance — urgent | Urgent | template_3 | No |

**On the `is_sensitive` flag:** the classification engine supports flagging a
band as sensitive (e.g. "possible deceased account holder — CSP must verify
before sending"); this was used for the top band during early development, but
is currently **off for every band** — the extraction review gate (§5, step 2)
already gives the CSP two independent checks (the review screen, and the
per-case detail page) before anything sends, so no extra "verify first" flag is
shown today. The mechanism itself remains in the code (`campaigns/inoperative/
config.json` → `is_sensitive`) in case a future band needs it.

---

## 9. Message templates — Inoperative accounts campaign

**All messages are in CSP name only. No Eko Bharat. No bank name mentioned explicitly.**

There are exactly **two** WhatsApp templates (normal and urgent) plus one SMS
template, shared across the four bands as shown in §8.

### Template 1 — WhatsApp (normal: 0.1<100 and 100<1000)
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

### Template 3 — WhatsApp (urgent: 1000<10000 and B>10000)
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

### SMS template — all bands (160 chars max, Hinglish only)
```
Namaste {name} ji, aapka SBI account band pada 
hai. Sampark karein: {csp_name} {csp_phone}
```

**Template variables:**
- `{name}` — from customer data (parsed/extracted, CSP-reviewed)
- `{csp_name}` — from config.py / Settings (set once by CSP)
- `{branch_address}` — from config.py / Settings
- `{csp_phone}` — from config.py / Settings

**The message is locked** — it cannot be edited on any screen. A wording
change request goes through Eko (Help & Support), not a direct edit — see §15.

---

## 10. Communication policy — STRICT

- One-way notification ONLY
- No reply processing
- No chatbot
- No conversational AI
- No auto-reply
- Customer replies are completely ignored — system does not even listen
- Webhooks are ONLY for delivery status: sent / delivered / read / failed
- No OTP, PIN, password ever
- No financial details in any message
- No account balance in any message
- No account number in any message

---

## 11. Two-level tracking

### Communication tracking (automatic)
Updated by webhook / message-status events. CSP does nothing.

| Status | Meaning |
|--------|---------|
| pending | Queued for sending (CSP has approved it) |
| wa_attempted | Sent to the WhatsApp bridge (Baileys) |
| wa_delivered | Double grey ticks |
| wa_read | Blue ticks (never arrives if the recipient has read-receipts off — treated as delivered, not an error) |
| wa_failed | Could not deliver → trigger SMS |
| sms_sent | SMS dispatched via MSG91 |
| sms_delivered | MSG91 confirmed |
| sms_failed | Both failed → escalate |
| escalated | CSP must manually visit |

### Business case tracking (manual by CSP)
CSP clicks buttons on dashboard.

| Status | Meaning |
|--------|---------|
| pending | Message not sent yet |
| customer_not_visited | Message sent, customer hasn't come |
| customer_visited_in_progress | Customer came, process started (reachable directly from `pending` too — a CSP can mark a visit even before/without a message having been sent, e.g. the customer walked in on their own) |
| process_completed | Account reactivated |
| case_closed | Final status, included in reports |

---

## 12. Tech stack — as built

| Component | Tool | Reason |
|-----------|------|--------|
| Language | Python 3.11 | Entire backend in one language |
| WhatsApp | Baileys (`@whiskeysockets/baileys`, Node.js) | Free, no browser/Chromium (direct WebSocket protocol), real delivery-status events |
| SMS | MSG91 | India-native, DLT compliant, paid (₹300-500/month) |
| OCR | docTR (deep-learning, local; GPU-accelerated when available) + Tesseract (orientation detection + digit cross-check) | On-premise, no cloud, data stays on PC; docTR is markedly more accurate on scanned tables than Tesseract alone |
| LLM/Agent | NONE — templates only | DPDP safe, no external API, 100% local |
| Excel/CSV parser | openpyxl / Python `csv` | Lightweight, 100% accurate — the preferred input when the bank can provide it |
| PDF parser | pdfplumber (typed PDF text) / pypdfium2 (render scanned pages for OCR) | Table extraction / page rendering |
| Database | SQLite | Single file, local SSD, zero setup |
| Dashboard | Flask + Jinja2 + Bootstrap (vendored locally, no CDN) | Pure Python, no frontend framework |
| Validation | Pydantic | Type-safe row validation |
| PII-at-rest encryption | `cryptography` (Fernet) | Encrypts customer_cases identifying fields locally — see §15 rule 13 |
| Bridge | Python → Node.js HTTP (127.0.0.1 only) | Python calls `wa_server.js` via local HTTP |

**No cloud. No foreign servers for customer data. No AI APIs.**

---

## 13. Hardware — target deployment CSP PC

This section describes the **eventual CSP deployment PC** — a separate,
weaker machine than the one this software is currently being built and tested
on (a development machine with 16 GB RAM and a discrete GPU, used because it
was directed to build on the available hardware first). The code is
device-agnostic: the OCR engine auto-detects and uses a GPU if present, and
falls back to CPU otherwise, with no code change needed between the two
machines (see `config.py` → `DOCTR_RECO_ARCH`).

The **confirmed** deploy PC (from its DxDiag) is a **Dell Inspiron 3268**, and
it is weaker than the "8 GB" originally assumed here:

| Spec | Value | Status |
|------|-------|--------|
| OS | Windows 10 Pro 19045 | ✓ Supported |
| RAM | **4 GB** (i3-7100, 2c/4t, Intel HD 630, no GPU) | ✓ Handled by `core/hardware.py` |
| Storage | 128 GB SSD (~52 GB free) | ✓ Enough |
| Operating hours | 8AM - 8PM | ✓ Batch completes within hours |
| Internet | Broadband | ✓ For WhatsApp + MSG91 |

**RAM strategy on the real 4 GB PC** (auto-selected at startup — see §12 of
`README.md`; only ~1.5–2 GB is realistically free, the box already runs an
Aadhaar fingerprint RD service + a browser):
- Windows + other apps: ~2–2.5 GB
- WhatsApp bridge (Baileys, no browser): ~100 MB
- Python + Flask: ~150 MB
- **Scanned OCR runs Tesseract-only (~90 MB, PyTorch never loaded)** because
  4 GB < `OCR_RAM_THRESHOLD_GB`; docTR's ~1 GB is skipped entirely on this box.
- CSV/Excel (preferred bank input) also ~90 MB, no OCR at all.
- So the whole app stays around ~350–450 MB — fits without swapping.

On a capable ≥6 GB machine, `auto` mode instead loads docTR (`parseq` on a GPU,
`crnn_vgg16_bn` on CPU) for higher raw accuracy. Same build, no code change.

---

## 14. Folder structure — as built

```
code/
│
├── CLAUDE.md                    ← This file (core platform reference)
├── instructions.md              ← Productization spec (Eko admin portal, etc.)
├── PRODUCTIZATION_ROADMAP.md    ← Phased plan for the productization work
├── ADMIN_PORTAL_DESIGN.md       ← Eko admin portal proposal
├── EXTERNAL_DATA_REGISTER.md    ← Decisions/data still needed from the product owner
├── PROJECT_REPORT.md            ← Architecture writeup
├── app.py                       ← Flask entry point, session hardening, IST time filters
├── config.py                    ← CSP details, credentials, limits, OCR engine settings
├── requirements.txt             ← Python deps
│
├── campaigns/
│   └── inoperative/
│       ├── config.json          ← Campaign bands + template IDs + is_sensitive
│       ├── classifier.py        ← Balance band → template/tone/sensitivity mapping
│       └── templates.py         ← WA templates (template_1, template_3) + SMS template
│
├── core/
│   ├── crypto.py                 ← PII-at-rest encryption (Fernet) + account blind index
│   ├── extraction.py            ← Upload → review draft → CSP confirm → case creation
│   ├── ocr_table.py             ← docTR/Tesseract table OCR (grid detection, column split)
│   ├── ocr.py                   ← Tesseract path/setup helper
│   ├── parser.py                ← Excel/CSV/PDF/image format dispatch
│   ├── parser_ocr_helper.py     ← OCR-path helpers for parser.py
│   ├── column_mapper.py         ← Maps varying bank-file headers to canonical fields
│   ├── validator.py             ← Pydantic models for row validation
│   ├── message_engine.py        ← Fills templates; never auto-queues for sending
│   ├── approval.py              ← Automatic batch send + manual review/approve/undo
│   ├── dispatcher.py            ← WhatsApp + SMS sender logic
│   ├── comm_runner.py           ← Batch dispatch loop (pause/resume/stop)
│   ├── webhooks.py              ← Delivery-status event handling (Baileys + legacy map)
│   ├── tracking.py              ← Business-status state machine
│   ├── settings.py              ← CSP settings (name/phone/address) stored in DB
│   ├── auth.py                  ← Login, password hashing, lockout
│   └── processor.py             ← Direct (non-review-gated) commit path — used by tests
│
├── whatsapp/
│   ├── wa_server.js             ← Baileys HTTP server (no Chromium)
│   └── package.json
│
├── database/
│   ├── db.py                    ← SQLite connection + schema setup
│   ├── schema.sql               ← Table definitions (customer_cases, messages,
│   │                               communication_attempts, business_tracking, users, ...)
│   ├── queries.py                ← All SQL access
│   └── seed.py                  ← Reference data (branches, campaigns, templates)
│
├── dashboard/
│   ├── routes.py                ← Flask routes
│   ├── webhook_routes.py        ← Inbound delivery-status webhook
│   ├── templates/
│   │   ├── login.html
│   │   ├── welcome.html         ← Campaign selection only
│   │   ├── documents.html       ← Upload + upload history (per campaign)
│   │   ├── review.html          ← Extraction review/compare/zoom/undo-redo screen
│   │   ├── campaign.html        ← Main dashboard (Overview/Cases/Reports/Settings)
│   │   ├── case_detail.html     ← Full record for one case (view/edit/approve)
│   │   ├── approve_sheet.html   ← Manual review-and-approve list
│   │   └── audit.html           ← Local audit log
│   └── static/
│       ├── css/ops.css
│       ├── js/mask.js           ← Name/mobile masking (click to reveal briefly)
│       ├── js/i18n.js           ← Hindi/English toggle
│       └── vendor/              ← Bootstrap, vendored locally (no CDN)
│
├── scripts/                     ← OCR-engine benchmark, dummy-data generator
├── tests/                       ← pytest suite (80 tests)
├── data/                        ← Synthetic/dummy test data
└── uploads/                     ← Temp storage; source files deleted right after
    └── drafts/                    processing; a startup check clears anything left behind
```

---

## 15. DPDP Act compliance rules — NEVER BREAK THESE

1. **No financial data in messages** — no balance, no account number, no band mentioned
2. **No customer name in SMS if possible** — use generic "Dear customer" for SMS
3. **No cloud APIs** — docTR and Tesseract run locally; no Google Vision, no Gemini, no OpenAI
4. **No foreign servers** — all customer data stays on CSP local PC
5. **MSG91 only gets** — phone number + generic message text. Nothing else.
6. **WhatsApp gets** — phone number + message text. CEO authorized this.
7. **SQLite only** — local file on SSD. Never upload database anywhere.
8. **Uploaded files** — deleted from `uploads/` right after processing, and a
   startup check removes anything accidentally left behind. Not kept.
9. **Any future sensitive-flagged band** — never auto-send. CSP must manually
   verify first (mechanism exists; no band is flagged sensitive by default
   today — see §8).
10. **No OTP, PIN, password** — ever, in any message, in any context.
11. **On-screen masking** — customer names and mobile numbers are masked by
    default everywhere in the dashboard; a CSP click reveals them briefly, then
    they re-mask automatically.
12. **Message is locked** — never freely editable, so no risk of an
    accidental/non-compliant message being typed.
13. **PII encrypted at rest, purged on case closure** — added because an RBI
    inspector can physically visit a CSP and examine the machine, so on-screen
    masking (rule 11) is not enough on its own; the raw SQLite file must also
    not be human-readable. `customer_cases.name/mobile/account_number/
    father_name/address` are stored **encrypted** (`core/crypto.py`, Fernet,
    key at `database/pii.key` — generated locally, never committed) and
    decrypted transparently wherever the app reads them (dashboard, message
    engine, dispatcher) — no other module changes. Account-number dedup uses a
    separate one-way HMAC blind index (`account_number_hash`) since encryption
    is non-deterministic and can't be matched with SQL `=`; the hash survives
    purge so "one account = one case, ever" still holds. Once a case reaches
    the terminal `case_closed` business status, its identifying fields are
    **irreversibly nulled** (`database.queries.purge_case_pii`, wired from both
    `core/tracking.py`'s transition and the sensitive-skip route) — only
    `case_id`, band/village/taluka, and communication/tracking history remain,
    which is all a report needs. village/taluka stay unencrypted throughout
    (not individually identifying, needed for category reporting).

---

## 16. Frontend — 4-page flow

### Page 1: Login
- Fields: CSP ID + Password
- No campaign info shown before login
- On success: redirect to Page 2
- Hindi/English toggle available here too

### Page 2: Welcome — campaign selection only
- Welcome with CSP name (from config, not hardcoded)
- Campaign cards (Inoperative = active, others = coming soon)
- No upload here — that is Page 3

### Page 3: Documents — upload + history (per campaign)
- File upload zone (drag-drop or click) — Excel/CSV/PDF/scanned PDF/image
- Page-range input — shown **only when a PDF is selected**, hidden for
  CSV/Excel/image (those never have a "page range")
- Upload history table with Open Dashboard / Delete per batch

### Review screen (after upload, before Page 4)
- Extracted rows shown next to the source (scanned page, or a read-only table
  for CSV/Excel), both panels zoomable
- Every field editable; Ctrl+Z / Ctrl+Y to undo/redo corrections
- Nothing becomes a real case until **Confirm & Create Cases**

### Page 4: Campaign Dashboard (4 sub-tabs)
- **Overview:** metric cards (Total/Reached/Failed) + a mini-metrics row
  (visited, pending visit, reach rate, WA delivered, SMS delivered) + progress
  bars (reached / visited / closed) + separate WhatsApp and SMS channel
  breakdown cards (sent/delivered/read/failed) + category-breakdown bars per
  balance band + an Escalations panel + a flagged (`is_sensitive`) panel, shown
  only if any case is flagged
- **Cases:** Full table with filters (band, comm status, business status) +
  search + spreadsheet-style keyboard navigation + per-row action button
- **Reports:** Batch summary, escalation list, visit log — downloadable CSV
- **Settings:** CSP config form + WhatsApp status/QR + SMS status + audit log link

### Case detail page (from anywhere — click a case)
- Full record, editable until the case is queued/sent
- Approve for Sending button (case not yet queued)
- Communication history (channel, status, date, time in IST, detail)
- Prev/Next navigation through the batch (arrow keys too)

### Manual review-and-approve sheet ("Check manually, then approve")
- Lists every **reachable** customer in the batch (not-reachable ones are
  excluded here — they appear in the Escalations panel instead)
- Approve / Undo per row, "Approve All Remaining", "Send Approved Now"

---

## 17. Dashboard metrics

### Top metric cards (campaign independent)
1. Total cases
2. Reached (WA + SMS combined — delivered or read)
3. Failed (both channels)

### Mini-metrics row
visited · pending visit · reach rate % · WA delivered · SMS delivered

### Channel breakdown cards
1. WhatsApp — sent / delivered / read / failed
2. SMS — sent / delivered / failed

### Category breakdown bars (inoperative campaign)
One bar per real balance band: `0.1<100`, `100<1000`, `1000<10000`, `B>10000`
— each showing reached/total, colored by urgency (the two higher bands stand
out).

### Flagged panel (only if any case has `is_sensitive=1`)
- Shows record number + village
- Approve button (sends message)
- Skip button (marks as skipped)
- Not shown at all today, since no band is currently flagged sensitive (§8)

---

## 18. Case table columns

| Column | What it shows |
|--------|--------------|
| # | Row number |
| Name | Masked by default (click to reveal briefly) |
| Mobile | Masked by default (click to reveal briefly, together with the name) |
| Band | Balance band pill |
| Channel | WA / SMS / Fail |
| Comm status | pending / delivered / read / failed / escalated |
| Business status | Not visited / In progress / Completed / Closed |
| Village | Village name |
| Action | Button changes per state: Visited / Done / Close |

---

## 19. Business status transitions

```
pending
  ├── (CSP clicks "Visited") ──────────────┐
  ↓ (automatic — a message was sent)       │
customer_not_visited ── (CSP clicks "Visited") ──┐
                                                   ↓
                              customer_visited_in_progress
                                                   ↓ (CSP clicks "Done")
                                          process_completed
                                                   ↓ (CSP clicks "Close")
                                              case_closed
```
`pending` can move directly to `customer_visited_in_progress` (a customer may
walk in before/without a message being sent) as well as via
`customer_not_visited` in the normal flow.

---

## 20. WhatsApp configuration

- Engine: Baileys (`@whiskeysockets/baileys`, Node.js) — talks to WhatsApp
  directly over the multi-device WebSocket protocol; **no browser, no
  Chromium, no visible window**
- Dedicated SIM — separate from CSP personal number
- QR code scan once — session persisted locally in `whatsapp/.wa_session/`
- On logout (device unlinked from the phone), the server automatically clears
  the stale session and generates a fresh QR by itself; a "Reset & New QR"
  button in Settings forces this manually if ever needed
- Runs as background HTTP server on `127.0.0.1:3000`
- Python calls it via HTTP POST (`{mobile, message}` only)
- Daily limit: 200 messages (safe limit)
- Delay: 12 seconds between messages, checked in 1-second slices so
  Pause/Stop take effect within a second
- Message-status events (Baileys `WAMessageStatus`): 1=pending, 2=sent
  (server ack), 3=delivered, 4=read, 5=played (voice note, treated as read)
- No reply listening — completely deaf to customer replies

---

## 21. SMS configuration

- Provider: MSG91
- Cost: ₹300-500/month (only paid component)
- Only triggered when WhatsApp fails
- Sends: phone number + generic Hinglish message
- 160 chars max per segment (use Hinglish Roman script — not Devanagari)
- DLT registration required (TRAI mandate for India)
- Sender ID: registered with DLT
- Status: the fallback path is fully built in code; MSG91 account
  activation/DLT registration is a pending **operational** step, not a code gap

---

## 22. Data the bank sends — confirmed columns

From scanned PDF analysis (Kushinagar district, Tamkuhi Raj taluka, CSP 1ABXXXXX):

| Column | Required | Example |
|--------|----------|---------|
| SR NO | No | 1, 2, 3 |
| Branch code | No | 1332 |
| CSP code | No | 1ABXXXXX |
| Account number | Yes | 34XXXXXXXX |
| Name | Yes | RAMESH KUMAR |
| Balance band | Yes | 100<1000 |
| Father name | No | RAJU KUMAR |
| Mobile number | Yes | 98XXXXXXXX |
| Taluka | No | Tamkuhi Raj |
| Village | No | Ahiraule |
| Address | No | VILL-AHIRAULI DIST-KUSHINAGAR 274302 |

**Account type: Jan Dhan (zero balance minimum — no minimum balance requirement)**

If the bank can provide this as an **Excel/CSV export** (their source system
is digital), that route is 100% accurate with no OCR involved at all — the
scanned-PDF/OCR path exists for when only a paper/scan is available.

---

## 23. Inoperative account reasons (for classification only — NOT for messages)

| Band | Real world reason |
|------|------------------|
| 0.1 < 100 | Account opened never used / balance drained / switched to UPI app |
| 100 < 1000 | Migrant worker left / seasonal farmer / elderly can't visit |
| 1000 < 10000 | MNREGA/subsidy not collected / remittance from migrant family / own savings |
| B > 10000 | Large idle balance — same reasons as above, at a larger scale |

**These reasons are for internal classification logic only. They never appear in messages to customers.**

---

## 24. Cost structure

| Component | Cost |
|-----------|------|
| Baileys (WhatsApp) | ₹0 |
| docTR + Tesseract OCR | ₹0 |
| Templates (no LLM) | ₹0 |
| SQLite | ₹0 |
| Flask | ₹0 |
| MSG91 SMS | ₹300-500/month |
| **Total** | **₹300-500/month** |

---

## 25. Key design principles — never violate

1. **Campaign independent** — core system never changes for new campaigns. Only campaign config changes.
2. **Case oriented** — each customer = one case record in the database. Independent. Atomic.
3. **N stays N** — total cases = confirmed rows from the reviewed upload. Never more, never less at any point.
4. **On-premise** — all customer data processing on CSP PC. No cloud.
5. **No LLM** — pure template engine. Deterministic. Safe. Fast. Free.
6. **Graceful fallback** — WhatsApp fail → SMS. SMS fail → escalate to CSP.
7. **Two-level tracking** — comm tracking automatic, business tracking manual.
8. **One-way communication** — system sends only. Never receives or processes replies.
9. **DPDP safe** — minimum data in messages, on-screen masking, no financial details, local storage only.
10. **CSP name only** — Eko Bharat Ventures never appears anywhere.

---

## 26. What NOT to build (scope boundaries — core platform)

- No LLM integration (was considered, rejected for DPDP + cost reasons)
- No cloud hosting for customer data (on-premise mandate)
- No Google Vision OCR (cloud — rejected)
- No Gemini API (cloud — rejected)
- No reply handling (no-reply policy)
- No auto-reply (no-reply policy)
- No KYC campaign yet (future)
- No Aadhaar linking campaign yet (future)
- No multi-CSP support in the core dashboard itself (single CSP PC) —
  multi-CSP fleet management is being addressed separately by the Eko Admin
  Portal (see `ADMIN_PORTAL_DESIGN.md`), which does not touch customer data
- No mobile app (browser dashboard only)
- No email channel (not authorized)

---

*End of CLAUDE.md — Read this fully before writing any code.*
