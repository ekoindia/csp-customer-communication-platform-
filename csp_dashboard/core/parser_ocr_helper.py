import re
from typing import List, Dict

_KNOWN_COLUMNS = [
    "sr_no", "branch_code", "csp_code", "account_number",
    "name", "balance_band", "father_name", "mobile",
    "taluka", "village", "address",
]


def parse_ocr_text(text: str) -> List[Dict]:
    """
    Best-effort parser for raw OCR output from a scanned bank table.
    Attempts to find a header row then map subsequent lines to columns.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    header_idx = _find_header_row(lines)
    if header_idx is None:
        return []
    headers = _split_row(lines[header_idx])
    rows = []
    for line in lines[header_idx + 1:]:
        cells = _split_row(line)
        if len(cells) >= 4:
            rows.append(dict(zip(headers, cells)))
    return rows


def _find_header_row(lines: List[str]) -> int | None:
    for i, line in enumerate(lines[:15]):
        lower = line.lower()
        if "account" in lower or "mobile" in lower or "name" in lower:
            return i
    return None


def _split_row(line: str) -> List[str]:
    return re.split(r"\s{2,}|\t|\|", line)
