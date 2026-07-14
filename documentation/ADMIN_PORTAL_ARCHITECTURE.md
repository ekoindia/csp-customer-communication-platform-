# Eko Admin Portal — Architecture & Workflows

*How Eko centrally watches all 523 CSP installs through ONE API, with campaign
message-tracking + physical-visit tracking flowing in, and **zero customer PII**
ever reaching Eko — not even masked.*

> Commission math is intentionally **out of scope** here (deferred / EDR-1). The
> `earnings` field exists in the schema and API but is reported as `0` until the
> formula is decided. Everything else is fully built.

---

## 1. The shape (who runs where)

```
   523 × CSP local PC (on-premise)                 Eko server (the RAG server)
 ┌───────────────────────────────┐              ┌──────────────────────────────┐
 │  CSP dashboard (Flask :5000)   │              │  Admin portal (Flask :7000)  │
 │  local SQLite (ALL customer    │              │  admin.db (NO customer data) │
 │  data lives & stays here)      │              │                              │
 │                                │   ONE API    │  UI: Fleet / Campaigns /     │
 │  core/admin_reporter.py  ──────┼──HTTPS──────▶│      Earnings / WhatsApp     │
 │   • report_once()  (push)      │  outbound    │  api.py  /api/v1/report (in) │
 │   • sync_once()    (pull)      │◀─────────────┤          /api/v1/sync   (out)│
 └───────────────────────────────┘   only       └──────────────────────────────┘
```

**Why the CSP pushes outbound (and Eko never connects in):** a CSP PC sits
behind a home/shop broadband router (NAT) with no public address — Eko *cannot*
reach into it. So the local app initiates every connection: it PUSHes status on
a timer, and PULLs anything Eko wants to send back (new version, commands). This
is also the safer direction: the customer PC never opens a listening port to the
internet.

---

## 2. The single Eko API (v1)

One base URL, served by the admin portal on Eko's server. `config.ADMIN_API_BASE`
on each CSP points at it.

| Method | Path | Direction | Purpose | Auth |
|--------|------|-----------|---------|------|
| `POST` | `/api/v1/report` | CSP → Eko | PII-free heartbeat + full campaign status | `X-API-Key` |
| `GET`  | `/api/v1/sync`   | Eko → CSP | latest app version + queued commands | `X-API-Key` + `csp_id` |
| `GET`  | `/api/v1`        | — | health / discovery | none |

Auth is a **per-CSP API key** (`api_keys` table). 523 CSPs → 523 keys; a key
identifies exactly one `csp_id` and can be deactivated (`active=0`) to cut a CSP
off without touching the others.

---

## 3. Workflow A — report (CSP → Eko), every 5 min

1. `admin_reporter.build_payload()` reads the **local** SQLite and assembles an
   allow-listed dict (see §5). Counts are computed with the *same SQL the CSP's
   own dashboard uses* (`database/queries.batch_overview`), summed campaign-wide,
   so admin numbers match what the CSP sees.
2. `report_once()` → `POST {base}/report` with `X-API-Key`.
3. `api.report()` validates the key, then writes **only** allow-listed fields:
   - `csps` — heartbeat + WhatsApp flags + **CSP machine hardware** (RAM/CPU/GPU/OS/OCR engine).
   - `progress` — one row per (csp, campaign, month): headline counts + **message
     tracking** (`wa_sent/delivered/read/failed`, `sms_sent/delivered/failed`,
     `escalated`) + **physical-visit tracking** (`visit_pending/visited/
     in_progress/completed/closed`).
   - `progress_bands` — per balance-band `total`/`reached` (band = category).
   - `audit` — event *types* only (login, upload, send…), with timestamps.
4. `last_seen` is stamped → drives the online/offline dot (15-min freshness window).

Upserts are keyed `UNIQUE(csp_id, campaign_id, month)`, so a CSP re-reporting the
same month **updates in place** — counts never inflate (the "N stays N" rule
extends to the admin side).

