-- ============================================================
-- Eko Admin Portal — schema (lives on Eko's server, NOT the CSP PC)
-- Holds ONLY allow-listed, PII-FREE data reported by CSP installs.
--
-- HARD RULE: there are deliberately NO columns for customer name / mobile /
-- account number / father name / address / village-as-identifier / message
-- text / case id — not even in masked form. None of that is ever received or
-- stored. Everything below is either operational (about the CSP MACHINE /
-- INSTALL) or an AGGREGATE COUNT (about a campaign), never about a person.
-- ============================================================

-- Per-CSP API keys the admin issues; a CSP install authenticates with these.
CREATE TABLE IF NOT EXISTS api_keys (
    csp_id      TEXT PRIMARY KEY,
    api_key     TEXT NOT NULL,
    name        TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

-- One row per CSP install (the fleet). last_seen drives online/offline.
-- The hardware_* / os_* / ocr_engine columns describe the CSP's MACHINE
-- (not any customer) — used to confirm the deploy PC can run the software
-- and which OCR profile it landed on.
CREATE TABLE IF NOT EXISTS csps (
    csp_id              TEXT PRIMARY KEY,
    name                TEXT,               -- CSP shop/branch name (public), not a person
    version             TEXT,               -- installed app version
    install_id          TEXT,               -- opaque per-install id
    whatsapp_connected  INTEGER NOT NULL DEFAULT 0,
    whatsapp_banned     INTEGER NOT NULL DEFAULT 0,
    -- CSP machine hardware profile (operational, not PII) -----------------
    hw_ram_gb           REAL,
    hw_available_gb     REAL,
    hw_cpu_threads      INTEGER,
    hw_gpu              INTEGER NOT NULL DEFAULT 0,   -- 1 if an NVIDIA GPU is present
    os_name             TEXT,
    ocr_engine          TEXT,               -- resolved engine: doctr | tesseract
    dxdiag              TEXT,               -- full Windows DxDiag machine report (machine info, not PII)
    first_seen          TEXT,
    last_seen           TEXT
);

-- Aggregate campaign progress per (CSP, campaign, month). COUNTS ONLY.
-- Split into the same two tracking systems the CSP dashboard shows:
--   (a) message/communication tracking  -> wa_* / sms_* / escalated
--   (b) physical-visit (business) tracking -> visit_* + visited/closed
-- Nothing here identifies a customer; each field is "how many cases are in
-- state X", never "which customer".
CREATE TABLE IF NOT EXISTS progress (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    csp_id          TEXT NOT NULL,
    campaign_id     TEXT,
    month           TEXT,
    -- headline counts --------------------------------------------------------
    total           INTEGER NOT NULL DEFAULT 0,   -- cases in this campaign/month
    reached         INTEGER NOT NULL DEFAULT 0,   -- delivered/read on any channel
    failed          INTEGER NOT NULL DEFAULT 0,   -- both channels failed
    pct             REAL    NOT NULL DEFAULT 0,   -- reach rate %
    -- (a) message tracking : WhatsApp ---------------------------------------
    wa_sent         INTEGER NOT NULL DEFAULT 0,
    wa_delivered    INTEGER NOT NULL DEFAULT 0,   -- delivered (not yet read)
    wa_read         INTEGER NOT NULL DEFAULT 0,
    wa_failed       INTEGER NOT NULL DEFAULT 0,
    -- (a) message tracking : SMS fallback -----------------------------------
    sms_sent        INTEGER NOT NULL DEFAULT 0,
    sms_delivered   INTEGER NOT NULL DEFAULT 0,
    sms_failed      INTEGER NOT NULL DEFAULT 0,
    escalated       INTEGER NOT NULL DEFAULT 0,   -- both failed -> manual visit
    -- (b) physical-visit (business) tracking --------------------------------
    visit_pending   INTEGER NOT NULL DEFAULT 0,   -- message sent, not visited yet
    visited         INTEGER NOT NULL DEFAULT 0,   -- customer came (has visited_at)
    in_progress     INTEGER NOT NULL DEFAULT 0,   -- visit started
    completed       INTEGER NOT NULL DEFAULT 0,   -- account reactivated
    closed          INTEGER NOT NULL DEFAULT 0,   -- case closed
    -- commission (left as-is; formula deferred / EDR-1) ---------------------
    earnings        REAL    NOT NULL DEFAULT 0,
    updated_at      TEXT,
    UNIQUE(csp_id, campaign_id, month)
);

-- Per-balance-band rollup (category bars). Band is a CATEGORY, not a person;
-- "how many cases in band 100<1000 were reached" reveals nothing identifying.
CREATE TABLE IF NOT EXISTS progress_bands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    csp_id      TEXT NOT NULL,
    campaign_id TEXT,
    month       TEXT,
    band        TEXT NOT NULL,
    total       INTEGER NOT NULL DEFAULT 0,
    reached     INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT,
    UNIQUE(csp_id, campaign_id, month, band)
);

-- Operational audit EVENTS only (type + timestamp). No customer reference.
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    csp_id      TEXT NOT NULL,
    type        TEXT NOT NULL,
    ts          TEXT NOT NULL
);

-- Software update history per CSP. A row is added whenever a CSP's reported app
-- version CHANGES between heartbeats (i.e. it ran the update and got a new build)
-- so the admin can see how many times and when each CSP updated. Software-only
-- info (versions + timestamps), no customer/campaign data.
CREATE TABLE IF NOT EXISTS update_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    csp_id       TEXT NOT NULL,
    from_version TEXT,
    to_version   TEXT,
    ts           TEXT NOT NULL
);

-- Centralized OCR operational metrics. CONTENT MUST NEVER BE STORED HERE:
-- no filenames, no extracted text, no image bytes, no customer identifiers.
CREATE TABLE IF NOT EXISTS ocr_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,
    csp_id          TEXT NOT NULL,
    file_type       TEXT,
    page_count      INTEGER NOT NULL DEFAULT 0,
    row_count       INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,              -- ok | error
    error_class     TEXT,
    created_at      TEXT NOT NULL
);

-- Server-set config the CSPs read via /sync (e.g. latest_version for updates).
CREATE TABLE IF NOT EXISTS server_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

-- Commands the admin queues for a CSP; delivered when that CSP next polls /sync
-- (Eko cannot push to a local CSP PC, so delivery is pull-based).
CREATE TABLE IF NOT EXISTS commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    csp_id       TEXT NOT NULL,
    command      TEXT NOT NULL,
    payload      TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | delivered
    created_at   TEXT NOT NULL,
    delivered_at TEXT
);

-- Admin portal login users.
CREATE TABLE IF NOT EXISTS admin_users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    login_id    TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'admin',
    created_at  TEXT NOT NULL
);

-- Uploaded install/update packages the admin distributes to CSPs. The file
-- itself lives on disk under admin_portal/releases/ (stored_name = the actual
-- on-disk filename, uuid-prefixed to avoid collisions); this table is the
-- index + integrity hash.
--   kind='install' -> the package a brand-new CSP downloads via CSP_Setup.bat
--                      (no API key exists yet, so distribution must be public).
--   kind='update'  -> a published software update; also mirrored into
--                      server_config (latest_version/update_url/update_sha256)
--                      so the existing /api/v1/sync path serves it unchanged.
CREATE TABLE IF NOT EXISTS releases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL CHECK(kind IN ('install','update')),
    version     TEXT,
    filename    TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL
);
