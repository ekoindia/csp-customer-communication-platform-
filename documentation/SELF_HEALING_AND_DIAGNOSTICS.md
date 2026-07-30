# Self-Healing & Self-Diagnosing — whole-software reference

**Goal:** the platform repairs what it can by itself, and when it genuinely cannot,
it states the exact reason in one line instead of failing silently. No step is
allowed to leave a CSP staring at an empty screen with no explanation.

**Hard rule — self-healing NEVER widens the compliance boundary.** Every repair in
this document is limited to the machine's *own* software and operational state.
None of it moves customer data anywhere, keeps anything longer, or adds an external
dependency. Concretely, a self-heal action may:

| A repair MAY | A repair MUST NEVER |
|---|---|
| reinstall/repair the app's own dependencies | send customer data anywhere new |
| download SOFTWARE (Python, Node, git, OCR model weights) | upload a scan, name, mobile, account no. or message text off the machine |
| delete its own stale state (broken venv, dead WhatsApp session, stray files, leftover uploads) | retain PII longer than the existing lifecycle, or restore purged PII |
| restart its own local processes | call a cloud LLM / cloud OCR / any foreign service |
| re-generate a message **from the locked template** | write or alter message wording |
| report **counts** northbound | report identifiers northbound |

The boundary itself is unchanged from `CLAUDE.md` §15: on-premise processing, no
LLM, no cloud APIs, template-only messages, PII encrypted at rest and purged at
case closure, server-side OCR is process-and-forget (counts only).

---

## Stage 1 — Install / update

| Failure (all seen live) | Detection | Automatic repair | If it can't |
|---|---|---|---|
| App package download fails / is huge | package size + content check | slim ~300 KB package from the Eko server; if the server is behind, patch only the changed bridge files (~30 KB) from the repo | says download failed, keeps the existing install untouched |
| `winget` hangs (its CDN is blocked on CSP networks) | not used at all any more | Python 3.11 / Node LTS fetched **directly** from python.org / nodejs.org over plain HTTPS and installed silently | falls back to the "install manually" message |
| Broken venv — `python.exe` present but `pyvenv.cfg` gone ("No pyvenv.cfg file") | the interpreter is *run*, not just found on disk | stops app processes, deletes `.venv`, recreates it, re-verifies | clear "close any open CSP Platform window and re-run" |
| Dependencies half-installed | every critical library is **imported**, not assumed | pip retry → clean `--force-reinstall` → re-verify | names the missing libraries |
| npm needs `git` for a Baileys dependency ("spawn git ENOENT") | `git` absent from PATH | portable **MinGit** fetched into `<install>\tools\mingit` and put on PATH for that npm run only — no system install, no admin | says WhatsApp deps may fail; dashboard + OCR keep working |
| Setup wrote a placeholder into `.env` (`ADMIN_API_BASE=REPLACE-…`) | `config.py` validates the value | placeholder / scheme-less values are **ignored** and the built-in Eko address used | logs the value it ignored |
| Server-OCR left off, so scans silently extract 0 rows | `SERVER_OCR_ENABLED` default + written by the setup file | defaults to on (still gated on a real API key) | Channel Status shows why OCR is unusable |
| Leftover uploads from a crash | startup sweep of `uploads/` | deleted (DPDP hygiene) | logged loudly |

Entry point: `CSP_FIX_ALL.bat` — one file, re-runnable, keeps cases/keys/WhatsApp
session, ends with the self-heal pass and opens the dashboard.

## Stage 2 — Upload → OCR → extraction

| Failure | Detection | Behaviour |
|---|---|---|
| **0 rows, no reason** (the worst one) | every reason is recorded during the run | red banner on the review screen: server unreachable / bad-or-missing API key / server returned 0 rows on a poor-rotated scan / empty file / no table — plus what to do |
| Bad render vs unreadable scan | first page's rendered size is reported | banner states what was sent: `2480x3508 px, ~1800 KB, 300 DPI` |
| One page fails in a multi-page PDF | per-page result | the other pages' rows are **kept**; each failed page is named with its reason ("Page 12 could not be read: …") |
| Same page appears twice | content hash per page (transient) | OCR'd once, rows reused, page order preserved |
| Multi-page upload starves a 4 GB box | measured free/total RAM | in-flight pages capped (2, or 1 under 0.8 GB free) — DPI/accuracy unchanged |
| Local OCR absent on a server-OCR install | install type | local OCR is **not** attempted (it used to crash with "0.3 GB free"); a light source preview is still rendered for the review |
| A column layout we've never seen | two-pass header mapping (exact wins over substring) | all real formats map (standard, `A/C No`, `ACNO`, `MEMB_CUST_AC`); missing band → safe default; blank mobile → "not reachable"; **every original column carried through** |
| Account number and name printed with no gap (`50000000001Ramesh Kumar`) | letters found in the account token/cell | letters become the start of the name; account stays digits-only |
| Rows the detector missed on a dense scan | optional second pass (`OCR_SECOND_PASS=adaptive`) | a second look at the same page in RAM, merged by account number — off by default because it doubles CPU time |
| Human last line of defence | — | the **review gate**: nothing becomes a case until the CSP confirms; rows are editable and "+ Add row" exists |

## Stage 3 — Eko server link

