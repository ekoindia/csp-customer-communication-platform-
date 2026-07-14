-- ============================================================
-- CSP Platform — SQLite Schema
-- Single source of truth. All data stays local.
-- ============================================================

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- Reference / config tables
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS campaigns (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS branches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    csp_name    TEXT NOT NULL,
    csp_phone   TEXT NOT NULL,
    csp_address TEXT NOT NULL,
    branch_code TEXT
);

CREATE TABLE IF NOT EXISTS templates (
    id          TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    channel     TEXT NOT NULL CHECK(channel IN ('whatsapp', 'sms')),
    body        TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE IF NOT EXISTS configuration (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- ------------------------------------------------------------
-- Document processing
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT NOT NULL UNIQUE,
    campaign_id     TEXT NOT NULL,
    original_name   TEXT NOT NULL,
    file_format     TEXT NOT NULL,
    total_rows      INTEGER NOT NULL DEFAULT 0,
    valid_rows      INTEGER NOT NULL DEFAULT 0,
    invalid_rows    INTEGER NOT NULL DEFAULT 0,
    uploaded_at     TEXT NOT NULL,
    processed_at    TEXT,
    status          TEXT NOT NULL DEFAULT 'uploaded'
                    CHECK(status IN ('uploaded','processing','done','failed')),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

-- PII-AT-REST: name, mobile, account_number, father_name, address are stored
-- ENCRYPTED (see core/crypto.py) — never plain text on disk, so a CSP's local
-- SQLite file is not human-readable if inspected directly (e.g. an RBI on-site
-- visit). Encryption/decryption happens transparently in database/queries.py;
-- every other module keeps reading/writing these as plain strings.
-- account_number_hash is a separate, deterministic, one-way blind index
-- (HMAC-SHA256) used ONLY for exact-match account dedup — it survives even
-- after a case's PII is purged (see pii_purged_at) since it cannot be reversed
-- back into the account number.
-- village/taluka/balance_band are NOT encrypted — alone they don't identify a
-- specific customer, and reporting/category bars need them queryable.
CREATE TABLE IF NOT EXISTS customer_cases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id             TEXT NOT NULL UNIQUE,
    batch_id            TEXT NOT NULL,
    campaign_id         TEXT NOT NULL,
    account_number      TEXT,               -- encrypted; NULL after purge
    account_number_hash TEXT,               -- one-way dedup index; kept after purge
    name                TEXT,               -- encrypted; NULL after purge
    mobile              TEXT,               -- encrypted; NULL after purge
    father_name         TEXT,               -- encrypted; NULL after purge
    balance_band        TEXT NOT NULL,
    village             TEXT,
    taluka              TEXT,
    address             TEXT,               -- encrypted; NULL after purge
    band_label          TEXT NOT NULL,
    tone                TEXT NOT NULL,
    template_id         TEXT NOT NULL,
    is_sensitive        INTEGER NOT NULL DEFAULT 0,
    pii_purged_at       TEXT,               -- set once name/mobile/etc are wiped
    created_at          TEXT NOT NULL,
    FOREIGN KEY (batch_id)    REFERENCES documents(batch_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

CREATE INDEX IF NOT EXISTS idx_cases_account_hash
    ON customer_cases(account_number_hash);

-- ------------------------------------------------------------
-- Message engine
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         TEXT NOT NULL UNIQUE,
    wa_message      TEXT NOT NULL,
    sms_message     TEXT NOT NULL,
    template_id     TEXT NOT NULL,
    generated_at    TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES customer_cases(case_id)
);

-- ------------------------------------------------------------
-- Communication layer
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS communication_attempts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id             TEXT NOT NULL,
    channel             TEXT NOT NULL CHECK(channel IN ('whatsapp','sms')),
    status              TEXT NOT NULL
                        CHECK(status IN (
                            'pending','wa_attempted','wa_delivered','wa_read',
                            'wa_failed','sms_sent','sms_delivered','sms_failed',
                            'escalated'
                        )),
    provider_message_id TEXT,
    sent_at             TEXT,
    updated_at          TEXT NOT NULL,
    error_detail        TEXT,
    FOREIGN KEY (case_id) REFERENCES customer_cases(case_id)
);

CREATE INDEX IF NOT EXISTS idx_comm_provider_msg
    ON communication_attempts(provider_message_id);

-- ------------------------------------------------------------
-- Business tracking  (manual — CSP clicks)
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS business_tracking (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN (
                        'pending',
                        'customer_not_visited',
                        'customer_visited_in_progress',
                        'process_completed',
                        'case_closed'
                    )),
    is_escalated    INTEGER NOT NULL DEFAULT 0,
    message_sent_at TEXT,
    visited_at      TEXT,
    closed_at       TEXT,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES customer_cases(case_id)
);

-- ------------------------------------------------------------
-- Security
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    login_id    TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'csp_operator',
    created_at  TEXT NOT NULL,
    last_login  TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    action      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
