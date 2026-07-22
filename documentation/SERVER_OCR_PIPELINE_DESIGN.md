# Server-side OCR pipeline — production-grade, DPDP-compliant design

**Goal:** move the heavy OCR compute OFF the CSP's low-end machines onto Eko's
server (so accuracy and throughput are no longer bottlenecked by 4 GB PCs),
**without leaking CSP customer data into Eko's system**, and **without violating
the DPDP Act**. The WhatsApp campaign continues to run entirely on the CSP's
local machine — only OCR uses the server.

---

## 0. The core reality (why not "just hash it")

OCR must read real pixels. You cannot OCR a hash (one-way) or homomorphically
encrypted data (not practical for vision). So the server *will* see the image
pixels while it runs OCR. The engineering goal is therefore not "never see" but:

- **Transient, sealed processing** — decrypt only in memory, ideally inside a
  hardware enclave the operators themselves cannot read.
- **Zero retention** — nothing (image, text, logs) is stored or reusable after
  the response is returned.
- **Lawful processing** — Eko runs the OCR service as a **Data Processor** for
  the CSP/bank (the **Data Fiduciary**), under a written processing agreement.

That combination is what makes "our server computes it, but it never lands in
our system" true in every sense that matters (and that DPDP cares about).

---

## 1. Roles under DPDP (this is what makes it legal)

- **Data Fiduciary:** the CSP / SBI (they decide the purpose — customer outreach).
- **Data Processor:** the Eko OCR service — processes **only on instruction**,
  **only for OCR**, retains nothing, no secondary use. (DPDP §8 processor model.)
- A **Data Processing Agreement (DPA)** between Eko and the fiduciary is required
  and is the legal basis. This is not a loophole — it is the standard, compliant
  way a fiduciary uses a processor's compute. Get this signed before go-live.

---

## 2. Architecture (high level)

```
        CSP LOCAL MACHINE (light)                    EKO SERVER (heavy, stateless)
 ┌────────────────────────────────┐        ┌──────────────────────────────────────┐
 │ Capture: phone camera / PDF /   │        │  OCR PROCESSOR (per-request ephemeral) │
 │ scanned image                   │        │  ┌────────────────────────────────┐   │
 │        │                        │  mTLS  │  │  TEE / enclave (decrypt here)   │   │
 │        ▼  envelope-encrypt      │ +      │  │   → high-accuracy table OCR     │   │
 │  [ encrypted image + data key ] ├───────▶│  │     (docTR / PaddleOCR / VLM)   │   │
 │                                 │        │  │   → structured rows             │   │
 │  ◀──── encrypted rows ──────────┤◀───────┤  │   → wipe memory, return         │   │
 │        │ decrypt locally        │        │  └────────────────────────────────┘   │
 │        ▼                        │        │  Retains: NOTHING of content.          │
 │  REVIEW GATE (human) → confirm  │        │  Logs: request-id, ts, page-count,     │
 │        ▼                        │        │        latency ONLY (no content).      │
 │  Local SQLite (PII encrypted)   │        └──────────────────────────────────────┘
 │        ▼                        │
 │  WhatsApp campaign (LOCAL)      │   ← never leaves the CSP machine
 └────────────────────────────────┘
```

**Only the image goes out (encrypted); only structured text comes back
(encrypted). Cases, messaging, and all persistence stay on the CSP machine.**

---

## 3. Data flow (step by step)

1. **Capture** on the CSP client (phone app or desktop upload): image/PDF page.
2. **Minimize** (client-side, optional): grayscale + deskew + crop to the table
   region; drop EXIF/geo metadata. Smaller payload, better OCR, less incidental data.
3. **Envelope-encrypt** (client): generate a random one-time AES-256 data key,
   encrypt the image with it; encrypt the data key to the **enclave's attested
   public key** (so only a genuine, unmodified enclave can unwrap it).
