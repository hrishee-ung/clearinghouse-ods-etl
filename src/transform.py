from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import numbers

from exceptions import FileGenerationError


def generate_output_basename(config: Dict[str, Any], run_id: str, file_key: str) -> str:
    file_settings = config.get("file_settings", {})
    pattern = file_settings.get("output_filename_pattern", "{file_key}_{timestamp}_{run_id}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_run_id = run_id.replace(":", "_").replace("-", "_")

    return pattern.format(
        file_key=file_key,
        timestamp=timestamp,
        run_id=safe_run_id,
        uuid=uuid4().hex,
    )


def _normalize_cell(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def _required_column(df: pd.DataFrame, column_name: str, file_key: str) -> pd.Series:
    if column_name not in df.columns:
        raise FileGenerationError(
            f"Required column '{column_name}' not found in query result for '{file_key}'."
        )
    return df[column_name]


def build_formatted_rows(
    df: pd.DataFrame,
    file_key: str,
    config: Dict[str, Any],
    file_date: str,
) -> list[list[str]]:
    """
    Build final outbound rows for one query/file.

    Data rows:
    A = D1
    B = blank
    C = First Name
    D = Middle Initial
    E = Last Name
    F = Name Suffix
    G = Date of Birth
    H = File creation date
    I = blank
    J = 001585
    K = 00
    L = TERM_CODE.UNG_ID
    """
    layout = config.get("clearinghouse_layout", {})
    header_template = layout.get("header_row", [])

    if not header_template:
        raise FileGenerationError("clearinghouse_layout.header_row is not configured.")

    header_row = [str(v).replace("{file_date}", file_date) for v in header_template]
    rows: list[list[str]] = [header_row]

    first_name = _required_column(df, "SPRIDEN_FIRST_NAME", file_key)
    middle_initial = _required_column(df, "NSC_MIDDLE_INITIAL", file_key)
    last_name = _required_column(df, "SPRIDEN_LAST_NAME", file_key)
    name_suffix = _required_column(df, "NAME_SUFFIX", file_key)
    birthdate = _required_column(df, "NSC_BIRTHDATE", file_key)
    term_code = _required_column(df, "TERM_CODE", file_key)
    ung_id = _required_column(df, "UNG_ID", file_key)

    for idx in df.index:
        composite_id = f"{_normalize_cell(term_code.loc[idx])}.{_normalize_cell(ung_id.loc[idx])}"

        row = [
            "D1",                                   # A
            "",                                     # B
            _normalize_cell(first_name.loc[idx]),   # C
            _normalize_cell(middle_initial.loc[idx]),  # D
            _normalize_cell(last_name.loc[idx]),    # E
            _normalize_cell(name_suffix.loc[idx]),  # F
            _normalize_cell(birthdate.loc[idx]),    # G
            file_date,                              # H
            "",                                     # I
            "001585",                               # J
            "00",                                   # K
            composite_id,                           # L
        ]
        rows.append(row)

    trailer_row_number = len(rows) + 1
    trailer_row = ["T1", str(trailer_row_number)]
    rows.append(trailer_row)

    return rows


def _write_excel_as_text_formatted(rows: list[list[str]], workbook_path: Path, sheet_name: str = "Sheet1") -> None:
    wb = Workbook()
    try:
        ws = wb.active
        ws.title = sheet_name[:31]

        for row in rows:
            ws.append(row)

        for row in ws.iter_rows():
            for cell in row:
                cell.number_format = numbers.FORMAT_TEXT

        wb.save(workbook_path)
    finally:
        wb.close()
        del wb


def _write_tab_delimited_text(
    rows: list[list[str]],
    text_file_path: Path,
    delimiter: str,
    encoding: str,
    line_ending: str,
) -> None:
    with text_file_path.open("w", encoding=encoding, newline="") as f:
        for row in rows:
            string_row = [str(value) if value is not None else "" for value in row]
            f.write(delimiter.join(string_row) + line_ending)


def generate_clearinghouse_files(
    extracted_data: dict[str, pd.DataFrame],
    config: Dict[str, Any],
    run_id: str,
    file_key: str,
) -> tuple[list[dict[str, Any]], int]:
    """
    Generate separate Excel and tab-delimited text files for each configured query.

    Returns:
        file_artifacts: list of dicts with file_key, workbook_path, text_file_path, record_count
        total_record_count: int
    """
    if extracted_data is None:
        raise FileGenerationError("Cannot generate files because extracted_data is None.")

    file_settings = config.get("file_settings", {})
    paths_config = config.get("paths", {})

    delimiter = file_settings.get("delimiter", "\t")
    encoding = file_settings.get("encoding", "utf-8")
    line_ending = file_settings.get("line_ending", "\n")
    workbook_extension = file_settings.get("workbook_extension", ".xlsx")
    text_extension = file_settings.get("text_extension", ".txt")

    output_dir = Path(paths_config.get("output", "."))
    output_dir.mkdir(parents=True, exist_ok=True)

    file_date = datetime.now().strftime("%Y%m%d")

    file_artifacts: list[dict[str, Any]] = []
    total_record_count = 0

    try:
        for file_key, df in extracted_data.items():
            base_name = generate_output_basename(config, run_id, file_key)
            workbook_path = output_dir / f"{base_name}{workbook_extension}"
            text_file_path = output_dir / f"{base_name}{text_extension}"

            if workbook_path.exists():
                raise FileGenerationError(
                    f"Workbook output already exists and will not be overwritten: {workbook_path}"
                )

            if text_file_path.exists():
                raise FileGenerationError(
                    f"Text output already exists and will not be overwritten: {text_file_path}"
                )

            rows = build_formatted_rows(df, file_key, config, file_date)

            _write_excel_as_text_formatted(rows, workbook_path, sheet_name=file_key[:31])
            _write_tab_delimited_text(rows, text_file_path, delimiter, encoding, line_ending)

            record_count = len(df.index)
            total_record_count += record_count

            file_artifacts.append(
                {
                    "file_key": file_key,
                    "workbook_path": workbook_path,
                    "text_file_path": text_file_path,
                    "record_count": record_count,
                }
            )

        return file_artifacts, total_record_count

    except OSError as exc:
        raise FileGenerationError(f"Failed writing output file(s): {exc}") from exc
    except Exception as exc:
        if isinstance(exc, FileGenerationError):
            raise
        raise FileGenerationError(f"Failed generating Clearinghouse files: {exc}") from exc