## 4. Workflow B — sync (Eko → CSP), same tick

1. `sync_once()` → `GET {base}/sync?csp_id=…` with `X-API-Key`.
2. Server returns `{latest_version, commands[]}` and marks any pending commands
   `delivered` (deliver-once). Eko can't push, so this pull is how a new version
   announcement or an admin command reaches a local PC.
3. Version-based self-update **is** wired: when `/sync` reports a newer
   `latest_version` + `update_url`, `core/admin_reporter.sync_once()` calls
   `core/updater.stage_update()` to download + verify (sha256) + stage the
   package, and `run.bat` applies it on the next start. The published version
   is taken from the package's own `VERSION` file (see the "Updates" page), so
   it always matches what the CSP will report after applying — no re-stage
   loop. What remains a follow-up is mapping the discrete queued *commands*
   (`update_software` / WA-recheck / pause) to actions — those are today only
   surfaced (printed); their transport is done.

---

## 5. The DPDP boundary — exactly what leaves, and what never does

**Leaves the CSP PC (allow-list, enforced in `admin_reporter.build_payload` AND
again in `api.report` — belt and suspenders):**

- `csp_id`, `install_id`, CSP **shop** name (public, not a person), app version
- WhatsApp `connected` / `banned` flags
- CSP **machine** hardware: RAM, free RAM, CPU threads, GPU present, OS, OCR engine
- **Aggregate counts** per campaign/month: message-tracking + visit-tracking + per-band totals
- Audit **event types** + timestamps

**NEVER leaves — and there is no column to store it even if it did:**

- ❌ customer name, mobile, account number, father's name, address
- ❌ village/taluka as an identifier (only band categories, which are counts)
- ❌ message text, case id, or *any* per-customer row — **not even masked**

The admin DB schema (`admin_portal/schema.sql`) has **no PII columns at all**.
Every number the admin sees is "how many cases are in state X", never "which
customer". A leaked/rogue field in the payload is dropped because ingest reads a
fixed set of keys — anything else is ignored. (Verified by a PII-injection test:
a payload carrying `mobile`/`customer_name`/`account_number` stores nothing.)

---

## 6. Admin portal pages

| Page | Shows |
|------|-------|
| **Fleet** | every CSP, online/offline, version, WhatsApp state |
| **CSP detail** | that CSP's machine hardware + per-campaign message tracking, visit tracking, band bars, recent event types |
| **Campaigns** | fleet-wide rollup of message + visit tracking per campaign/month, PLUS a per-CSP breakdown table under each campaign/month |
| **Earnings** | per-CSP earnings (0 until the commission formula lands) |
| **API Keys** | issue / rotate / revoke per-CSP keys (§8) — the gate every CSP must pass before it can report at all |
| **New CSP Setup** | upload the install package (`CSP_Platform.zip`) once; get a public download link + a ready `CSP_Setup.bat` with it baked in |
| **Updates** | publish a new version (upload a `.zip` — sha256 auto-computed — or paste an external URL), optionally push an "update now" nudge |
| **WhatsApp Health** | connected / banned across the fleet |

---

## 7. Go-live on Eko's RAG server — "koi API issue to nahi aayega na?"

**No.** The build is deliberately config-driven so nothing structural changes:

