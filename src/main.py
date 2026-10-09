from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from config_loader import ensure_directories, load_config
from database import get_sql_connection
from etl_logger import ETLDatabaseLogger
from exceptions import (
    ConfigurationError,
    DatabaseConnectionError,
    ExtractionError,
    FileGenerationError,
    SFTPConnectionError,
    SFTPDownloadError,
    SFTPUploadError,
    ValidationError,
)
from extract import extract_clearinghouse_data
from file_utils import archive_file
from logging_utils import sanitize_error_message, setup_application_logger
from sftp_client import SFTPClientManager
from transform import generate_clearinghouse_files
from validate import validate_clearinghouse_files, validate_source_data


def generate_run_id() -> str:
    return uuid4().hex


def run_etl(config: Dict[str, Any]) -> int:
    # --- TESTING FLAG ---
    skip_sftp = False  # Set to False to re-enable SFTP uploads and downloads
    # --------------------

    process_name = config.get("app", {}).get("process_name", "Clearinghouse_ETL")
    run_id = generate_run_id()
    logger = setup_application_logger(config, run_id)
    

    logger.info("Starting process. Run_ID=%s", run_id)

    ensure_directories(config)

    db_connection = None
    sftp_manager = None
    etl_db_logger = None

    extracted_data = None
    extracted_count = 0
    file_artifacts: list[dict[str, Any]] = []
    uploaded_remote_paths: list[str] = []

    try:
        db_connection = get_sql_connection(config)
        etl_db_logger = ETLDatabaseLogger(db_connection)

        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Extract Data",
            message="Beginning source extraction.",
        )
        try:
            extracted_data, extracted_count = extract_clearinghouse_data(db_connection, config)
            etl_db_logger.log_success(
                step,
                records_extracted=extracted_count,
                message=f"Successfully extracted {extracted_count} record(s) across all queries.",
            )
            logger.info("Extracted %s record(s).", extracted_count)
        except ExtractionError as exc:
            etl_db_logger.log_failure(step, error_message=str(exc))
            raise

        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Validate Source",
            message="Validating extracted source data.",
        )
        try:
            validate_source_data(extracted_data, config)
            etl_db_logger.log_success(
                step,
                records_extracted=extracted_count,
                message="Source validation passed.",
            )
            logger.info("Source validation passed.")
        except ValidationError as exc:
            etl_db_logger.log_failure(
                step,
                error_message=str(exc),
                records_extracted=extracted_count,
            )
            raise

        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Generate Files",
            message="Generating individual workbook and text files.",
        )
        try:
            file_artifacts, total_generated_records = generate_clearinghouse_files(
                extracted_data=extracted_data,
                config=config,
                run_id=run_id,
            )
            etl_db_logger.log_success(
                step,
                records_processed=total_generated_records,
                message=f"Generated {len(file_artifacts)} workbook/text file pair(s).",
            )
            logger.info("Generated %s file pair(s).", len(file_artifacts))
        except FileGenerationError as exc:
            etl_db_logger.log_failure(step, error_message=str(exc))
            raise

        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Validate Files",
            message="Validating generated workbook and text files.",
        )
        try:
            validate_clearinghouse_files(file_artifacts, config)
            etl_db_logger.log_success(
                step,
                records_processed=sum(a["record_count"] for a in file_artifacts),
                message="All generated files passed validation.",
            )
            logger.info("File validation passed.")
        except ValidationError as exc:
            etl_db_logger.log_failure(step, error_message=str(exc))
            raise

        if skip_sftp:
            logger.info("Skipping SFTP connection, upload, and download steps for testing purposes.")
        else:
            step = etl_db_logger.start_step(
                run_id=run_id,
                process_name=process_name,
                step_name="Connect SFTP",
                message="Establishing SFTP connection.",
            )
            try:
                sftp_manager = SFTPClientManager(config)
                sftp_manager.connect_sftp()
                etl_db_logger.log_success(step, message="SFTP connection established successfully.")
                logger.info("SFTP connection established.")
            except SFTPConnectionError as exc:
                etl_db_logger.log_failure(step, error_message=str(exc))
                raise

            ch_config = config.get("clearinghouse", {})
            sftp_config = config.get("sftp", {})

            for artifact in file_artifacts:
                upload_step = etl_db_logger.start_step(
                    run_id=run_id,
                    process_name=process_name,
                    step_name=f"Upload File - {artifact['file_key']}",
                    file_name=artifact["text_file_path"].name,
                    file_path=str(artifact["text_file_path"]),
                    message=f"Uploading text file for {artifact['file_key']}.",
                )
                try:
                    uploaded_remote_path = sftp_manager.upload_file(
                        local_file_path=artifact["text_file_path"],
                        remote_directory=ch_config.get("upload_directory", "."),
                        verify_remote=bool(ch_config.get("verify_remote_after_upload", True)),
                        max_retries=int(sftp_config.get("max_retries", 3)),
                        retry_delay_seconds=int(sftp_config.get("retry_delay_seconds", 10)),
                    )
                    uploaded_remote_paths.append(uploaded_remote_path)

                    etl_db_logger.log_success(
                        upload_step,
                        file_name=artifact["text_file_path"].name,
                        file_path=str(artifact["text_file_path"]),
                        remote_path=uploaded_remote_path,
                        records_uploaded=artifact["record_count"],
                        message=f"Uploaded text file for {artifact['file_key']} successfully.",
                    )
                    logger.info("Uploaded %s to %s", artifact["text_file_path"].name, uploaded_remote_path)
                except SFTPUploadError as exc:
                    etl_db_logger.log_failure(upload_step, error_message=str(exc))
                    raise

            step = etl_db_logger.start_step(
                run_id=run_id,
                process_name=process_name,
                step_name="Download Responses",
                message="Downloading available response files.",
            )
            try:
                responses_dir = config.get("paths", {}).get("responses")

                downloaded_files = sftp_manager.download_response_files(
                    remote_directory=ch_config.get("receive_directory", "receive"),
                    local_directory=responses_dir,
                    allowed_extensions=sftp_config.get("response_file_extensions", []),
                )
                etl_db_logger.log_success(
                    step,
                    records_downloaded=len(downloaded_files),
                    message=f"Downloaded {len(downloaded_files)} response file(s).",
                )
                logger.info("Downloaded %s response file(s).", len(downloaded_files))
            except SFTPDownloadError as exc:
                etl_db_logger.log_failure(step, error_message=str(exc))
                raise

        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Archive Files",
            message="Archiving generated workbook and text files after successful upload.",
        )
        try:
            archive_dir = config.get("paths", {}).get("archive")

            # Give Windows / OneDrive / antivirus a brief moment to release file handles
            time.sleep(3)

            archive_warnings: list[str] = []

            for artifact in file_artifacts:
                archived_workbook, workbook_deleted = archive_file(artifact["workbook_path"], archive_dir)
                archived_text, text_deleted = archive_file(artifact["text_file_path"], archive_dir)

                logger.info("Archived workbook to %s", archived_workbook)
                logger.info("Archived text file to %s", archived_text)

                if not workbook_deleted:
                    warning = (
                        f"Workbook source file could not be deleted after archive copy: "
                        f"{artifact['workbook_path']}"
                    )
                    archive_warnings.append(warning)
                    logger.warning(warning)

                if not text_deleted:
                    warning = (
                        f"Text source file could not be deleted after archive copy: "
                        f"{artifact['text_file_path']}"
                    )
                    archive_warnings.append(warning)
                    logger.warning(warning)

            message = f"Archived {len(file_artifacts)} workbook/text file pair(s)."
            if archive_warnings:
                message = message + " Some source files remained in output because they were locked."

            etl_db_logger.log_success(
                step,
                records_processed=sum(a["record_count"] for a in file_artifacts),
                message=message,
            )
        except OSError as exc:
            etl_db_logger.log_failure(step, error_message=str(exc))
            raise

        logger.info("Process completed successfully. Run_ID=%s", run_id)
        return 0

    except (
        ConfigurationError,
        DatabaseConnectionError,
        ExtractionError,
        ValidationError,
        FileGenerationError,
        SFTPConnectionError,
        SFTPUploadError,
        SFTPDownloadError,
    ) as exc:
        logger.error("Process failed. Run_ID=%s Error=%s", run_id, sanitize_error_message(str(exc)))
        return 1
    except Exception:
        logger.exception("Unexpected failure. Run_ID=%s", run_id)
        return 1
    finally:
        if sftp_manager:
            sftp_manager.disconnect_sftp()
        if db_connection:
            db_connection.close()


if __name__ == "__main__":
    config_file = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
    exit_code = run_etl(load_config(config_file))
    sys.exit(exit_code)