4. **Transport:** POST to the Eko OCR service over **mutual TLS**, authenticated
   with the CSP's per-install API key + a short-lived request token. Rate-limited.
5. **Attestation check:** the client verifies the enclave's remote-attestation
   quote BEFORE sending, so the key is only ever released to a verified enclave
   running the exact expected code (not a tampered/loggable server).
6. **Process (in enclave, in RAM):** decrypt → run the high-accuracy table OCR
   model → produce structured rows (account / name / mobile / balance_band) →
   **encrypt the rows back to the client** → **zero the memory**. No disk, no DB,
   no content logs, no egress anywhere except the response.
7. **Return:** encrypted structured rows to the CSP client.
8. **Decrypt + Review (CSP local):** the client decrypts, shows the existing
   **review gate**; the CSP fixes any errors; confirms → cases created in the
   **local SQLite** (PII encrypted at rest, as today).
9. **Campaign (CSP local):** WhatsApp/SMS runs entirely locally, unchanged.
10. **Eko server keeps:** only **anonymous operational metrics** — request count,
    page count, latency, success/fail — for billing/monitoring/commission. **No
    image, no text, no customer identifiers, ever.**

---

## 4. DPDP compliance controls (mapped)

| DPDP principle | How this design satisfies it |
|---|---|
| Lawful processing (processor) | DPA between Eko (processor) and CSP/SBI (fiduciary); OCR-only instruction |
| Purpose limitation | Service does OCR and nothing else; no secondary use, no model training on data |
| Data minimization | Only the image needed for OCR is sent; metadata stripped; server gets no CSP/campaign identity beyond an opaque token |
| Storage limitation | **Zero retention** — process-and-forget, in-memory only, wiped per request |
| Security safeguards (§8(5)) | Envelope encryption + mTLS + **TEE decryption** + attestation + ephemeral compute + no content logging |
| No unauthorized access | Enclave: even Eko ops cannot read process memory; access to metrics is RBAC-controlled |
| Breach minimization | Nothing retained → the exposure window is a single in-RAM request, not a database |
| Auditability | Metadata-only audit trail (who called, when, how many pages) — never content |

**Honest boundary:** the image is *processed* by Eko's system (in RAM). With a
TEE, no human/operator/log can read it, and nothing persists — but it is still
"processing personal data," so the DPA + notice/consent chain must exist. This is
compliant processing, not an exemption.

---

## 5. Two build tiers (pick based on assurance needed)

**Tier 1 — Stateless zero-retention microservice (fast to ship):**
- OCR runs in an ephemeral, isolated container (one per request or a locked-down
  pool), no persistent volume, no content logging, egress firewalled to none.
- Encryption in transit (mTLS + app-layer). Server decrypts in RAM, wipes after.
- **Gap vs Tier 2:** a privileged Eko operator *could* in principle observe RAM.
  Mitigated by policy, isolation, and no-logging — but not hardware-guaranteed.
- Good enough to pilot with a DPA + strong ops controls.

**Tier 2 — Confidential Computing (production gold standard):**
- OCR runs inside a **TEE**: Azure Confidential VMs / AWS Nitro Enclaves / GCP
  Confidential VMs (CPU), or **NVIDIA Confidential GPU (H100 CC)** for GPU OCR.
- Client releases the decryption key only after **remote attestation** verifies
  the enclave. Even Eko admins cannot read the plaintext.
- This is the honest fulfilment of "our server computes it but it never leaks
  into our system." Higher cost/complexity.

**Recommendation:** build **Tier 1 now** (with DPA + zero-retention + attest-ready
client), then upgrade the *same* API to **Tier 2 (TEE)** for scale/citizen-grade
assurance. The client contract doesn't change between tiers.

---

## 6. Tech choices

- **OCR model (server, GPU):** a real table model — docTR, **PaddleOCR
  PP-StructureV2** (great at tabular layout), or a doc-VLM (DeepSeek-OCR /
  Unlimited-OCR class) if accuracy demands. This is the accuracy fix too.