1. **Server side (once, on Eko's box):** deploy `admin_portal/`, run it with
   `ADMIN_BIND_HOST=0.0.0.0` (env var — no code edit) behind the RAG server's
   HTTPS/reverse proxy. The endpoint paths (`/api/v1/report`, `/api/v1/sync`) are
   identical to the demo.
2. **CSP side (per install):** set one env var / config value —
   `ADMIN_API_BASE=https://admin.eko.co.in/api/v1`. That is the *only* thing that
   changes. `report_once()`/`sync_once()` build every URL from that base.
3. **Auth is unchanged:** `X-API-Key` header works the same over HTTPS as HTTP.
4. **Turn it on:** `ADMIN_REPORT_ENABLED=1` (default off, so nothing reports
   until you deploy the server and flip it).

So the whole cutover is: deploy the folder, set `ADMIN_BIND_HOST`, hand each CSP
its `ADMIN_API_BASE` + `ADMIN_API_KEY`, flip `ADMIN_REPORT_ENABLED=1`. No
API/schema/route change. The demo you build locally *is* the production API,
only the base URL and bind host differ — both are environment variables.

**One operational (non-code) go-live to-do:** a real TLS cert on the public
URL. Issuing a unique `api_key` per CSP is no longer a to-do — it's a built
feature (§8): no demo key ships by default at all (a fresh `admin_portal/db.py`
`setup()` seeds only the admin login, never an `api_keys` row), so every CSP
gets a real, unique, admin-issued key from day one.

---

## 8. Issuing per-CSP API keys — "API Keys" page

`/api-keys` (nav: **API Keys**). Before a CSP install can report at all
(`POST /report` / `GET /sync` both 401 on an unknown or inactive key), an admin
generates one here:

- **Issue** — enter the CSP ID (+ optional display name) → a
  `secrets.token_urlsafe(32)` key is generated and shown **once**, in full,
  right on that response (never redirected away, or the plaintext would be
  lost). Hand that CSP ID + key to the CSP — paste into INSTALL.bat's
  "Connect to Eko Admin Portal" prompt, or the dashboard's `/admin-connect`
  screen, or `.env` directly.
- **Rotate** — issuing again for a CSP ID that already has a key REPLACES it;
  the old key stops authenticating immediately (no grace period — a CSP using
  the stale key gets 401 on its next report/sync and needs the new one).
- **Revoke / Reactivate** — toggles `active` without deleting the row (audit
  trail preserved); a revoked key 401s immediately.
- The keys list only ever shows the **last 4 characters** — the full value is
  never displayed again after the one-time issue/rotate response.

No demo/placeholder key exists in a fresh `admin.db` — `admin_portal/db.py`'s
`setup()` seeds only the `admin`/`admin123` portal login (change that password
for a real deployment), deliberately nothing in `api_keys`.

---

## 9. Onboarding a real CSP, end to end

1. **Eko:** `/api-keys` → issue a key for the new CSP ID. Copy it.
2. **Eko:** `/setup` → upload `CSP_Platform.zip` once (or reuse the last
   uploaded one) → download the generated `CSP_Setup.bat` (APP_URL baked in).
3. **Send the CSP:** the `CSP_Setup.bat` file + the CSP ID and API key from
   step 1 (over WhatsApp/email — these two strings are not secret-sensitive
   enough to need a separate secure channel, but treat them as credentials).
4. **CSP double-clicks `CSP_Setup.bat`:** downloads the package, installs
   Python/Node/Tesseract/deps, and — new — **prompts for the CSP ID + API key**
   right there in the installer (blank/Enter = skip, configure later instead).
   Given both, it writes `.env` directly; the app starts already connected.
5. **If skipped at install:** the dashboard's first successful login redirects
   to `/admin-connect` (same two fields, same "skip for now" option) — shown
   exactly once; reachable again anytime after from Settings → "Eko Admin
   Connection" → Reconfigure.
6. **Either path:** `core/admin_reporter.py` starts reporting immediately
   (live in-process, no restart) and shows up on Fleet/Campaigns/CSP-detail.
7. **Software updates, later:** Eko uploads a new version on `/updates`
   (sha256 auto-computed) → every connected CSP's next `/sync` learns about it
   → `core/updater.py` downloads + verifies + stages it in the background →
   the CSP dashboard shows a green **"Update ready — vX.X.X"** banner telling
   the operator to restart the app (close both windows, reopen the desktop
   icon) — `run.bat` applies the staged update before the app starts back up.
