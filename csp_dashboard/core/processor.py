"""
Document Processing pipeline.
Owner of: documents table, customer_cases table.

Flow:
  uploaded file
    → parser.py         (raw rows)
    → column_mapper.py  (normalise column names)
    → validator.py      (Pydantic CustomerRow)
    → classifier.py     (balance band → template_id / tone / is_sensitive)
    → queries.py        (write to DB)
"""

import os
from datetime import datetime, timezone
from typing import Sequence, Tuple

from pydantic import ValidationError

import config
from core.parser import parse
from core.column_mapper import map_columns, extract_row
from core.validator import CustomerRow
from campaigns.inoperative.classifier import classify
from database import queries


def _batch_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"BATCH_{ts}"


def _case_id(batch_id: str, index: int) -> str:
    return f"{batch_id}_C{index:04d}"


def process_upload(file_path: str, campaign_id: str) -> Tuple[str, dict]:
    """
    Run the full pipeline for an uploaded file.

    Returns:
        (batch_id, stats)
        stats = {total, valid, invalid, errors: [{row, reason}]}
    """
    return process_upload_batch([file_path], campaign_id)


def process_upload_batch(
    file_paths: Sequence[str],
    campaign_id: str,
    original_names: Sequence[str] | None = None,
) -> Tuple[str, dict]:
    """
    Run the full pipeline for one upload request containing one or more files.

    All rows are stored under a single batch_id, then messages are generated once
    for that batch so dispatch can run as one queue.
    """
    if not file_paths:
        raise ValueError("No files supplied")

    batch_id = _batch_id()
    names = list(original_names or [os.path.basename(p) for p in file_paths])
    original_name = _display_name(names)
    file_format = _display_format(file_paths)

    # -- 1. Record document upload --
    queries.insert_document(batch_id, campaign_id, original_name, file_format)
    queries.update_document_status(batch_id, "processing")

    errors = []
    total = 0
    valid_count = 0
    row_number = 0

    try:
        for file_index, file_path in enumerate(file_paths):
            file_name = names[file_index] if file_index < len(names) else os.path.basename(file_path)

            # -- 2. Parse file → raw rows --
            try:
                raw_rows = parse(file_path)
            except Exception as e:
                errors.append({"row": 0, "reason": f"{file_name}: Processing error: {e}"})
                continue

            if not raw_rows:
                errors.append({"row": 0, "reason": f"{file_name}: No data found in file"})
                continue

            total += len(raw_rows)

            # -- 3. Map columns --
            first_row = raw_rows[0]
            mapping = map_columns(list(first_row.keys()))

            required = {"account_number", "name", "mobile", "balance_band"}
            missing = required - set(mapping.keys())
            if missing:
                errors.append({
                    "row": 0,
                    "reason": f"{file_name}: Required columns not found: {', '.join(missing)}",
                })
                continue

            for raw in raw_rows:
                row_number += 1
                extracted = extract_row(raw, mapping)

                # -- 4. Validate --
                try:
                    row = CustomerRow(**extracted)
                except ValidationError as e:
                    errors.append({"row": row_number, "reason": str(e.errors()[0]["msg"])})
                    continue

                # -- 5. Classify --
                try:
                    classification = classify(row.balance_band)
                except ValueError as e:
                    errors.append({"row": row_number, "reason": str(e)})
                    continue

                # -- 6. Insert customer case --
                case_id = _case_id(batch_id, row_number)
                queries.insert_customer_case(
                    case_id=case_id,
                    batch_id=batch_id,
                    campaign_id=campaign_id,
                    account_number=row.account_number,
                    name=row.name,
                    mobile=row.mobile,
                    father_name=row.father_name,
                    balance_band=row.balance_band,
                    village=row.village,
                    taluka=row.taluka,
                    address=row.address,
                    band_label=classification["band"],
                    tone=classification["tone"],
                    template_id=classification["template_id"],
                    is_sensitive=classification["is_sensitive"],
                )

                # -- 7. Init business tracking --
                queries.init_business_tracking(case_id)

                valid_count += 1

    except Exception as e:
        queries.update_document_status(batch_id, "failed")
        return batch_id, {"total": 0, "valid": 0, "invalid": 0, "errors": [{"row": 0, "reason": f"Processing error: {e}"}]}
    finally:
        for file_path in file_paths:
            # DPDP: don't keep raw uploads after the intake attempt.
            try:
                os.remove(file_path)
            except OSError:
                pass

    invalid = total - valid_count

    if valid_count == 0:
        queries.update_document_status(batch_id, "failed")
        if not errors:
            errors.append({"row": 0, "reason": "No valid rows found"})
        return batch_id, {
            "total": total,
            "valid": 0,
            "invalid": invalid,
            "messages_generated": 0,
            "errors": errors,
        }

    queries.update_document_counts(batch_id, total, valid_count, invalid)

    # -- 8. Generate messages for all valid cases --
    from core.message_engine import generate_batch_messages
    msg_result = generate_batch_messages(batch_id)
    for e in msg_result["errors"]:
        errors.append({"row": 0, "reason": f"Message error [{e['case_id']}]: {e['reason']}"})

    return batch_id, {
        "total": total,
        "valid": valid_count,
        "invalid": invalid,
        "messages_generated": msg_result["generated"],
        "errors": errors,
    }


def _display_name(names: Sequence[str]) -> str:
    if len(names) == 1:
        return names[0]
    shown = ", ".join(names[:3])
    if len(names) > 3:
        shown = f"{shown}, +{len(names) - 3} more"
    return f"{len(names)} files: {shown}"


def _display_format(file_paths: Sequence[str]) -> str:
    formats = {
        os.path.splitext(path)[1].lstrip(".").lower()
        for path in file_paths
        if os.path.splitext(path)[1]
    }
    if len(formats) == 1:
        return next(iter(formats))
    return "mixed"
