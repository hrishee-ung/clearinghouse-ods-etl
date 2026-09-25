from __future__ import annotations

from typing import Any, Dict, Tuple

import pandas as pd
import pyodbc

from exceptions import ExtractionError


def _quote_sql_string(value: str) -> str:
    """
    Safely quote a SQL string literal for controlled config-driven substitution.
    """
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def build_source_query(config: Dict[str, Any]) -> str:
    """
    Build the extraction SQL from a config-driven template.
    """
    extract_config = config.get("extract", {})

    source_query_template = extract_config.get("source_query_template")
    start_term = extract_config.get("start_term")
    next_enrollment_term = extract_config.get("next_enrollment_term")
    exclude_grad_terms = extract_config.get("exclude_grad_terms", [])

    if not source_query_template:
        raise ExtractionError("No extract.source_query_template found in configuration.")

    if not start_term:
        raise ExtractionError("No extract.start_term found in configuration.")

    if not next_enrollment_term:
        raise ExtractionError("No extract.next_enrollment_term found in configuration.")

    if not exclude_grad_terms:
        raise ExtractionError("No extract.exclude_grad_terms found in configuration.")

    exclude_grad_terms_sql = ", ".join(_quote_sql_string(term) for term in exclude_grad_terms)

    try:
        query = source_query_template.format(
            start_term=start_term,
            next_enrollment_term=next_enrollment_term,
            exclude_grad_terms_sql=exclude_grad_terms_sql,
        )
    except KeyError as exc:
        raise ExtractionError(f"Missing template placeholder value: {exc}") from exc

    return query


def extract_clearinghouse_data(
    connection: pyodbc.Connection,
    config: Dict[str, Any],
) -> Tuple[pd.DataFrame, int]:
    """
    Extract source data for the Clearinghouse file.
    """
    try:
        query_to_run = build_source_query(config)
        df = pd.read_sql(query_to_run, connection)
        record_count = len(df.index)
        return df, record_count
    except Exception as exc:
        if isinstance(exc, ExtractionError):
            raise
        raise ExtractionError(f"Failed to extract data from SQL Server: {exc}") from exc