`core/server_ocr_client.check_connection()` is called by Channel Status **before**
any upload, so the CSP sees the link state up front: `connected` / `off` /
`not_configured` / `unreachable` / `bad_key` / `server_error` — each with a
plain-English reason **and** the fix ("ask Eko to issue a fresh key, then re-run
this CSP's Setup file").

Server side: OCR is bounded by a semaphore sized from the machine's real core
count (2 cores always left for the fleet heartbeats), returns 503 `ocr_busy`
instead of thrashing, and lazy-imports its OCR stack so a missing dependency can
never take the portal down (503 `ocr_unavailable`).

## Stage 4 — WhatsApp bridge (`core/selfheal.py`)

Run automatically by `CSP_FIX_ALL.bat`, or by hand: `python -m core.selfheal`.

| Failure (all seen live) | Detection | Automatic repair |
|---|---|---|
| Stray `node` / `Node.js` files in `whatsapp\` — cmd runs those instead of Node, so the bridge exits with **no output at all** | files present | deleted |
| Baileys at an unusable version — 7.x (ESM-only RC + native bridge) or <6.7.22 (carries advisory GHSA-qvv5-jq5g-4cgg) | version read from `node_modules` | reinstalled to the pinned range, with the app's portable MinGit on PATH |
| Port 3000 held by a stale process that doesn't answer `/status` | port open but no HTTP response | stale Node processes stopped |
| Half-written `.wa_session` → Baileys stuck before the QR | log says "refusing"/"temporary block", or `creds.json` missing | session cleared **only when not connected** — a linked CSP never has to re-scan |
| Stale WhatsApp web version → "Connection Failure", **no QR ever** | — | `fetchLatestBaileysVersion()` is passed to the socket |
| A 3 s retry loop that itself earns a temporary block | consecutive failures counted | exponential backoff 5 s→60 s, stop after 6 with "wait ~30 minutes, then Reset & New QR" |
| Delivery ACKs silently rejected (webhook token set in Flask, absent in the bridge) → cases stuck at "sent" | token parity check | the launcher now passes `WEBHOOK_TOKEN`; a mismatch is reported |
| The bridge's failure reason was **thrown away** (stdout → DEVNULL) | — | output kept in `whatsapp/wa_server.log`; Channel Status shows the reason from it |

## Stage 5 — Sending / dispatch

| Failure | Behaviour |
|---|---|
| One case raises mid-batch | caught per case, escalated, counted — **the rest of the batch still goes out** (it used to abandon everything) |
| A refusal reported as HTTP 200 (`success:false`) | the response **body** is the source of truth — a customer who got nothing is no longer recorded as reached (same fix for MSG91's `type:error`) |
| Number not on WhatsApp (the dominant real-world cause) | checked before sending; recorded as a distinct reason so it's separable from a network failure, then SMS → escalate |
| Queued case with no number | escalated, never handed to the provider |
| Customer visited before the message went out | the automatic `pending → not_visited` step only applies from `pending`, so the CSP's later status wins |
| WhatsApp fails after the fact (wrong number, bridge down) | the case is **retryable**: approve again → new pending attempt, failed attempt kept in history, escalation flag cleared |
| CSP corrects a wrong number | "Check WA" verifies the number is on WhatsApp **before** re-approving |

## Stage 6 — Tracking → closure → reporting

| Failure | Behaviour |
|---|---|
| Cases stuck with no explanation for the bank | **case outcome** (reactivated / deceased / moved away / wrong contact / refused / account closed / other) — editable even after closure, survives the PII purge, included in the CSV |
| OCR got a field wrong and the case is already created | per-case correction of name / mobile / account / father / village / taluka / address; refused on a purged case; duplicate account refused; dedup index moved with it; message regenerated **from the locked template** if the name changed and nothing was sent |
| CSP edits their address in Settings but messages keep the old one | unsent messages are rebuilt on save; already-sent ones are deliberately left as the record of what was received |
| Admin panels describing only part of the fleet | each panel states its coverage ("covers 37 of 87 cases; 1 of 2 CSPs report it"); visit statuses are mutually exclusive and add up to the total; "ever visited" is separated as cumulative |
| A mistyped duplicate CSP ID splitting a CSP's numbers | admin can delete a CSP from all its tables |
| A format-specific chart read as fleet-wide | the balance-band chart is gone; groupings are generated from whatever dimensions the data actually has |

---

## What is deliberately NOT self-healed

Honest list — these need a human, and the software says so rather than pretending:

1. **Reach.** Most numbers on a rural Jan Dhan list have no WhatsApp account. The
   fix is SMS (MSG91/DLT activation), not code.
2. **A genuinely unreadable scan.** The review gate + "+ Add row" is the answer;
   OCR is ~96–98 % on a dense page.
3. **A missing column in the bank list** (e.g. no phone number at all) — reported
   as a DATA gap, never silently as "poor reach".
4. **A revoked / wrong API key.** Named exactly (`bad_key`), but only an admin can
   issue a new one.
5. **Per-page OCR speed** on the CPU-only server (~50–160 s for a dense page).
   Parallelism helps throughput, not per-page latency; a GPU is the real fix.
6. **A WhatsApp block from repeated attempts.** The backoff stops making it worse
   and tells the CSP to wait — it cannot un-block the number.

## Test coverage

`tests/test_selfheal.py` (version gate, log→reason, webhook parity, shadow-file
removal, "a pass never raises"), `test_server_ocr.py` (partial-page success,
per-upload dedup, connection self-diagnosis), `test_extraction.py` (0-row
self-diagnosis, format-agnostic carry), `test_column_mapper.py` (all real header
layouts), `test_account_name_join.py`, `test_dispatcher_contract.py` (a refusal is
a failure), `test_approval.py` (retry, batch not abandoned, no status drag-back),
`test_settings_message_refresh.py`, `test_reporting_bifurcation.py`. **223 passing.**
