import os

from dotenv import load_dotenv

# Loads variables from a local .env file (never committed — see .env.example)
# into the process environment, so every os.environ.get() below can be set
# there instead of hardcoded here or exported by hand each terminal session.
# The path is resolved ABSOLUTELY (next to this config.py) rather than relying
# on the current working directory, so the same .env loads whether the CSP app
# runs from csp_dashboard/ or the admin portal imports `config` from the
# sibling admin_dashboard/ folder. Safe no-op if .env doesn't exist yet.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# CSP identity — these are ONLY fallback placeholders / dev-test seed values.
# The REAL values are entered by the CSP on the first-run onboarding screen and
# stored in the branches table (settings.get_csp_settings() reads DB-first). On a
# production install no branch is seeded, so these never reach a real message —
# onboarding always sets them first. Kept env-overridable so nothing CSP-specific
# is hardcoded in the shipped repo.
CSP_NAME = os.environ.get("CSP_NAME", "Demo CSP")
CSP_PHONE = os.environ.get("CSP_PHONE", "0000000000")
CSP_ADDRESS = os.environ.get("CSP_ADDRESS", "Not set")

# Public GitHub repo the app installs/updates FROM. UPDATE.bat pulls the latest
# code straight from here (no zip to build/host/send) — Eko just `git push`.
GITHUB_APP_ZIP_URL = os.environ.get(
    "GITHUB_APP_ZIP_URL",
    "https://github.com/ekoindia/csp-customer-communication-platform-/archive/refs/heads/main.zip",
)

# Dev/test seed login only (used when SEED_DEFAULT_USER is on). Production seeds
# NO default login — the CSP sets their own ID + password during onboarding.
LOGIN_ID = os.environ.get("LOGIN_ID", "CSP001")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD", "changeme")

# First-run onboarding vs. a pre-seeded default operator.
#   Real installs: FALSE  → no default login is seeded; on first launch the CSP
#                           goes through the onboarding screen (before login) and
#                           sets their OWN login ID + password + branch details.
#   Tests/dev:     TRUE   → seed the CSP001/changeme operator and mark onboarding
#                           complete, so the suite can log in without the wizard
#                           (conftest sets this).
SEED_DEFAULT_USER = os.environ.get("SEED_DEFAULT_USER", "0") == "1"

# Language of the OUTGOING customer message (WhatsApp + SMS): "hi" (default,
# best for rural SBI customers) or "en". Same finalised wording, translated.
MESSAGE_LANGUAGE = "hi"

WA_SERVER_URL = "http://localhost:3000"
WA_DAILY_LIMIT = 200
WA_DELAY_SECONDS = 12

# Optional shared secret for inbound webhooks. If set (non-empty), inbound
# webhook requests must carry header  X-Webhook-Token: <this value>.
# Leave empty to rely on localhost-only binding.
WEBHOOK_TOKEN = ""

MSG91_AUTH_KEY = ""
MSG91_SENDER_ID = ""
MSG91_TEMPLATE_ID = ""

# OCR engine for scanned documents.
#   "auto"      = pick by hardware (see core/hardware.py): docTR on a big box
#                 with RAM free; otherwise ONNXTR on the 4 GB CPU box (accurate,
#                 no PyTorch) when its bundled models are present, else Tesseract.
#   "doctr"     = force docTR (deep learning, ~1 GB resident) always.
#   "onnxtr"    = force OnnxTR: docTR's models on ONNX Runtime (~700 MB, NO
#                 PyTorch). The ACCURATE engine for the 4 GB CPU-only CSP box —
#                 detection finds every table row and reads names; account/mobile
#                 digits still come from the custom crnn.onnx. Models bundled in
#                 core/models/ (offline, DPDP-safe). Feeds the SAME grid logic.
#   "tesseract" = force the light Tesseract-only reader (~150 MB, no PyTorch) —
#                 last-resort fallback; under-reads dense scanned tables.
#   "paddle"    = PaddleOCR (kept as a future numpy-1 option; not installed).
# All are fully local/on-premise (DPDP-safe, no cloud). The CSV/Excel/typed-PDF
# paths never touch any of these — they use no OCR at all. Numeric cells
# (account / mobile) are always cross-checked with a digit-whitelisted Tesseract
# re-read regardless of engine.
#
# NOTE: PaddleOCR 2.7.x is NOT compatible with this project's numpy 2.x stack,
# so "paddle" needs a dedicated numpy-1 environment. "auto" never selects it.
#
# The shipped default is "auto" so the real 4 GB deploy PC is never forced onto
# docTR (which would OOM it). For DEV testing on a capable box you can pin an
# engine for a session WITHOUT editing this file — set the CSP_OCR_ENGINE env
# var (e.g. CSP_OCR_ENGINE=doctr) before launching. Unset = "auto" as before.
OCR_ENGINE = os.environ.get("CSP_OCR_ENGINE", "auto").lower()

