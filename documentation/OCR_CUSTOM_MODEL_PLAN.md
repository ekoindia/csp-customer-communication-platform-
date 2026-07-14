# Custom On-Device OCR — Plan (Phase 3)

*Goal: read the bank's mobile-photo scanned account lists **accurately** on the
real 4 GB / i3 / no-GPU deploy PCs, with **no manual typing by the CSP**.*

---

## 1. Why we need this (evidence, not opinion)

Measured on a real DocScanner mobile-scan (`data/DocScanner ….pdf`, page 1):

| Engine | Runs on 4 GB box? | Rows extracted (of ~52) | Notes |
|--------|-------------------|-------------------------|-------|
| docTR (deep learning) | ❌ needs ≥6-8 GB (PyTorch ~1 GB) | ~52 | good, but OOMs the deploy PC |
| Tesseract-only | ✅ (current deploy path) | **16-24** | misses most rows; garbage names (`X`, `TOR`) |

- Lower DPI: **no change** (time flat ~35 s/page; rows 16-24). Not a lever.
- OpenCV preprocessing (CLAHE/sharpen): **small** gain (24→26 rows; 8→11 valid mobiles). A complement, not a fix.

**Conclusion:** the engine is the bottleneck. General OCR either doesn't fit the
hardware (docTR) or under-reads mobile scans (Tesseract). The fix is a **small,
task-specific model** that fits the hardware — the same principle that lets
license-plate / meter readers run accurately on tiny devices.

The ideal fix remains **digital bank data (CSV/Excel = no OCR = 100%)** — keep
pushing SBI/Eko for an export. This plan is for when only a mobile scan exists.

---

## 2. The key insight that makes it tractable

We do **not** need a general OCR. Prior product decisions shrank the job:

- **Message is generic (no customer name).** → the name does **not** need to be
  read accurately. This removes OCR's hardest part (arbitrary text).
- Only **three** fields must be right, and they're the *easy* kind:
  - **Mobile** — 10 digits → digit recognition.
  - **Account** — digits (for dedup) → digit recognition.
  - **Balance band** — one of **4 fixed values** → this is **4-class
    classification**, not OCR at all.

So the model = **a small digit recognizer + a 4-class classifier.** Both are
solved, tiny, CPU-cheap problems. (An optional small text recognizer for names
can come later; it's not on the critical path.)

---

## 3. How it runs on 4 GB (train heavy, deploy light)

- **Train** on the dev box (RTX 4060) or a cloud GPU — PyTorch used *only here*.
- **Export** the trained model to **ONNX**.
- **Deploy** with **onnxruntime** (CPU) on the CSP PC — NOT PyTorch.
  onnxruntime is ~50 MB, does millisecond inference on CPU, and fits easily in
  the ~1.5 GB free on the 4 GB box. Optionally **int8-quantize** for even
  smaller/faster. The deploy box never loads a heavy ML framework.

This slots into the existing pipeline with **no change to the parts that work**:
grid detection (OpenCV) and the human review gate stay; we only **replace the
per-cell word reader** (Tesseract) with the ONNX recognizer for the digit/band
cells.

---

## 4. Data plan (the real cost)

A model is only as good as its labelled data. Two sources, combined:

1. **Synthetic bootstrap (fast, large):** render digits / the 4 band strings in
   the bank's font, then augment to mimic phone scans — blur, skew, perspective
   warp, uneven lighting/shadow, JPEG noise, paper texture. Generates tens of
   thousands of labelled cells automatically.
2. **Real fine-tune (small, high-value):** crop cells from **real** scanned docs
   (the product owner will supply these manually) and label a few hundred —
   mobiles, accounts, bands. An **Eko operator** labels them once; **never the
   CSP**, and this happens off the CSP machine (DPDP: use test/sample data, not
   live customer files, or a properly consented processing agreement).

Best recipe: pretrain on synthetic → fine-tune on the small real set.

---

## 5. Build stages

1. **Data pipeline** — cell-crop tool (uses existing grid detection to cut
   cells) + synthetic generator + augmentation.
2. **Models** —
   - Digit recognizer: tiny CRNN+CTC (variable-length digit strings) or a
     per-digit CNN after digit segmentation. Target alphabet: `0-9` (+ blank).
   - Band classifier: small CNN, 4 classes.
3. **Train + evaluate** on held-out real cells. Metrics below.
4. **Export ONNX + quantize;** verify parity vs the torch model.
5. **Integrate** — new `core/ocr_onnx.py` recognizer; wire it into
   `ocr_table.py` behind the existing `resolve_ocr_engine()` (`"onnx"` option)
   so it's selectable per machine with zero change elsewhere.
6. **Validate on the 4 GB box** — accuracy + speed under real memory pressure.

---

## 6. Success criteria

- **Mobile digit accuracy ≥ 99%** on real held-out cells (this is the field that
  must be right — a wrong number messages a stranger).
- **Band classification ≥ 99%** (only 4 classes).
- **Reachable-mobile rate** (valid 10-digit mobiles extracted / rows present)
  materially above today's Tesseract baseline (~8-11 per page).
- **No dropped rows:** every row the grid detects is kept; a cell the model is
  unsure of is **flagged, not guessed**.
- **Speed:** ≤ a few seconds/page inference on the i3 (onnxruntime CPU).
- **Footprint:** model + runtime well under the 4 GB budget; no PyTorch on box.

---

## 7. Safety model (unchanged, reinforced)

Even at 99%, never message a wrong number. Keep the **verify-or-escalate** rule:
read each mobile, and if the model's confidence is low or a second read
disagrees, **do not send** — route that customer to the manual-call list (the
CSP's existing process). The CSP still types nothing; uncertain rows fall back
to the old manual follow-up for that minority.

---

## 8. Honest effort / sequencing

This is a **multi-week phase** (data pipeline → train → export → integrate →
validate), not a few hours — the model is easy, the data + validation are the
work. It runs **after** the shipped hardening (edit-lock, PII, manual update,
background extraction). Recommended order:

1. ✅ Done: background extraction + progress (so big scans don't freeze the UI).
2. Push for **digital bank data** in parallel (the only true 100%, zero-OCR).
3. Build this model, starting with the **digit recognizer** (highest value —
   the mobile number), then the band classifier, then (optional) names.

---

*This plan replaces the earlier "typed-PDF-first" idea, which is moot for
mobile-photo scans (no text layer). See also `README.md` §OCR and the
hardening-pass notes.*
