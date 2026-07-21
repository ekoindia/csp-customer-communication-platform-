# CSP Scan — Android app

A thin **WebView shell** around the client-side scanner page
(`csp_dashboard/mobile_scanner/scan.html`). Everything — camera capture,
on-device OCR (Tesseract.js), PDF rendering (PDF.js), Excel build (SheetJS), and
`.cspx` encryption (Web Crypto) — runs **inside the page, on the phone**. The app
just hosts it in a secure context so the camera and Web Crypto work.

## How the APK is produced (no Android Studio needed)

Every push builds it in GitHub Actions and publishes a downloadable, debug-signed
APK:

1. Open the repo's **Actions** tab → the latest **"Build CSP Scan APK"** run.
2. Download the **`CSP-Scan-APK`** artifact (a zip) → inside is **`CSP-Scan.apk`**.
3. Send that `.apk` to the CSP.
4. On the phone: allow **"install from unknown sources"**, install, tap the
   **CSP Scan** icon.

To build manually: Actions tab → "Build CSP Scan APK" → **Run workflow**.

## DPDP

The scanner page loads through `WebViewAssetLoader` (a secure `https://` virtual
origin), does all processing locally, and only ever emits an **encrypted
`.cspx`**. The document image and extracted PII never leave the phone in the
clear. See `documentation/APK_MOBILE_SCANNER_SPEC.md`.

## Notes

- `minSdk 26` (Android 8+). Debug-signed (fine for sideloading; not for Play Store).
- The OCR/Excel/PDF libraries load from a CDN on first use (they are program code,
  not customer data) — so the first scan needs internet; afterwards the WebView
  caches them. A later version can bundle them for fully offline use.