# Below this much TOTAL RAM, "auto" mode drops docTR (PyTorch ~1 GB) for the
# Tesseract-only reader so a 4 GB deployment PC doesn't swap/OOM. The real
# deploy target (Dell Inspiron 3268) has 4 GB, so it lands on Tesseract-only.
OCR_RAM_THRESHOLD_GB = 6

# Safety valve: even on a machine that clears the total-RAM bar, "auto" mode
# will NOT load docTR unless at least this much RAM is FREE right now — so a
# bigger box under memory pressure also falls back to the light path instead of
# OOMing. Checked per page at OCR time.
DOCTR_MIN_FREE_RAM_GB = 2.5

# Cap OCR CPU threads so a 2-core/4-thread i3 isn't oversubscribed. On the 4 GB
# CSP box this stays 4. The centralized OCR SERVER (40 vCPU) overrides this via
# the TORCH_MAX_THREADS env (set in deploy/restart_admin.sh) so onnxruntime uses
# many cores per page instead of 4 — the difference between ~34 s/page and a few
# seconds on the shared box.
TORCH_MAX_THREADS = int(os.environ.get("TORCH_MAX_THREADS", "4"))

# docTR recognition backbone.
#   "auto"          = "parseq" on a GPU (accurate, practical only with CUDA),
#                     else "crnn_vgg16_bn" (light + much faster on CPU).
#   "parseq" / "master" / "crnn_vgg16_bn" = force that backbone.
# docTR auto-runs on the GPU when CUDA is available and falls back to CPU
# otherwise — same code on the dev RTX 4060 box and the CPU-only deploy PC.
DOCTR_RECO_ARCH = "auto"

# OnnxTR model files (used when OCR_ENGINE resolves to "onnxtr"). Empty = use the
# ones bundled in core/models/ (db_mobilenet_v3_large.onnx + crnn_mobilenet_v3_
# small.onnx). Override only to point at a different local ONNX file — never a
# URL (models must stay local for DPDP: no runtime download).
ONNXTR_DET_PATH = os.environ.get("ONNXTR_DET_PATH", "")
ONNXTR_RECO_PATH = os.environ.get("ONNXTR_RECO_PATH", "")

# Scanned-PDF render DPI (peak OCR RAM grows with DPI^2). "auto" = 300 on a
# capable box, OCR_LOW_RAM_DPI on a 4 GB box / when little RAM is free (so a
# full-page render doesn't push the deploy PC into swap). Set an int here to pin
# it (e.g. 300) if a particular scan needs more sharpness and the box can spare
# the RAM. See core/hardware.render_dpi.
OCR_RENDER_DPI = "auto"
OCR_LOW_RAM_DPI = 260   # printed tables read better at 260 than 220; still fits 4 GB
OCR_HIGH_RAM_DPI = 300

