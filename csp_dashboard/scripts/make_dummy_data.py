"""
Generate FAKE bank-document test data in the exact column format the real sheet
uses. Use this for ALL testing so real customer data never goes anywhere.

Produces:
  data/dummy_bank.csv         — fast path (no OCR), test upload/segregation/review
  data/dummy_bank_table.png   — a rendered table image, test the OCR path

Every name / mobile / account here is INVENTED. Villages/taluka are obviously
fake ("Testpur", "Sample Block") so this can never be mistaken for real data.
"""

import csv
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")

HEADERS = ["SR NO", "BRANCH", "CSP_CODE", "MEMB_CUST_A", "NAME", "BALANCE",
           "FTHR_NM", "MOBILE_NBR", "TALUKA", "VILLAGE", "ADDRESS_WITH_PIN"]

# Fake rows: cover every band; some blank mobiles (not reachable).
ROWS = [
    # acct,        name,            balance,      father,        mobile,       village
    ("50000000001", "RAMESH KUMAR",  "B>10000",    "TESTFATHER A", "",           "Testpur"),
    ("50000000002", "SITA DEVI",     "1000<10000", "TESTFATHER B", "",           "Testpur"),
    ("50000000003", "MOHAN LAL",     "1000<10000", "TESTFATHER C", "9990000003", "Demoganj"),
    ("50000000004", "GEETA X",       "1000<10000", "TESTFATHER D", "9990000004", "Demoganj"),
    ("50000000005", "SURESH PRASAD", "100<1000",   "TESTFATHER E", "8880000005", "Testpur"),
    ("50000000006", "ANITA DEVI",    "100<1000",   "TESTFATHER F", "8880000006", "Sampleville"),
    ("50000000007", "VIJAY SINGH",   "100<1000",   "TESTFATHER G", "7770000007", "Sampleville"),
    ("50000000008", "KAVITA X",      "0.1<100",    "TESTFATHER H", "7770000008", "Testpur"),
    ("50000000009", "RAJU PRASAD",   "0.1<100",    "TESTFATHER I", "6660000009", "Demoganj"),
    ("50000000010", "MEENA KUMARI",  "0.1<100",    "TESTFATHER J", "",           "Demoganj"),
    ("50000000011", "ARUN YADAV",    "B>10000",    "TESTFATHER K", "9990000011", "Sampleville"),
    ("50000000012", "POOJA DEVI",    "1000<10000", "TESTFATHER L", "8880000012", "Testpur"),
    ("50000000013", "SANJAY X",      "100<1000",   "TESTFATHER M", "7770000013", "Demoganj"),
    ("50000000014", "REKHA KUMARI",  "0.1<100",    "TESTFATHER N", "6660000014", "Sampleville"),
    ("50000000015", "DEEPAK GUPTA",  "1000<10000", "TESTFATHER O", "9990000015", "Testpur"),
]

BRANCH, CSP, TALUKA = "99999", "9XTEST999", "Sample Block"


def _address(village):
    return f"VILL-{village.upper()} SAMPLE BLOCK DIST-TESTNAGAR 000000"


def write_csv():
    path = os.path.join(DATA, "dummy_bank.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        for i, (acct, name, bal, fa, mob, vil) in enumerate(ROWS, 1):
            w.writerow([i, BRANCH, CSP, acct, name, bal, fa, mob, TALUKA, vil, _address(vil)])
    return path


def write_image():
    path = os.path.join(DATA, "dummy_bank_table.png")
    col_w = [50, 70, 100, 140, 150, 110, 140, 130, 120, 110, 340]
    row_h, pad = 40, 10
    W = sum(col_w) + pad * 2
    H = row_h * (len(ROWS) + 1) + pad * 2
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    def draw_row(y, cells, bold=False):
        x = pad
        for cw, text in zip(col_w, cells):
            d.rectangle([x, y, x + cw, y + row_h], outline="black", width=1)
            d.text((x + 4, y + 8), str(text)[:40], fill="black", font=font)
            x += cw

    draw_row(pad, HEADERS, bold=True)
    for i, (acct, name, bal, fa, mob, vil) in enumerate(ROWS, 1):
        y = pad + row_h * i
        draw_row(y, [i, BRANCH, CSP, acct, name, bal, fa, mob, TALUKA, vil, _address(vil)])
    img.save(path)
    return path


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    print("wrote", write_csv())
    print("wrote", write_image())
