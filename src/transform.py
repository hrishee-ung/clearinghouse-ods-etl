from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple
from uuid import uuid4

import pandas as pd

from exceptions import FileGenerationError


def transform_to_clearinghouse_format(
    data: pd.DataFrame,
    config: Dict[str, Any],
) -> pd.DataFrame:
    """
    Transform extracted source data into the Clearinghouse file structure.

    In the current design, the SQL query already returns the submission-ready fields.
    Therefore this function intentionally performs a pass-through unless future
    formatting/business rules are required in Python.
    """
    if data is None:
        raise FileGenerationError("Cannot transform data because the source dataset is None.")

    return data.copy()


def generate_output_filename(config: Dict[str, Any], run_id: str) -> str:
    """
    Generate a unique file name based on configured pattern.
    """
    file_settings = config.get("file_settings", {})
    pattern = file_settings.get("output_filename_pattern", "clearinghouse_{timestamp}_{run_id}.txt")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_run_id = run_id.replace(":", "_").replace("-", "_")
    filename = pattern.format(
        timestamp=timestamp,
        run_id=safe_run_id,
        uuid=uuid4().hex,
    )
    return filename


def generate_clearinghouse_file(
    data: pd.DataFrame,
    config: Dict[str, Any],
    run_id: str,
) -> Tuple[Path, int]:
    """
    Transform source data and write the outbound Clearinghouse submission file.
    """
    try:
        transformed = transform_to_clearinghouse_format(data, config)

        file_settings = config.get("file_settings", {})
        paths_config = config.get("paths", {})

        delimiter = file_settings.get("delimiter", ",")
        encoding = file_settings.get("encoding", "utf-8")
        line_ending = file_settings.get("line_ending", "\n")
        extension = file_settings.get("extension", ".csv")
        include_header = bool(file_settings.get("include_header", True))

        output_dir = Path(paths_config.get("output", "."))
        output_dir.mkdir(parents=True, exist_ok=True)

        filename = generate_output_filename(config, run_id)
        if not filename.lower().endswith(extension.lower()):
            filename = f"{filename}{extension}"

        file_path = output_dir / filename

        if file_path.exists():
            raise FileGenerationError(
                f"Output file already exists and will not be overwritten: {file_path}"
            )

        transformed.to_csv(
            file_path,
            sep=delimiter,
            index=False,
            header=include_header,
            encoding=encoding,
            lineterminator=line_ending,
        )

        return file_path, len(transformed.index)

    except OSError as exc:
        raise FileGenerationError(f"Failed writing output file: {exc}") from exc
    except Exception as exc:
        if isinstance(exc, FileGenerationError):
            raise
        raise FileGenerationError(f"Failed generating Clearinghouse file: {exc}") from exc