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
const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
} = require("@whiskeysockets/baileys");
const { Boom } = require("@hapi/boom");

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

// Pending sends keyed by our own request id, so we can resolve/reject them
// once Baileys confirms the message left (or failed).
async function connectWhatsApp() {
    if (connecting) return sock;
    connecting = true;
    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

    sock = makeWASocket({
        auth: state,
        printQRInTerminal: false,
        // A real browser identity string — some networks reject the default.
        browser: ["CSP Platform", "Chrome", "1.0"],
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
                // Transient drop (network, phone offline) — reconnect automatically.
                console.log("WhatsApp connection dropped — reconnecting.");
                setTimeout(connectWhatsApp, 3000);
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
    try {
        const sent = await sock.sendMessage(jid, { text: message });
        return res.json({ success: true, message_id: sent.key.id });
    } catch (err) {
        return res.status(500).json({ success: false, error: err.message });
    }
});

// GET /status
app.get("/status", (req, res) => {
    res.json({ ready: isReady, has_qr: Boolean(lastQrDataUrl) });
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
