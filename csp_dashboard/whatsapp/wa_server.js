/**
 * WhatsApp server — Baileys edition.
 *
 * Why Baileys instead of whatsapp-web.js: whatsapp-web.js drives a headless
 * Chromium browser to automate the WhatsApp Web page. WhatsApp regularly
 * changes that web client, and headless Chromium gets detected/out of sync —
 * this is why the QR would generate but never actually link. Baileys has no
 * browser at all: it implements the WhatsApp multi-device protocol directly
 * over a WebSocket (the same way the WhatsApp phone app talks to WhatsApp's
 * servers), so there's no Chromium version to go stale.
 *
 * The HTTP surface (POST /send, GET /status, GET /qr) is UNCHANGED — the
 * Flask app and comm_runner.py don't need to know which engine is behind it.
 */

const express = require("express");
const http = require("http");
const fs = require("fs");
const QRCode = require("qrcode");
const { Boom } = require("@hapi/boom");

/*
 * Baileys is loaded with dynamic import(), NOT require().
 *
 * Why: Baileys 6.7.20+ and 7.x ship as ES Modules, and a CommonJS require() of
 * an ESM package throws ERR_REQUIRE_ESM on Node 20 — which is what CSP machines
 * run. The last CommonJS release (6.7.18) carries a published message-spoofing
 * advisory (GHSA-qvv5-jq5g-4cgg), so pinning back to it is not acceptable on a
 * machine that messages bank customers. dynamic import() works from CommonJS on
 * every Node we ship, so we can run the PATCHED version and keep this file as-is
 * otherwise. Resolved once, lazily, before the first connect.
 */
let makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion;
let _baileysLoading = null;

function loadBaileys() {
    if (!_baileysLoading) {
        _baileysLoading = import("@whiskeysockets/baileys").then((m) => {
            // Different builds expose things either at the top level or under
            // .default, so resolve defensively instead of assuming one shape.
            const ns = (m.default && typeof m.default === "object"
                        && m.default.useMultiFileAuthState) ? m.default : m;
            makeWASocket = (typeof m.default === "function" && m.default)
                        || (typeof ns.default === "function" && ns.default)
                        || ns.makeWASocket;
            useMultiFileAuthState = ns.useMultiFileAuthState || m.useMultiFileAuthState;
            DisconnectReason = ns.DisconnectReason || m.DisconnectReason;
            // Optional: absent in some builds — we fall back to Baileys' default.
            fetchLatestBaileysVersion = ns.fetchLatestBaileysVersion
                                     || m.fetchLatestBaileysVersion
                                     || (async () => ({}));
            if (typeof makeWASocket !== "function" || !useMultiFileAuthState) {
                throw new Error("Baileys loaded but its API was not found "
                                + "(unexpected package shape)");
            }
        });
    }
    return _baileysLoading;
}

const app = express();
app.use(express.json());

const PORT = 3000;
const SESSION_DIR = ".wa_session";

// Wipe the saved WhatsApp session so the next connect starts fresh and emits a
// brand-new QR. Used when the phone unlinks the device (logged out) or when the
// dashboard asks for a fresh QR — this is what fixes "QR won't load after a
// logout / re-login".
function clearSession() {
    try { fs.rmSync(SESSION_DIR, { recursive: true, force: true }); }
    catch (e) { /* nothing to clear */ }
}

let connecting = false;   // guard against overlapping connect attempts
const FLASK_WEBHOOK_HOST = "127.0.0.1";
const FLASK_WEBHOOK_PORT = 5000;
const FLASK_WEBHOOK_PATH = "/webhook/whatsapp";
const WEBHOOK_TOKEN = process.env.WEBHOOK_TOKEN || "";

let sock = null;
let isReady = false;
let lastQrDataUrl = null;
let lastQrGeneratedAt = null;
// Reconnect backoff state. WhatsApp answers "Connection Failure" during
// registration when it is refusing the client (stale web version, or a temporary
// block from too many attempts); retrying every 3s makes that worse.
let retries = 0;
const MAX_RETRIES = 6;
let lastError = null;