- **Service:** FastAPI + a GPU worker queue (e.g., Celery/RQ or a simple async
  pool); stateless; horizontal autoscale for citizen scale.
- **Transport/crypto:** mTLS; envelope encryption with AES-256-GCM; keys via the
  enclave's attested public key (Tier 2) or a KMS-held key (Tier 1).
- **AuthN/Z:** per-CSP API key (already exists in the admin portal) + short-lived
  signed request tokens; per-CSP rate limits and quotas.
- **Isolation:** no persistent storage mounted; `--read-only` containers; no
  outbound network from the OCR worker except the response path; content logging
  disabled at the framework level.
- **Observability:** metrics only (Prometheus counters/histograms) — request
  count, pages, latency, error codes. Assert (in code + tests) that no field can
  carry content.

---

## 7. What changes on the CSP side

- The CSP client gains an **"OCR via Eko" path**: encrypt image → call service →
  decrypt rows → existing review gate. Falls back to local OCR only if offline.
- The 4 GB desktop no longer runs heavy OCR → **bottleneck gone**; it just does
  capture, review UI, local DB, and WhatsApp (all light).
- The **phone app becomes optional** — capture can happen on the desktop too,
  since compute is no longer the constraint. (Keep phone capture if the camera
  UX is better.)
- WhatsApp/SMS campaign: **unchanged, fully local.**

---

## 8. Phased plan

1. **DPA + governance** (parallel, non-code): processor agreement, notice/consent
   review, retention policy = zero, security policy sign-off. *Blocker for go-live.*
2. **Tier-1 service:** FastAPI OCR microservice (stateless, zero-retention,
   metrics-only logging) + strong table OCR model on a GPU box. mTLS + envelope
   encryption. Per-CSP auth via existing API keys.
3. **CSP client integration:** encrypt-and-call path + decrypt + review; offline
   fallback; feature-flag per CSP.
4. **Load/accuracy validation** on real (consented) samples; tune the model.
5. **Tier-2 upgrade:** move the OCR worker into a TEE + client-side attestation.
6. **Scale:** autoscaling GPU pool, quotas, monitoring, DR runbook.

---

## 9. Honest risks / decisions to confirm

- **Legal:** the DPA + consent chain is mandatory and is a business/legal task,
  not just code. Server-side processing of PII is lawful *with* it, not without.
- **"Sees plaintext" in Tier 1:** acceptable for a pilot with controls; Tier 2
  (TEE) is required to truly guarantee no-operator-access at citizen scale.
- **Cost:** GPU + (for Tier 2) confidential-compute instances are a real recurring
  cost — the trade for taking OCR off CSP machines and getting high accuracy.
- **Latency/connectivity:** the CSP now needs internet for OCR (campaign can
  still run offline). Keep a local-OCR fallback for no-network cases.
- **Do NOT** log request bodies, cache images, persist to disk, or train models
  on this data — enforce in code + review + tests.

---

## 10. Decisions needed before building

1. Tier 1 (ship now) vs go straight to Tier 2 (TEE)?
2. Cloud/provider for GPU (+ confidential compute vendor for Tier 2)?
3. OCR model: PP-Structure / docTR / doc-VLM — pick by accuracy vs cost.
4. Who owns the DPA + consent review (legal), and by when?
5. Capture device: keep the phone app, or desktop-only capture?

---

## 11. AS-BUILT (Tier 1 — what is actually implemented)

This section is the source of truth for the shipped code; §1–10 above are the
original design rationale. Where they differ, this section wins.

