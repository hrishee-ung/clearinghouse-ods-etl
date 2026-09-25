from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable

import pandas as pd

from exceptions import ValidationError


def validate_source_data(
    data: pd.DataFrame,
    config: Dict[str, Any],
) -> None:
    """
    Generic source-data validation.
    """
    app_config = config.get("app", {})
    extract_config = config.get("extract", {})

    allow_zero_records = bool(app_config.get("allow_zero_records", False))
    required_fields: list[str] = extract_config.get("required_fields", [])
    duplicate_check_fields: list[str] = extract_config.get("duplicate_check_fields", [])

    if data is None:
        raise ValidationError("Source validation failed: extracted data is None.")

    if data.empty and not allow_zero_records:
        raise ValidationError("Source validation failed: extracted record count is zero.")

    _validate_required_fields(data, required_fields)
    _validate_duplicates(data, duplicate_check_fields)




def validate_clearinghouse_file(
    file_path: str | Path,
    expected_record_count: int,
    config: Dict[str, Any],
) -> None:
    """
    Validate generated output file.
    Add Clearinghouse-specific file validations here later.
    """
    validation_config = config.get("validation", {})
    file_settings = config.get("file_settings", {})

    file_must_exist = bool(validation_config.get("file_must_exist", True))
    file_must_not_be_empty = bool(validation_config.get("file_must_not_be_empty", True))
    validate_record_count_match = bool(validation_config.get("validate_record_count_match", True))
    encoding = file_settings.get("encoding", "utf-8")

    path = Path(file_path)

    if file_must_exist and not path.exists():
        raise ValidationError(f"Generated file does not exist: {path}")

    if file_must_not_be_empty and path.stat().st_size == 0:
        raise ValidationError(f"Generated file is empty: {path}")

    try:
        with path.open("r", encoding=encoding, newline="") as f:
            lines = f.readlines()
    except OSError as exc:
        raise ValidationError(f"Generated file could not be read: {exc}") from exc

    actual_record_count = len(lines)

    if validate_record_count_match and actual_record_count != expected_record_count:
        raise ValidationError(
            f"File record count mismatch. Expected {expected_record_count}, found {actual_record_count}."
        )



def _validate_required_fields(data: pd.DataFrame, required_fields: Iterable[str]) -> None:
    for field in required_fields:
        if field not in data.columns:
            raise ValidationError(f"Required field missing from extract: {field}")

        null_count = int(data[field].isna().sum())
        if null_count > 0:
            raise ValidationError(
                f"Required field '{field}' contains {null_count} null value(s)."
            )


def _validate_duplicates(data: pd.DataFrame, duplicate_check_fields: Iterable[str]) -> None:
    fields = list(duplicate_check_fields)
    if not fields:
        return

    for field in fields:
        if field not in data.columns:
            raise ValidationError(f"Duplicate-check field missing from extract: {field}")

    duplicate_rows = data.duplicated(subset=fields, keep=False)
    duplicate_count = int(duplicate_rows.sum())

    if duplicate_count > 0:
        raise ValidationError(
            f"Duplicate validation failed: found {duplicate_count} duplicate row(s) "
            f"using fields {fields}."
        )