// Pending sends keyed by our own request id, so we can resolve/reject them
// once Baileys confirms the message left (or failed).
async function connectWhatsApp() {
    if (connecting) return sock;
    connecting = true;
    // ESM package, resolved once (see loadBaileys). If it can't load, clear the
    // guard so a later /reset or retry isn't blocked forever by connecting=true.
    try {
        await loadBaileys();
    } catch (err) {
        connecting = false;
        _baileysLoading = null;   // allow a fresh attempt next time
        throw err;
    }
    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

    // Use the WA web build WhatsApp currently expects. With a stale version the
    // server answers "Connection Failure" during registration and NO QR is ever
    // emitted — the bridge just reconnect-loops forever (seen on a live CSP).
    let waVersion;
    try {
        const v = await fetchLatestBaileysVersion();
        waVersion = v && v.version;
        if (waVersion) console.log("WhatsApp web version:", waVersion.join("."));
    } catch (e) {
        console.log("Could not fetch the latest WhatsApp version:", e.message);
    }

    sock = makeWASocket({
        auth: state,
        printQRInTerminal: false,
        // A real browser identity string — some networks reject the default.
        browser: ["CSP Platform", "Chrome", "1.0"],
        ...(waVersion ? { version: waVersion } : {}),
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (connection === "connecting") connecting = true;
        if (connection === "open" || connection === "close") connecting = false;

        if (qr) {
            QRCode.toDataURL(qr, { width: 320, margin: 2 })
                .then((dataUrl) => {
                    lastQrDataUrl = dataUrl;
                    lastQrGeneratedAt = new Date().toISOString();
                    console.log("WhatsApp QR ready for dashboard scan.");
                })
                .catch((err) => console.error("QR image generation error:", err.message));
        }

        if (connection === "open") {
            isReady = true;
            lastQrDataUrl = null;
            lastQrGeneratedAt = null;
            retries = 0;            // healthy again — reset the backoff
            lastError = null;
            console.log("WhatsApp connected.");
        }

        if (connection === "close") {
            isReady = false;
            const statusCode = lastDisconnect?.error instanceof Boom
                ? lastDisconnect.error.output?.statusCode
                : undefined;
            const loggedOut = statusCode === DisconnectReason.loggedOut;
            if (loggedOut) {
                // The device was unlinked. Wipe the dead session and reconnect
                // so a FRESH QR is generated automatically — the CSP does not
                // have to do anything, and login/logout no longer leaves the QR
                // stuck.
                console.log("WhatsApp logged out — clearing session, generating a new QR.");
                lastQrDataUrl = null;
                clearSession();
                setTimeout(connectWhatsApp, 1000);
            } else {
                // Transient drop (network, phone offline) — reconnect, but with a
                // BACKOFF. A fixed 3s retry hammered WhatsApp's registration
                // endpoint every few seconds when it kept answering "Connection
                // Failure"; that is exactly how a number/IP gets rate-limited, and
                // the QR then never arrives. Back off, and stop after a while with
                // a clear reason instead of looping forever.
                retries += 1;
                if (retries > MAX_RETRIES) {
                    lastError = "WhatsApp refused the connection " + retries
                        + " times in a row. This is usually a temporary block from "
                        + "too many attempts — wait ~30 minutes, then press "
                        + "\"Reset & New QR\". (Check the internet connection too.)";
                    console.log(lastError);
                    return;      // stop; /reset or a restart starts over
                }
                const wait = Math.min(60000, 5000 * Math.pow(2, retries - 1));
                console.log(`WhatsApp connection dropped — retry ${retries}/${MAX_RETRIES} in ${Math.round(wait / 1000)}s.`);
                setTimeout(connectWhatsApp, wait);
            }
        }
    });

    // Delivery status: Baileys emits message status changes here.
    // WAMessageStatus: 0 ERROR, 1 PENDING, 2 SERVER_ACK (sent), 3 DELIVERY_ACK
    // (delivered), 4 READ, 5 PLAYED. We forward SERVER_ACK/DELIVERY_ACK/READ.
    sock.ev.on("messages.update", (updates) => {
        for (const { key, update } of updates) {
            if (update.status !== undefined && key?.id) {
                postAck(key.id, update.status);
            }
        }
    });

    return sock;
}

connectWhatsApp().catch((err) => console.error("WhatsApp connect error:", err.message));

function normalizeIndianMobile(mobile) {
    const digits = String(mobile).replace(/\D/g, "");
    if (digits.length === 10) return `91${digits}`;
    if (digits.length === 12 && digits.startsWith("91")) return digits;
    return null;
}

