import os

import pytesseract
from PIL import Image


_TESSERACT_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


for _path in _TESSERACT_PATHS:
    if os.path.exists(_path):
        pytesseract.pytesseract.tesseract_cmd = _path
        break


def extract_text_from_image(image_path: str) -> str:
    img = Image.open(image_path)
    return pytesseract.image_to_string(img, lang="eng")


def extract_text_from_pdf_page_image(pil_image: Image.Image) -> str:
    return pytesseract.image_to_string(pil_image, lang="eng")
