from __future__ import annotations

from typing import Any, Dict

import pyodbc

from config_loader import get_env_value
from exceptions import DatabaseConnectionError


def build_connection_string(config: Dict[str, Any]) -> str:
    """
    Build a SQL Server connection string from configuration and environment variables.
    """
    sql_config = config.get("sql_server", {})

    driver = sql_config.get("driver")
    server = sql_config.get("server")
    database = sql_config.get("database")
    trusted_connection = bool(sql_config.get("trusted_connection", True))
    encrypt = "yes" if sql_config.get("encrypt", True) else "no"
    trust_server_certificate = "yes" if sql_config.get("trust_server_certificate", False) else "no"

    if not driver or not server or not database:
        raise DatabaseConnectionError("SQL Server configuration is incomplete.")

    if trusted_connection:
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"Trusted_Connection=yes;"
            f"Encrypt={encrypt};"
            f"TrustServerCertificate={trust_server_certificate};"
        )
    else:
        uid_env = sql_config.get("uid_env")
        pwd_env = sql_config.get("pwd_env")

        if not uid_env or not pwd_env:
            raise DatabaseConnectionError(
                "SQL username/password environment variable names are not configured."
            )

        username = get_env_value(uid_env, required=True)
        password = get_env_value(pwd_env, required=True)

        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            f"Encrypt={encrypt};"
            f"TrustServerCertificate={trust_server_certificate};"
        )

    return conn_str


def get_sql_connection(config: Dict[str, Any]) -> pyodbc.Connection:
    """
    Return an open pyodbc connection.
    """
    try:
        conn_str = build_connection_string(config)
        connection = pyodbc.connect(conn_str, timeout=30)
        return connection
    except pyodbc.Error as exc:
        raise DatabaseConnectionError(f"Failed to connect to SQL Server: {exc}") from exc