// POST /send  — body: { mobile: "9876543210", message: "..." }
app.post("/send", async (req, res) => {
    const { mobile, message } = req.body;

    if (!mobile || !message) {
        return res.status(400).json({ success: false, error: "mobile and message required" });
    }
    if (!isReady || !sock) {
        return res.status(503).json({ success: false, error: "WhatsApp not ready" });
    }

    const normalizedMobile = normalizeIndianMobile(mobile);
    if (!normalizedMobile) {
        return res.status(400).json({ success: false, error: "invalid mobile number" });
    }

    const jid = `${normalizedMobile}@s.whatsapp.net`;

    // Is this number actually ON WhatsApp? On a rural Jan Dhan list most send
    // failures are simply numbers with no WhatsApp account (or a digit misread by
    // OCR) — not a network or connection problem. Without this check every case
    // came back as a generic failure, so the CSP could not tell "this person has
    // no WhatsApp, call them / send SMS" from "retry later". We report it as a
    // distinct reason so the dashboard can say exactly that.
    try {
        const found = await sock.onWhatsApp(normalizedMobile);
        if (!found || !found.length || !found[0]?.exists) {
            return res.json({
                success: false,
                reason: "not_on_whatsapp",
                error: "This number is not on WhatsApp (or the number is wrong)",
            });
        }
    } catch (err) {
        // The check itself failed (transient) — don't block the send, just try it.
        console.error("onWhatsApp check failed:", err.message);
    }

    try {
        const sent = await sock.sendMessage(jid, { text: message });
        return res.json({ success: true, message_id: sent.key.id });
    } catch (err) {
        return res.status(500).json({ success: false, error: err.message });
    }
});

// GET /check?mobile=98XXXXXXXX — is a number on WhatsApp? Lets the CSP verify a
// corrected number BEFORE re-approving a failed case, instead of send-and-hope.
app.get("/check", async (req, res) => {
    if (!isReady || !sock) {
        return res.status(503).json({ ok: false, error: "WhatsApp not ready" });
    }
    const normalizedMobile = normalizeIndianMobile(req.query.mobile || "");
    if (!normalizedMobile) {
        return res.status(400).json({ ok: false, error: "invalid mobile number" });
    }
    try {
        const found = await sock.onWhatsApp(normalizedMobile);
        const exists = !!(found && found.length && found[0]?.exists);
        return res.json({ ok: true, on_whatsapp: exists });
    } catch (err) {
        return res.status(500).json({ ok: false, error: err.message });
    }
});

// GET /status
app.get("/status", (req, res) => {
    // Surface WHY there is no QR yet, so the dashboard can say it instead of
    // sitting on "awaiting QR scan" forever.
    res.json({ ready: isReady, has_qr: Boolean(lastQrDataUrl),
               retrying: retries > 0 && retries <= MAX_RETRIES,
               error: lastError });
});

// GET /qr  — dashboard-friendly QR image for linking the sender WhatsApp.
app.get("/qr", (req, res) => {
    res.json({
        ready: isReady,
        qr: lastQrDataUrl,
        generated_at: lastQrGeneratedAt,
    });
});

// POST /reset — force a fresh start: drop the socket, wipe the session, and
// reconnect so a new QR is generated. The dashboard calls this when the QR is
// missing/stuck so linking always works, even after a logout/re-login.
app.post("/reset", async (req, res) => {
    try {
        if (sock) { try { sock.end(new Error("reset")); } catch (e) { /* ignore */ } }
        sock = null;
        isReady = false;
        lastQrDataUrl = null;
        connecting = false;
        retries = 0;          // a manual reset starts the backoff over
        lastError = null;
        clearSession();
        connectWhatsApp().catch((err) => console.error("reset reconnect error:", err.message));
        res.json({ ok: true });
    } catch (e) {
        res.status(500).json({ ok: false, error: e.message });
    }
});

app.listen(PORT, "127.0.0.1", () => {
    console.log(`WA server (Baileys) listening on http://127.0.0.1:${PORT}`);
});

// ── Forward a delivery-status update to the Flask webhook (fire-and-forget) ──
function postAck(messageId, status) {
    const payload = JSON.stringify({ message_id: messageId, ack: status, engine: "baileys" });
    const headers = {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(payload),
    };
    if (WEBHOOK_TOKEN) headers["X-Webhook-Token"] = WEBHOOK_TOKEN;
    const options = {
        host: FLASK_WEBHOOK_HOST,
        port: FLASK_WEBHOOK_PORT,
        path: FLASK_WEBHOOK_PATH,
        method: "POST",
        headers: headers,
    };
    const req = http.request(options, (resp) => { resp.resume(); });
    req.on("error", (e) => console.error("ACK webhook error:", e.message));
    req.write(payload);
    req.end();
}