# Centralized OCR (Eko RAG/admin server, Tier 1).
# Disabled by default until the DPA/go-live switch is ready. When enabled, the
# CSP sends scanned PDFs/images to {ADMIN_API_BASE}/ocr/extract using the same
# per-CSP API key, with an AES-GCM app-layer envelope. The server runs OCR fully
# in RAM and returns an encrypted .xlsx (never rows in the clear, never a file on
# disk on either side); the CSP parses that .xlsx in memory into the SAME review
# gate as a bank Excel upload. Cases/messages always remain local.
SERVER_OCR_ENABLED = os.environ.get("SERVER_OCR_ENABLED", "0") == "1"
# Default engine = onnxtr: the ONLY engine with MEASURED accuracy on the real
# SBI scans (account 100% / name 99% / band 95% / mobile 85% across a 29-page
# scan). rapidocr (PP-OCR on ONNX) is kept as a selectable challenger but must
# beat onnxtr on a real benchmark before it earns the default (scripts/ocr_benchmark.py).
SERVER_OCR_ENGINE = os.environ.get("SERVER_OCR_ENGINE", "onnxtr").lower()
SERVER_OCR_TIMEOUT_SEC = int(os.environ.get("SERVER_OCR_TIMEOUT_SEC", "900"))
SERVER_OCR_MAX_MB = int(os.environ.get("SERVER_OCR_MAX_MB", "100"))
SERVER_OCR_RENDER_DPI = int(os.environ.get("SERVER_OCR_RENDER_DPI", "300"))
# Max simultaneous OCR jobs on the server. Raised to exploit the 40-vCPU box:
# the client sends pages in parallel (SERVER_OCR_PARALLEL) and each job now uses
# fewer threads (TORCH_MAX_THREADS set in deploy/restart_admin.sh) so several
# pages OCR at once across the cores instead of one page hogging 16 threads.
SERVER_OCR_MAX_CONCURRENCY = int(os.environ.get("SERVER_OCR_MAX_CONCURRENCY", "8"))
# CSP renders + sends this many PDF pages CONCURRENTLY (in waves) so a multi-page
# scan finishes in wall-clock ~= pages/parallel instead of pages x per-page time.
# Must be <= the server's SERVER_OCR_MAX_CONCURRENCY. Memory-safe: only this many
# rendered page images are held at once (fine on the 4 GB box).
SERVER_OCR_PARALLEL = int(os.environ.get("SERVER_OCR_PARALLEL", "6"))
# How long a client waits for an OCR slot on the server before giving up (and
# falling back to local OCR). Separate from the OCR compute timeout above.
SERVER_OCR_QUEUE_WAIT_SEC = int(os.environ.get("SERVER_OCR_QUEUE_WAIT_SEC", "20"))
# CSP-side retries for transient network / 5xx failures before falling back to
# local OCR. Kept small — the local fallback is always there.
SERVER_OCR_RETRIES = int(os.environ.get("SERVER_OCR_RETRIES", "2"))

# ── OnnxTR "heavy" arches — the accurate engine for the CENTRALIZED SERVER ────
# The server (Dell PowerEdge R730: 40 vCPU, 125 GiB RAM, NO GPU) has compute to
# spare, so it runs docTR's accuracy-leading arches instead of the small bundled
# ones the 4 GB CSP box uses. Weights are fetched by OnnxTR on first use and
# cached on disk — a MODEL download, never customer data, so DPDP is unaffected.
# The centralized OCR path forces this on automatically; the 4 GB box keeps the
# small bundled models (OCR_ONNXTR_HEAVY stays 0 there). VLM-class OCR is NOT
# used: no GPU, so it would be minutes per page.
OCR_ONNXTR_HEAVY = os.environ.get("OCR_ONNXTR_HEAVY", "0") == "1"
ONNXTR_DET_ARCH = os.environ.get("ONNXTR_DET_ARCH", "db_resnet50")    # detection
ONNXTR_RECO_ARCH = os.environ.get("ONNXTR_RECO_ARCH", "parseq")       # recognition

