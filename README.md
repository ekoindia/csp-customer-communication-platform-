# CSP Communication Platform

An on-premise tool for a CSP (Customer Service Point) to notify SBI customers
about account issues over WhatsApp (with SMS fallback), in the CSP's own name.
It reads the bank's customer list, lets the CSP review it, then sends and tracks
the messages. All customer data stays on the CSP's PC — no cloud, no AI.

First campaign: **Inoperative Accounts**. The engine is campaign-independent, so
new campaigns (KYC, Aadhaar linking) plug in as config.

## What it does

- Upload the bank's list — Excel/CSV, or a scanned/photo PDF.
- Extract each customer automatically (Excel/CSV is exact; scans use on-device OCR).
- The CSP reviews every row next to the source before anything is saved.
- Generate a fixed WhatsApp/SMS message (customer name + the CSP's details).
- Send the whole batch, or approve case-by-case, with pause/resume/stop.
- Track delivery automatically, and the CSP's own follow-up (Visited → Done → Closed).

## Quick start

Needs Python 3.11+, Node 18+, and Tesseract OCR (only for scans).

```bash
cd csp_dashboard
pip install -r requirements.txt        # use requirements-lite.txt on a 4 GB PC
cd whatsapp && npm install && cd ..
run.bat
```

Dashboard opens at http://127.0.0.1:5000. On first run the WhatsApp window shows
a QR — scan it once from your phone (WhatsApp → Linked Devices). Set the CSP's
name/phone/address in Settings (they go into every message).

## How it works

1. **Dashboard** (Flask) — login → pick campaign → upload → review → dashboard.
2. **Extraction** — parse Excel/CSV or OCR a scan, validate, classify by balance
   band, then show the review gate.
3. **Message** — fill a fixed template (no AI). Locked once generated.
4. **Send** — WhatsApp via Baileys (no browser) → SMS fallback (MSG91) → escalate.
5. **Tracking** — delivery status (automatic) + business status (CSP clicks).

A "case" is one customer, stored across four SQLite tables keyed by `case_id`.
N rows uploaded → exactly N cases, never more or fewer.

## OCR on scanned PDFs

- **Excel/CSV** is parsed directly — 100% accurate, no OCR. Preferred whenever
  the bank can give a digital file.
- **Scans** use on-device OCR. A capable machine uses docTR; the 4 GB deploy PC
  uses OnnxTR (docTR models on ONNX Runtime, no PyTorch, ~0.7–1 GB). Account and
  mobile digits get an extra read from a small custom model. Rows are anchored on
  the account number, so it works even when the scan's table lines are faint.
- The CSP confirms every field at the review gate, so nothing wrong gets saved.
  Mobile numbers can also be corrected later on the case detail page.

## Data & privacy

- All customer data lives in one local SQLite file on the CSP's PC, encrypted at
  rest, and is purged when a case is closed.
- Servers bind to `127.0.0.1` only — not reachable from the network.
- Names and mobiles are masked on screen, shown briefly on click.
- Messages carry only the phone number and the text; replies are never read.
- No cloud OCR and no LLM anywhere in the pipeline.
- The admin portal (when used) only ever receives PII-free info, never customer
  data. Each CSP has its own machine and its own database — nothing is shared.

## Balance bands & templates

| Band | Tone | Template |
|---|---|---|
| 0.1<100, 100<1000 | normal | template_1 |
| 1000<10000, B>10000 | urgent | template_3 |

Two WhatsApp templates and one SMS template, all in the CSP's name (no bank or
Eko name in the text). See `campaigns/inoperative/`.

## Updating a CSP install

Run `UPDATE.bat` (in the install folder) or `CSP_Update.bat` (a desktop
shortcut). It pulls the latest code from GitHub, replaces the program files and
removes ones dropped in the new version, keeps the database/config/WhatsApp
login, restores the desktop icon, and restarts. The old files are backed up so a
bad update can be rolled back.

## Layout

```
csp_dashboard/    the CSP app (runs on the CSP PC)
admin_dashboard/  Eko admin portal (runs on Eko's server, never sees customer data)
documentation/    detailed docs
CLAUDE.md         full technical context
```

## Tests

```bash
cd csp_dashboard && python -m pytest
```

## Cost

Everything is free except MSG91 SMS (~₹300–500/month), and only once SMS
fallback is activated.
