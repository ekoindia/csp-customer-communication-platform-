# CSP Communication Platform — Project Overview & Where AI Voice-Calling Fits

*A shareable overview for an AI voice-calling service provider: what our project
is, exactly **where** an AI-calling channel is needed, and the **boundaries** it
must operate within. Contains no customer data, credentials, or internal server
addresses.*

*Status: WhatsApp + SMS notification is live today. The voice-calling channel is
**proposed / not yet built** — this document defines its scope.*

---

## 1. Project overview

### The problem
A **CSP (Customer Service Point)** — a bank's local banking correspondent in
rural India — receives lists of customers who have an **account issue** (e.g. an
**inoperative / dormant account**) and must tell each customer to visit the CSP
to fix it. Traditionally the CSP calls every customer **manually, one by one** —
slow, inconsistent, and untracked.

### What the platform does
It automates that outreach while keeping the CSP in control and the data local:

1. **Ingest** the bank's customer list (usually a **mobile-phone scan/PDF**; the
   platform reads it on-device with OCR, or a digital file if available).
2. **Human review gate** — every extracted row is shown next to the source; the
   operator confirms before anything is created. Data is captured faithfully.
3. **Generate a fixed, template message** in the **CSP's own name** — no AI
   wording, no financial details, no account numbers.
4. **Send a one-way notification** and track delivery + the customer's visit.

### Architecture (high level)
- **On-premise** on the CSP's own Windows PC — all customer data stays local.
- **Channels:** WhatsApp (primary) → **SMS fallback** → **escalate to a manual
  visit/call** if both fail.
- **Two-level tracking:** automatic delivery status + the CSP's manual
  case-progress (visited → done → closed).
- **Tech:** local database, on-device OCR, template engine (no LLM), local
  WhatsApp bridge, SMS via a compliant Indian provider.

### Non-negotiable principles (these shape any new channel)
- **On-premise / data localization** — customer PII never leaves the CSP PC to
  any cloud; it is encrypted at rest and **purged when the case closes**.
- **Two interaction models by channel:**
  - **WhatsApp & SMS — strictly one-way.** Text notifications only; no reply is
    received or processed.
  - **AI voice call — conversational (two-way), within the project boundary.**
    The AI **may talk *with* the customer** to convey the account-issue message
    and answer basic "when/where to visit the CSP" questions — but strictly
    inside the limits in §4 (no financial/account details, no OTP/PIN, no
    transactions, minimal data, DPDP-compliant).
- **Minimal data to any third party** — our SMS provider, for example, gets only
  a phone number + a generic message. Nothing else.
- **CSP-name-only** messaging; **no financial data / no OTP/PIN** ever.
- Compliant with **India's DPDP Act 2023** and banking/telecom (RBI, DLT/TRAI).

---

## 2. How customers are reached today — and the gap

```
WhatsApp  →  (if it fails)  SMS  →  (if that fails)  escalate to a manual visit/call
```

**The reach gap:** the target audience is **rural, low-income, often
low-literacy** customers (migrant workers, seasonal farmers, elderly). In this
segment:
- Many have **no smartphone / no WhatsApp / no data** → WhatsApp doesn't reach them.
- Many **don't read SMS** (literacy, or simply ignore texts).
- So a meaningful share of customers end up in the **"not reached / escalated"**
  bucket — and the CSP is back to **calling them by hand**, the exact manual work
  we set out to remove.

---

## 3. WHERE an AI voice-calling channel is needed

**Precisely for the "not reached / escalated" segment** — the customers WhatsApp
and SMS cannot reach. An automated **voice call in Hindi** is the natural fit
because:
- A **voice call reaches feature phones and non-readers** — no app, no data, no
  reading required.
- **Rural customers respond better to a spoken message** in their language than
  to a text.
- It **automates the CSP's manual calling** — the last piece still done by hand
  today — so no customer is left to a slow one-by-one phone effort.

