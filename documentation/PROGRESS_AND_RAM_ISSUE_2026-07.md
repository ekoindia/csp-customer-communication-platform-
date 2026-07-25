# Progress & OCR/RAM Issue — Short Summary
*Written 2026-07-17*

## 1. Progress — how the software works (workflow)

```text
Bank file (Excel/CSV/PDF/scan)
        ↓
Software reads it (direct read for Excel/CSV, OCR for scan/photo)
        ↓
CSP reviews every row on screen and fixes anything wrong
        ↓
CSP approves (one by one or in bulk)
        ↓
Message sent on WhatsApp (SMS if WhatsApp fails)
        ↓
Delivery tracked automatically + CSP marks visit/closure manually
```

Every step above is **fully built and working** — tested end-to-end on the
dev machine with Excel, CSV, PDF, and scanned images. The software itself is
complete.

**Important note:** the software was built to handle every possible input
type — Excel, CSV, typed PDF, scanned PDF, and photos — so it works no
matter what format a bank sends. But in practice, for the campaigns run
from the CSP side so far, **only scanned PDF has actually been available**
— no bank has yet handed over an Excel/CSV export. So the OCR path isn't
an edge case here, it's the *only* path being used in the field right now
— which is exactly why the RAM problem below matters so much.

The one open problem is below.

---

## 2. Where we are stuck — OCR is not working properly on the CSP machine

**The problem, simply:** the CSP machine only has 4GB RAM (and Windows +
background services already use most of it). Reading a scanned image
(OCR) needs a good amount of memory to process — and even the **lightest,
smallest open-source local OCR model** we use still needs more memory than
this machine can spare. So on the CSP machine specifically, OCR runs slow
or gets stuck — not because the model is inaccurate, but because the
machine doesn't have enough free RAM to run it smoothly.

Since scanned PDF is the only format actually used in the field so far,
this is a real, live blocker — not a rare edge case.

**What's already been tried to fix this:**

- Switching automatically to the lightest OCR engine on weak machines
  (skipping the heavier AI model entirely below a RAM threshold).
- Lowering the image quality/resolution used for OCR on weak machines to
  use less memory.
- Processing one page at a time and clearing memory after each page instead
  of holding everything at once.
- Delaying the WhatsApp service start until after OCR finishes, so it
  doesn't compete for the same limited memory.
- **Tesseract** is used as the base engine — it's the lightest local OCR
  model available, chosen specifically because it's the one able to fit
  inside the CSP machine's hardware limits (the heavier AI-based OCR
  models don't fit at all on 4GB).
- On top of that, a **custom-trained OCR model** was built specifically for
  the two fields that matter most for accuracy — **mobile number** and
  **account number** — and is combined with Tesseract's output, so the
  most error-sensitive fields get an extra accuracy check without needing
  a full heavy OCR model for the whole page.

This is a genuine hardware-constraint problem, and it's actively being
worked on — not a one-line fix, since it needs the OCR pipeline itself to
be rebuilt lighter for this specific low-RAM machine.

---

## 3. Why Excel/CSV gives 100% accuracy (and scans don't)

- **Excel/CSV** = the bank's system exports the data as plain digital text.
  The software just reads it directly — no interpretation needed. This is
  why it's 100% accurate, and also why it needs almost no memory.
- **Scanned PDF/photo** = the software has to *look at an image* and guess
  the text, the same way a person reads a blurry photo or handwriting. Even
  the best OCR in the world occasionally misreads a digit or a smudged
  word — that's simply what OCR is: an educated guess on an image, not a
  read of real text. That's why every scanned row still needs a human
  check before it becomes a real case.

## 4. Why even the lightest OCR model struggles on the CSP machine

It's not an accuracy problem — it's a resource problem. Reading a scanned
page needs several memory-heavy steps (opening the image, cleaning it up,
detecting the table, reading each cell). Even the smallest/lightest OCR
model still needs more free memory for these steps than the CSP machine
has left after Windows and background apps are already running. So the
same OCR that runs fine on a normal machine slows down or stalls on this
specific 4GB machine — this is exactly the RAM-shortage problem being
solved right now.
