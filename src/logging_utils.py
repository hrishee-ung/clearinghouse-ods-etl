from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict


def setup_application_logger(config: Dict[str, Any]) -> logging.Logger:
    """
    Configure and return the application logger.
    """
    app_config = config.get("app", {})
    paths_config = config.get("paths", {})

    process_name = app_config.get("process_name", "Clearinghouse_ETL")
    log_level = app_config.get("log_level", "INFO").upper()
    logs_dir = Path(paths_config.get("logs", "."))
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_file = logs_dir / f"{process_name.lower()}.log"

    logger = logging.getLogger(process_name)
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def sanitize_error_message(message: str) -> str:
    """
    Best-effort sanitization to avoid exposing secret-like values in logs.
    """
    if not message:
        return message

    redacted_tokens = [
        "PRIVATE KEY",
        "BEGIN OPENSSH PRIVATE KEY",
        "BEGIN RSA PRIVATE KEY",
        "password",
        "pwd",
        "secret",
    ]

    sanitized = message
    for token in redacted_tokens:
        sanitized = sanitized.replace(token, "[REDACTED]")

    return sanitized[:4000]