### Where it plugs into the flow
```
WhatsApp  →  SMS  →  AI VOICE CALL (this vendor)  →  escalate to a physical visit only if the call also fails
```
- Primary role: a **third fallback channel** after WhatsApp and SMS.
- Optional: a **parallel reminder** for high-priority cases.
- Content: the AI **converses with the customer (two-way)** — it explains that
  their bank account has an issue and asks them to **visit the CSP**, and can
  answer basic questions like *when / where to visit*. The conversation must stay
  **within the project boundary** (§4): it never reveals account/financial
  details, never collects OTP/PIN/sensitive info, and never does transactions —
  its only goal is to get the customer to visit the CSP.

---

## 4. The BOUNDARY the AI-calling channel must stay within

These are hard limits from DPDP / RBI / our bank authorization — not preferences.

1. **Conversational (two-way) is allowed — within these limits.** The AI may hold
   a real back-and-forth with the customer to convey the account-issue message
   and answer basic "when/where to visit the CSP" questions. But the conversation
   **must not**: **(a)** disclose any **account or financial detail** (balance,
   account number, transactions); **(b)** collect any **sensitive input**
   (OTP / PIN / password / payment / verification); **(c)** perform any
   **transaction, authentication, or account servicing**; **(d)** stray off its
   purpose (getting the customer to visit the CSP). *(WhatsApp & SMS remain
   strictly one-way, no reply.)* Because the AI listens to the customer,
   recording/consent + data rules apply — see §4.3 and §4.7.
2. **Minimal data.** The vendor receives **only the mobile number + the fixed
   message/TTS text**. **No** name (unless separately justified & agreed), **no**
   account number, balance, address, or any bank/customer identifier.
3. **India-resident processing** as our **data processor** under a signed **Data
   Processing Agreement**; **no retention** of numbers or audio after the call;
   no use of the data for the vendor's own purposes or model training.
4. **No financial data, no OTP/PIN/password** in any call.
5. **CSP-name-only** branding; our operator's and the bank's names are not
   introduced beyond what we authorize.
6. **Telecom compliance:** DLT/TRAI-registered voice sending, DND/NDNC scrubbing,
   permitted calling hours, consent handling.
7. **Status callbacks only** — we accept `answered / no-answer / busy / failed`;
   we do **not** want or store any recording or the content of anything the
   customer says. If the AI voice listens to the customer at all (to sound
   conversational), the vendor must obtain any legally-required **recording/AI
   consent**, keep that audio **only for the call**, and **never** return its
   content to us or reuse it.

### Simple interface we envisage
```
Us → Vendor : { mobile_number, message_text|template_id, language: "hi" }   (per call)
Vendor → Us : { call_id, status, timestamp }                                (webhook)
```
Same minimal, status-only philosophy as our existing SMS integration.

---

## 5. What we need from the vendor (evaluation checklist)

- One-way **TTS / recorded** voice calls in **Hindi** (rural-friendly) — **no**
  conversational requirement.
- **DPDP-compliant, India-resident** handling; **DPA**; documented **no-retention**.
- **DLT/TRAI + DND** compliance for voice (handled by vendor, or clearly on us).
- **Delivery/answer status** webhook; no reply/recording data returned.
- **Per-call pricing**, throughput/limits, answer-rate reliability.
- **Security** (encrypted transport, access control, audit); no data used for training.
- Disclosure of **sub-processors and cloud regions**.

## 6. Out of scope (guardrails)
- The AI conversation is allowed but **bounded to one purpose** — the account
  issue and getting the customer to **visit the CSP**. It is **not** a general
  support line, grievance desk, or account-servicing channel.
- Not a channel that collects OTP / PIN / password / payment / verification or
  any sensitive input.
- Not a transaction / authentication line, and it **never discloses account or
  financial details** on the call.
- Not permitted to receive customer data beyond the mobile number + the message/
  script context (customer name only if separately justified and agreed).

---

*Prepared as a project overview for vendor evaluation. Any engagement is subject
to a Data Processing Agreement and the boundaries in §4.*
