from __future__ import annotations

import sys
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
from transform import generate_clearinghouse_file
from validate import validate_clearinghouse_file, validate_source_data


def generate_run_id() -> str:
    return uuid4().hex


def run_etl(config: Dict[str, Any]) -> int:
    process_name = config.get("app", {}).get("process_name", "Clearinghouse_ETL")
    logger = setup_application_logger(config)
    run_id = generate_run_id()

    logger.info("Starting process. Run_ID=%s", run_id)

    ensure_directories(config)

    db_connection = None
    sftp_manager = None
    etl_db_logger = None

    data = None
    extracted_count = 0
    output_file_path: Path | None = None
    generated_record_count = 0
    uploaded_remote_path: str | None = None

    try:
        db_connection = get_sql_connection(config)
        etl_db_logger = ETLDatabaseLogger(db_connection)
        logger.info("PHASE 1: configuration and startup successful.")
        #return 0
        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Extract Data",
            message="Beginning source extraction.",
        )
        try:
            data, extracted_count = extract_clearinghouse_data(db_connection, config)
            etl_db_logger.log_success(
                step,
                records_extracted=extracted_count,
                message=f"Successfully extracted {extracted_count} record(s).",
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
            validate_source_data(data, config)
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

        logger.info("PHASE 2: extraction and source validation successful.")
        #return 0

        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Generate File",
            message="Generating outbound submission file.",
        )
        try:
            output_file_path, generated_record_count = generate_clearinghouse_file(
                data=data,
                config=config,
                run_id=run_id,
            )
            etl_db_logger.log_success(
                step,
                file_name=output_file_path.name,
                file_path=str(output_file_path),
                records_processed=generated_record_count,
                message="Submission file generated successfully.",
            )
            logger.info("Generated file: %s", output_file_path)
        except FileGenerationError as exc:
            etl_db_logger.log_failure(step, error_message=str(exc))
            raise

        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Validate File",
            file_name=output_file_path.name if output_file_path else None,
            file_path=str(output_file_path) if output_file_path else None,
            message="Validating generated submission file.",
        )
        try:
            validate_clearinghouse_file(
                file_path=output_file_path,
                expected_record_count=generated_record_count,
                config=config,
            )
            etl_db_logger.log_success(
                step,
                file_name=output_file_path.name if output_file_path else None,
                file_path=str(output_file_path) if output_file_path else None,
                records_processed=generated_record_count,
                message="File validation passed.",
            )
            logger.info("File validation passed.")
        except ValidationError as exc:
            etl_db_logger.log_failure(
                step,
                error_message=str(exc),
                file_name=output_file_path.name if output_file_path else None,
                file_path=str(output_file_path) if output_file_path else None,
            )
            raise

        logger.info("PHASE 3: file generation and validation successful.")
        #return 0

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
        logger.info("PHASE 4: SFTP connection successful.")
        #return 0
        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Upload File",
            file_name=output_file_path.name if output_file_path else None,
            file_path=str(output_file_path) if output_file_path else None,
            message="Uploading submission file to Clearinghouse.",
        )
        try:
            ch_config = config.get("clearinghouse", {})
            sftp_config = config.get("sftp", {})
            uploaded_remote_path = sftp_manager.upload_file(
                local_file_path=output_file_path,
                remote_directory=ch_config.get("upload_directory", "/upload"),
                verify_remote=bool(ch_config.get("verify_remote_after_upload", True)),
                max_retries=int(sftp_config.get("max_retries", 3)),
                retry_delay_seconds=int(sftp_config.get("retry_delay_seconds", 10)),
            )
            etl_db_logger.log_success(
                step,
                file_name=output_file_path.name if output_file_path else None,
                file_path=str(output_file_path) if output_file_path else None,
                remote_path=uploaded_remote_path,
                records_uploaded=generated_record_count,
                message="File uploaded successfully.",
            )
            logger.info("Uploaded file to %s", uploaded_remote_path)
        except SFTPUploadError as exc:
            etl_db_logger.log_failure(
                step,
                error_message=str(exc),
                file_name=output_file_path.name if output_file_path else None,
                file_path=str(output_file_path) if output_file_path else None,
            )
            raise

        logger.info("PHASE 5: file upload successful.")
        #return 0

        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Download Responses",
            message="Downloading available response files.",
        )
        try:
            ch_config = config.get("clearinghouse", {})
            sftp_config = config.get("sftp", {})
            responses_dir = config.get("paths", {}).get("responses")

            downloaded_files = sftp_manager.download_response_files(
                remote_directory=ch_config.get("receive_directory", "/receive"),
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

        logger.info("PHASE 6: response download successful.")
        return 0
        
        step = etl_db_logger.start_step(
            run_id=run_id,
            process_name=process_name,
            step_name="Archive File",
            file_name=output_file_path.name if output_file_path else None,
            file_path=str(output_file_path) if output_file_path else None,
            remote_path=uploaded_remote_path,
            message="Archiving local submission file after successful upload.",
        )
        try:
            archive_dir = config.get("paths", {}).get("archive")
            archived_path = archive_file(output_file_path, archive_dir)
            etl_db_logger.log_success(
                step,
                file_name=archived_path.name,
                file_path=str(archived_path),
                remote_path=uploaded_remote_path,
                records_processed=generated_record_count,
                message="Submission file archived successfully.",
            )
            logger.info("Archived file to %s", archived_path)
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