# ── Minimum hardware the platform supports (the "hardware constraint") ───────
# Single source of truth, used by the install-time gate (INSTALL.bat) and the
# deploy preflight (deploy_check.py). The CONFIRMED deploy PC — Dell Inspiron
# 3268: 4 GB RAM, i3-7100, no GPU, Windows 10 x64 — is the FLOOR the software
# targets; it runs there in light Tesseract-only OCR (no PyTorch/docTR).
#   • RAM reports low on these boxes (~3.8 GB for a 4 GB PC) because the Intel
#     iGPU reserves shared memory, so the HARD floor is 3.0, not 4.0 — a stricter
#     floor would false-block a machine the app actually runs on.
MIN_RAM_HARD_GB = 3.0        # below this: NO-GO (can't run reliably) -> install blocks
MIN_RAM_RECOMMENDED_GB = 4.0 # below this: WARN (runs, light OCR mode, close other apps)
MIN_FREE_DISK_GB = 3.0       # install needs ~3 GB (app + Python/Node/Tesseract deps)
MIN_FREE_RAM_GB = 0.8        # free RAM to start a batch without swapping
MIN_OS = "Windows 10 (64-bit)"

DB_PATH = "database/csp_platform.db"

# ── Admin-portal reporting (CSP -> Eko) ─────────────────────────────────────
# The local CSP app PUSHES a small, PII-FREE heartbeat + status to Eko's admin
# portal (Eko can't reach into a CSP's local PC, so the CSP reports outbound).
# What is sent is strictly allow-listed in core/admin_reporter.py: this install's
# opaque id, app version, WhatsApp connected/banned flags, AGGREGATE campaign
# progress counts, earnings, and audit EVENT TYPES. NEVER any customer PII.
ADMIN_REPORT_ENABLED = os.environ.get("ADMIN_REPORT_ENABLED", "0") == "1"
# ONE Eko API base (lives on Eko's server with the admin portal). Every CSP
# install connects to this single API: it POSTs status to {base}/report and
# polls {base}/sync for server-side info (latest version, config, commands).
#
# GO-LIVE (Eko, ONE-TIME, before building the production CSP_Platform.zip):
# change the fallback string below to the real RAG-server URL, e.g.
#   ADMIN_API_BASE = os.environ.get("ADMIN_API_BASE", "https://admin.eko.co.in/api/v1")
# Every CSP that installs THAT build then defaults to the right server with
# zero action on their part — this value is the SAME for all 523 CSPs, so it
# is baked in here, not asked from the CSP (only CSP_ID + API_KEY are
# per-install and are asked — see INSTALL.bat's "Connect to Eko Admin Portal"
# step, or the dashboard's /admin-connect screen). The endpoint PATHS
# (/report, /sync) never change, so nothing else about the API moves.
ADMIN_API_BASE = os.environ.get("ADMIN_API_BASE", "http://122.176.147.78:8080/csp-admin/api/v1")
ADMIN_CSP_ID = os.environ.get("ADMIN_CSP_ID", "CSP001")   # this install's opaque id
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "demo-key-CSP001")  # per-CSP key
ADMIN_REPORT_INTERVAL_SEC = 120                    # heartbeat/sync cadence (2 min = admin reflects changes fast)


def _read_version() -> str:
    # APP_VERSION lives in the VERSION file (NOT hard-coded here), because an
    # auto-update PRESERVES config.py but OVERWRITES VERSION — so the version
    # advances after an update instead of being stuck at whatever config.py had.
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip() or "1.0.0"
    except Exception:
        return "1.0.0"


APP_VERSION = _read_version()

# ── Admin-portal SERVER binding (used only on Eko's server, by admin_dashboard/app.py)
# Local demo binds 127.0.0.1:7000. On the real server, set ADMIN_BIND_HOST=0.0.0.0
# (behind an HTTPS reverse proxy / the RAG server's own TLS) — no code change.
ADMIN_BIND_HOST = os.environ.get("ADMIN_BIND_HOST", "127.0.0.1")
ADMIN_BIND_PORT = int(os.environ.get("ADMIN_BIND_PORT", "7000"))

UPLOAD_FOLDER = "uploads"
MAX_UPLOAD_MB = 100
MAX_BATCH_FILES = 20

# ── RAG credentials (Eko's "RAG server") ────────────────────────────────────
# Fill these in .env (not here) — see .env / .env.example. Not wired into any
# code path yet; reserved for the Eko RAG-server integration.
RAG_SERVER_HOST_IP = os.environ.get("rag_server_host_ip", "")
RAG_SERVER_PORT = os.environ.get("rag_server_port", "")
RAG_SERVER_PASS = os.environ.get("rag_server_pass", "")
