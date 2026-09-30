from __future__ import annotations

import getpass
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pyodbc

from logging_utils import sanitize_error_message


@dataclass
class LogContext:
    run_id: str
    process_name: str
    step_name: str
    start_time: datetime
    log_id: Optional[int] = None


class ETLDatabaseLogger:
    """
    Logs ETL step activity to ODS_DEV.dbo.Log_Clearinghouse_ETL.
    """

    def __init__(self, connection: pyodbc.Connection) -> None:
        self.connection = connection

    def start_step(
        self,
        run_id: str,
        process_name: str,
        step_name: str,
        file_name: str | None = None,
        file_path: str | None = None,
        remote_path: str | None = None,
        message: str | None = None,
    ) -> LogContext:
        start_time = datetime.now()
        sql = """
            INSERT INTO dbo.Log_Clearinghouse_ETL
            (
                Run_ID,
                Process_Name,
                Step_Name,
                Step_Status,
                Start_Time,
                File_Name,
                File_Path,
                Remote_Path,
                Message,
                Host_Name,
                Created_By
            )
            OUTPUT INSERTED.Log_ID
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        cursor = self.connection.cursor()
        cursor.execute(
            sql,
            run_id,
            process_name,
            step_name,
            "STARTED",
            start_time,
            file_name,
            file_path,
            remote_path,
            message,
            socket.gethostname(),
            getpass.getuser(),
        )
        row = cursor.fetchone()
        self.connection.commit()

        return LogContext(
            run_id=run_id,
            process_name=process_name,
            step_name=step_name,
            start_time=start_time,
            log_id=row[0],
        )

    def complete_step(
        self,
        context: LogContext,
        status: str,
        file_name: str | None = None,
        file_path: str | None = None,
        remote_path: str | None = None,
        records_extracted: int | None = None,
        records_processed: int | None = None,
        records_uploaded: int | None = None,
        records_downloaded: int | None = None,
        message: str | None = None,
        error_message: str | None = None,
    ) -> None:
        end_time = datetime.now()
        duration_seconds = int((end_time - context.start_time).total_seconds())

        sql = """
            UPDATE dbo.Log_Clearinghouse_ETL
            SET
                Step_Status = ?,
                End_Time = ?,
                Duration_Seconds = ?,
                File_Name = COALESCE(?, File_Name),
                File_Path = COALESCE(?, File_Path),
                Remote_Path = COALESCE(?, Remote_Path),
                Records_Extracted = ?,
                Records_Processed = ?,
                Records_Uploaded = ?,
                Records_Downloaded = ?,
                Message = COALESCE(?, Message),
                Error_Message = ?
            WHERE Log_ID = ?
        """

        cursor = self.connection.cursor()
        cursor.execute(
            sql,
            status,
            end_time,
            duration_seconds,
            file_name,
            file_path,
            remote_path,
            records_extracted,
            records_processed,
            records_uploaded,
            records_downloaded,
            message,
            sanitize_error_message(error_message or "") if error_message else None,
            context.log_id,
        )
        self.connection.commit()

    def log_success(
        self,
        context: LogContext,
        **kwargs,
    ) -> None:
        self.complete_step(context=context, status="SUCCESS", **kwargs)

    def log_failure(
        self,
        context: LogContext,
        error_message: str,
        **kwargs,
    ) -> None:
        self.complete_step(
            context=context,
            status="FAILED",
            error_message=error_message,
            **kwargs,
        )