**Where it runs.** The OCR endpoint is a route on the EXISTING admin/RAG Flask
portal (`admin_dashboard/api.py` → `POST /api/v1/ocr/extract`), not a separate
FastAPI service. It reuses the portal's per-CSP API-key auth (`/report`,
`/sync`). This was chosen over a standalone service to avoid a second process to
supervise and a second nginx route (the shared box's nginx must not be touched).

**Protecting the portal (the hard rule).** The whole OCR stack
(`cryptography`, `numpy`, `opencv`, `pypdfium2`, `onnxtr`) is imported **lazily
inside the handler**, never at module load. If any OCR dependency is missing or
broken on the server, `/ocr/extract` returns a clean **503 `ocr_unavailable`**
and the fleet-heartbeat endpoints (`/report`, `/sync`) are completely
unaffected. The deploy scripts install `admin_dashboard/requirements-ocr-server.txt`
**best-effort** — a failed OCR install never blocks the portal restart.

**Engine.** Default `SERVER_OCR_ENGINE=onnxtr` — the only engine measured on the
real SBI scans (account 100% / name 99% / band 95% / mobile 85% on a 29-page
scan). `rapidocr` (PP-OCR on ONNX Runtime CPU) is wired as a selectable
challenger; promote it only if it beats onnxtr on real scans via
`scripts/ocr_benchmark.py --engines onnxtr,rapidocr`. docTR/paddle/VLM are NOT
used on this CPU-only box.

**Wire contract (both directions AES-256-GCM enveloped — `core/ocr_envelope.py`):**
```
request : { csp_id, payload: <enc{ request_id, file_type, file_b64,
                                    page_from, page_to }> }   header X-API-Key
response: { ok, payload: <enc{ request_id, xlsx_b64, page_count, row_count }> }
```
The server returns an **.xlsx built entirely in RAM** (`core/ocr_excel.py`),
never rows in the clear. All cells are TEXT so leading zeros in account/mobile
numbers survive. The CSP parses that .xlsx **in memory** (`xlsx_bytes_to_rows`,
never written to disk) straight into the SAME review gate a bank Excel upload
uses. Nothing is written to disk on EITHER side; the CSP review is table-only
(no source page image is persisted).

**Concurrency.** A `BoundedSemaphore(SERVER_OCR_MAX_CONCURRENCY, default 2)`
caps simultaneous OCR jobs. At capacity the endpoint returns **503 `ocr_busy`**
(non-blocking — no thread/RAM pile-up); the client backs off and falls back to
local OCR. No request is ever lost.

**Resilience.** The CSP client (`core/server_ocr_client.py`) retries transient
failures (network, 5xx, `ocr_busy`) `SERVER_OCR_RETRIES` times with backoff,
reusing one `request_id` (server is stateless, so retry is safe), then falls
back to local OCR. Any failure path still lands the CSP at the same review gate.

**Persistence = metrics only.** `ocr_metrics` (`request_id, csp_id, file_type,
page_count, row_count, latency_ms, status, error_class, created_at`) — no
filename, image, text, or customer identifier, ever (asserted by tests). Viewed
at the admin **OCR Log** page (`/ocr-log`).

**Security hardening.** API-key compare is constant-time (`hmac.compare_digest`)
on all three endpoints. Auth is checked BEFORE decrypt; decrypt happens BEFORE
an OCR slot is taken. Request size is capped at `SERVER_OCR_MAX_MB` (default 100).

**Feature flag.** Off by default: `SERVER_OCR_ENABLED=0`, and the client refuses
to run with the demo key. Flip `SERVER_OCR_ENABLED=1` + set `ADMIN_API_BASE/
ADMIN_CSP_ID/ADMIN_API_KEY` on the CSP to enable, per CSP.

**Python 3.11 (CSP) ↔ 3.12.3 (server).** The only things crossing the wire are
JSON + AES-GCM bytes + a standard .xlsx — no pickles, no version-specific
formats — so the two interpreters never need to match.

**Still open (not code):** the DPA / consent chain (go-live blocker), and a real
RapidOCR-vs-OnnxTR benchmark on consented real scans. Tier-2 (TEE + attestation
+ split auth/encryption keys) remains a future upgrade of the same contract.
