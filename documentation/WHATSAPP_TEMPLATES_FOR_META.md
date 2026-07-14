# WhatsApp message templates — CSP Inoperative-Accounts campaign

Single source of truth for the customer messages. Two forms of the **same
finalised wording**:

- **Current bridge (free/Baileys):** plain text, filled in Python — already live
  in `campaigns/inoperative/templates.py` (`WA_TEMPLATES` / `SMS_TEMPLATES`).
  Language is chosen by `config.MESSAGE_LANGUAGE` (`hi` default, or `en`).
- **Official WhatsApp Business Cloud API:** the `{{1}}/{{2}}/{{3}}` versions
  below, registered at Meta as **Utility** templates (`WABA_TEMPLATES` in the
  same file). Submit these verbatim when Eko moves a CSP to the official route.

**Send-time parameter order (both routes):**
`{{1}}` = customer name · `{{2}}` = CSP branch name · `{{3}}` = branch address

**Compliance:** CSP name only (never "Eko Bharat"); no phone number in any
message (CSP Mukesh's safety request — branch address only); no balance /
account number / band. One-way notification, asks the customer to visit the CSP.

---

## WhatsApp Template 1 — Normal reminder (English)
- **Template name:** `account_inoperative_normal_en`  ·  **Category:** Utility  ·  **Language:** en
- Used for bands `0.1<100`, `100<1000` (internal `template_1`)
```text
Hello {{1}},

Our records indicate that your SBI bank account has remained inactive for a long time. To reactivate your account, kindly visit your nearest SBI CSP branch.

Branch:
{{2}}

Address:
{{3}}

Thank you,
{{2}}
```

## WhatsApp Template 2 — Urgent reminder (English)
- **Template name:** `account_inoperative_urgent_en`  ·  **Category:** Utility  ·  **Language:** en
- Used for bands `1000<10000`, `B>10000` (internal `template_3`)
```text
Hello {{1}},

Our records indicate that your SBI bank account has remained inactive for a long time. Kindly visit your SBI CSP branch at the earliest to complete the required process.

Branch:
{{2}}

Address:
{{3}}

Thank you,
{{2}}
```

## WhatsApp Template 1 — Normal reminder (Hindi)
- **Template name:** `account_inoperative_normal_hi`  ·  **Category:** Utility  ·  **Language:** hi
```text
नमस्ते {{1}} जी,

हमारे रिकॉर्ड के अनुसार आपके SBI बैंक खाते में काफी समय से कोई लेन-देन नहीं हुआ है। अपना खाता पुनः सक्रिय करवाने के लिए कृपया अपने नज़दीकी SBI CSP शाखा पर आएँ।

शाखा:
{{2}}

पता:
{{3}}

धन्यवाद,
{{2}}
```

## WhatsApp Template 2 — Urgent reminder (Hindi)
- **Template name:** `account_inoperative_urgent_hi`  ·  **Category:** Utility  ·  **Language:** hi
```text
नमस्ते {{1}} जी,

हमारे रिकॉर्ड के अनुसार आपका SBI बैंक खाता काफी समय से निष्क्रिय है। आवश्यक प्रक्रिया पूरी करने के लिए कृपया जल्द से जल्द अपने SBI CSP शाखा पर आएँ।

शाखा:
{{2}}

पता:
{{3}}

धन्यवाद,
{{2}}
```

---

## SMS fallback (only when WhatsApp fails, via MSG91 + DLT) — ENGLISH ONLY

```text
Hello {name}, our records indicate that your SBI account is inactive. Kindly visit {csp_name} (SBI CSP).

Address: {branch_address}
```

> SMS is kept **English (GSM-7, ~160 chars/segment)** on purpose — cheap and
> single-segment. A Devanagari (Hindi) SMS would be UCS-2 (~70 chars/segment),
> multi-segment, costlier, and would need a separate Unicode DLT template.
> WhatsApp (which can be Hindi) is the primary channel; SMS only fires for the
> minority whose WhatsApp fails.

---

## Filled example (Hindi, normal)
> नमस्ते Ramesh जी,
> हमारे रिकॉर्ड के अनुसार आपके SBI बैंक खाते में काफी समय से कोई लेन-देन नहीं हुआ है। अपना खाता पुनः सक्रिय करवाने के लिए कृपया अपने नज़दीकी SBI CSP शाखा पर आएँ।
> शाखा: **Sonia Vihar SBI CSP**
> पता: **Sonia Vihar, Delhi 110090**
> धन्यवाद, **Sonia Vihar SBI CSP**
