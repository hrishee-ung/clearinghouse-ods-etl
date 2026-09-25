from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

from exceptions import ConfigurationError


def load_config(config_path: str | Path) -> Dict[str, Any]:
    """
    Load YAML configuration from disk.
    """
    path = Path(config_path)
    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ConfigurationError("Configuration file is empty or invalid.")

    return config


def get_env_value(env_var_name: str, required: bool = True, default: str | None = None) -> str | None:
    """
    Read an environment variable safely.
    """
    value = os.getenv(env_var_name, default)
    if required and not value:
        raise ConfigurationError(f"Required environment variable is missing: {env_var_name}")
    return value


def ensure_directories(config: Dict[str, Any]) -> None:
    """
    Ensure local runtime directories exist.
    """
    paths = config.get("paths", {})
    for key in ("output", "archive", "responses", "logs"):
        dir_path = paths.get(key)
        if dir_path:
            Path(dir_path).mkdir(parents=True, exist_ok=True)