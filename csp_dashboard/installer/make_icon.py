"""Generate the CSP Platform app icon (installer/CSP_Platform.ico).

A clean rounded-square mark with a message bubble (the platform is a customer
communication tool) over a deep-teal ground. Multi-size .ico so Windows renders
it crisply at every scale (desktop, taskbar, Add/Remove Programs)."""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CSP_Platform.ico")
BG1 = (13, 94, 102)      # deep teal
BG2 = (18, 132, 122)     # lighter teal (subtle vertical shade)
BUBBLE = (245, 166, 35)  # amber (matches the admin portal accent)
INK = (255, 255, 255)


def _draw(size: int) -> Image.Image:
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # vertical gradient ground
    for y in range(S):
        t = y / S
        c = tuple(int(BG1[i] + (BG2[i] - BG1[i]) * t) for i in range(3))
        d.line([(0, y), (S, y)], fill=c + (255,))
    # round the corners by masking
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=56, fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)
    # speech bubble
    d.rounded_rectangle([54, 66, 202, 168], radius=26, fill=BUBBLE)
    d.polygon([(84, 168), (84, 206), (120, 168)], fill=BUBBLE)   # tail
    # three message lines
    for i, w in enumerate((120, 120, 84)):
        yy = 92 + i * 24
        d.rounded_rectangle([74, yy, 74 + w, yy + 10], radius=5, fill=(13, 94, 102))
    # "CSP" wordmark
    try:
        font = ImageFont.truetype("arialbd.ttf", 46)
    except Exception:
        font = ImageFont.load_default()
    d.text((S / 2, 214), "CSP", font=font, fill=INK, anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


def main():
    base = _draw(256)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base.save(OUT, format="ICO", sizes=[(s, s) for s in sizes])
    print("wrote", OUT, "sizes", sizes)


if __name__ == "__main__":
    main()
