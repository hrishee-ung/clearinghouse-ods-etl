from __future__ import annotations

from typing import Any, Dict, Tuple

import pandas as pd
import pyodbc

from exceptions import ExtractionError


def _quote_sql_string(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _build_values_list(values: list[str]) -> str:
    return ",\n                ".join(f"({_quote_sql_string(v)})" for v in values)


def build_query_from_template(sql_template: str, term_config: Dict[str, Any]) -> str:
    try:
        return sql_template.format(
            historical_all_terms_exclude_term=term_config["historical_all_terms_exclude_term"],
            historical_graduate_start_term=term_config["historical_graduate_start_term"],
            historical_graduate_end_term=term_config["historical_graduate_end_term"],
            attrition_fall_terms_values=_build_values_list(term_config["attrition_fall_terms"]),
            not_matric_fall_terms_values=_build_values_list(term_config["not_matric_fall_terms"]),
        )
    except KeyError as exc:
        raise ExtractionError(f"Missing term configuration value: {exc}") from exc


def extract_clearinghouse_data(
    connection: pyodbc.Connection,
    config: Dict[str, Any],
) -> Tuple[dict[str, pd.DataFrame], int]:
    """
    Execute multiple configured Clearinghouse queries and return
    file_key -> DataFrame, along with total extracted row count.
    """
    extract_config = config.get("extract", {})
    term_config = extract_config.get("term_config", {})
    queries = extract_config.get("queries", [])

    if not queries:
        raise ExtractionError("No extract.queries found in configuration.")

    extracted_data: dict[str, pd.DataFrame] = {}
    total_record_count = 0

    try:
        for query_def in queries:
            file_key = query_def.get("file_key")
            sql_template = query_def.get("sql_template")

            if not file_key:
                raise ExtractionError("A configured query is missing file_key.")

            if not sql_template or not str(sql_template).strip():
                raise ExtractionError(f"Configured SQL template for '{file_key}' is empty.")

            query_to_run = build_query_from_template(str(sql_template), term_config)
            df = pd.read_sql(query_to_run, connection)

            extracted_data[file_key] = df
            total_record_count += len(df.index)

        return extracted_data, total_record_count

    except Exception as exc:
        if isinstance(exc, ExtractionError):
            raise
        raise ExtractionError(f"Failed to extract data from SQL Server: {exc}") from exc