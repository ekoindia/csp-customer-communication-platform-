# CSP Platform — Install Guide

Getting a CSP up and running is two steps: **Eko generates + sends one file**,
the **CSP double-clicks it**. No zip to build, no key to send separately.

---

## PART A — Eko side (generate + send the setup file)

1. **Log in to the admin portal**
   `http://122.176.147.78:8080/csp-admin/login`  (user `admin`)

2. **Issue the CSP's API key** — left nav → **API Keys** → enter the CSP's code
   (e.g. `1AB50895`) → **Issue**.

3. **Download the setup file** — on that CSP's row click **Download CSP_Setup.bat**.
   You get `CSP_Setup_<id>.bat` with the GitHub install link + CSP_ID + API key
   already baked in.

4. **Send that ONE file** to the CSP (WhatsApp / email / pen-drive).

---

## PART B — CSP side (install)

### Minimum hardware (the hardware constraint)
The setup checks this up front and stops with a clear message if the PC is below it.

| Component | Minimum | Notes |
|---|---|---|
| OS | **Windows 10, 64-bit** (or 11) | 32-bit not supported |
| RAM | **4 GB** | runs in light Tesseract-only OCR; hard floor 3 GB (below = blocked) |
| Free disk | **~3 GB** | for the app + Python/Node/Tesseract |
| CPU | dual-core x64 (i3 or better) | no GPU needed |
| Internet | broadband | WhatsApp + updates |

> Reference deploy PC = **Dell Inspiron 3268** (4 GB, i3-7100, no GPU, Win10 x64).
> The app auto-detects RAM and adapts: on a 4 GB box it uses the light OCR path,
> starts WhatsApp only when sending, and renders scans at a lower DPI — so it
> fits without swapping. Bank **CSV/Excel** uploads skip OCR entirely (most
> accurate + lightest).

1. **Double-click** `CSP_Setup_<id>.bat`.
2. On the Windows security (UAC) prompt, click **Yes / Run anyway**.
3. A black window opens — **leave it open** (2–5 min). It automatically:
   - downloads the app from GitHub into `C:\CSP_Platform`,
   - installs Python 3.11, Node.js, Tesseract-OCR + app dependencies (via winget),
   - writes `.env` (CSP_ID + key) — nothing to type,
   - creates the **"CSP Platform" icon** on the Desktop,
   - starts the app (the dashboard opens in the browser).
4. **First-time setup screen** (before login) — the CSP enters:
   - their own **Login ID** + **Password**,
   - **branch details** (CSP name, branch code, address, phone).
   Their login is also saved to `CSP_Login.txt` on the Desktop.
5. Log in → **dashboard ready**.

From then on: just double-click the **"CSP Platform"** desktop icon.

> **OCR note:** the app ships a small custom digit model (`core/models/crnn.onnx`)
> for account/mobile numbers; Tesseract reads the text fields (name/village/band).
> On the 4 GB deploy PC only these run (no PyTorch/docTR).

---

## Updating a CSP later
- **Online (normal):** the CSP double-clicks **`UPDATE.bat`** in `C:\CSP_Platform`
  — it pulls the latest from GitHub, keeps all settings/data/WhatsApp login, and
  restarts. Eko only has to `git push`.

## Troubleshooting
| Problem | Fix |
|---|---|
| "winget not found" | Microsoft Store → update **App Installer** → re-run |
| Download failed | Check internet, double-click again |
| Browser didn't open | Open `http://127.0.0.1:5000` manually |
| WhatsApp | Dashboard → Settings → scan the QR once |

## Admin: disable a CSP
Admin portal → **API Keys** → **Revoke** on that CSP (its reporting stops
immediately; **Reactivate** to re-enable).
