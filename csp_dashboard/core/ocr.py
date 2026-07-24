import os

from PIL import Image

# Tesseract is OPTIONAL (the platform runs on OnnxTR). Import defensively so a
# machine without the pytesseract binding can still load this module; importing
# core.ocr is used elsewhere only to set the binary path when it IS present.
try:
    import pytesseract
except ImportError:
    pytesseract = None


_TESSERACT_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


if pytesseract is not None:
    for _path in _TESSERACT_PATHS:
        if os.path.exists(_path):
            pytesseract.pytesseract.tesseract_cmd = _path
            break


def extract_text_from_image(image_path: str) -> str:
    if pytesseract is None:
        raise RuntimeError("Tesseract is not installed (OnnxTR is the OCR engine).")
    img = Image.open(image_path)
    return pytesseract.image_to_string(img, lang="eng")


def extract_text_from_pdf_page_image(pil_image: Image.Image) -> str:
    if pytesseract is None:
        raise RuntimeError("Tesseract is not installed (OnnxTR is the OCR engine).")
    return pytesseract.image_to_string(pil_image, lang="eng")
