from __future__ import annotations

import stat
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import paramiko

from config_loader import get_env_value
from exceptions import SFTPConnectionError, SFTPDownloadError, SFTPUploadError


class SFTPClientManager:
    """
    Wrapper around Paramiko SSH/SFTP operations.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.ssh_client: Optional[paramiko.SSHClient] = None
        self.sftp_client: Optional[paramiko.SFTPClient] = None

    def connect_sftp(self) -> None:
        ch_config = self.config.get("clearinghouse", {})
        sftp_config = self.config.get("sftp", {})

        host = ch_config.get("host")
        port = int(ch_config.get("port", 22))
        username = ch_config.get("username")
        private_key_path = ch_config.get("private_key_path")

        connect_timeout = int(sftp_config.get("connect_timeout_seconds", 30))
        banner_timeout = int(sftp_config.get("banner_timeout_seconds", 30))
        auth_timeout = int(sftp_config.get("auth_timeout_seconds", 30))

        if not host:
            raise SFTPConnectionError("SFTP host is not configured.")
        if not username:
            raise SFTPConnectionError("SFTP username is not configured.")
        if not private_key_path:
            raise SFTPConnectionError("SFTP private key path is not configured.")

        try:
            private_key = self._load_private_key(private_key_path)

            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.RejectPolicy())

            self.ssh_client.load_system_host_keys()
            self.ssh_client.connect(
                hostname=host,
                port=port,
                username=username,
                pkey=private_key,
                timeout=connect_timeout,
                banner_timeout=banner_timeout,
                auth_timeout=auth_timeout,
                look_for_keys=False,
                allow_agent=False,
            )

            self.sftp_client = self.ssh_client.open_sftp()

        except Exception as exc:
            self.disconnect_sftp()
            raise SFTPConnectionError(f"Failed to connect to SFTP server: {exc}") from exc

    def list_remote_files(self, remote_directory: str) -> List[str]:
        if not self.sftp_client:
            raise SFTPConnectionError("SFTP client is not connected.")

        try:
            return self.sftp_client.listdir(remote_directory)
        except Exception as exc:
            raise SFTPConnectionError(
                f"Failed to list remote files in '{remote_directory}': {exc}"
            ) from exc

    def upload_file(
        self,
        local_file_path: str | Path,
        remote_directory: str,
        verify_remote: bool = True,
        max_retries: int = 3,
        retry_delay_seconds: int = 10,
    ) -> str:
        if not self.sftp_client:
            raise SFTPConnectionError("SFTP client is not connected.")

        local_path = Path(local_file_path)
        if not local_path.exists():
            raise SFTPUploadError(f"Local file not found for upload: {local_path}")

        remote_path = f"{remote_directory.rstrip('/')}/{local_path.name}"

        attempt = 0
        while attempt < max_retries:
            attempt += 1
            try:
                if self.remote_file_exists(remote_path):
                    raise SFTPUploadError(
                        f"Remote file already exists. Upload aborted to prevent duplicate: {remote_path}"
                    )

                self.sftp_client.put(str(local_path), remote_path)

                if verify_remote and not self.remote_file_exists(remote_path):
                    raise SFTPUploadError(
                        f"Upload completed but remote file verification failed: {remote_path}"
                    )

                return remote_path

            except SFTPUploadError:
                raise
            except Exception as exc:
                if attempt >= max_retries:
                    raise SFTPUploadError(
                        f"Failed to upload file after {attempt} attempt(s): {exc}"
                    ) from exc
                time.sleep(retry_delay_seconds)

        raise SFTPUploadError("Upload failed unexpectedly.")

    def download_response_files(
        self,
        remote_directory: str,
        local_directory: str | Path,
        allowed_extensions: list[str] | None = None,
    ) -> list[Path]:
        if not self.sftp_client:
            raise SFTPConnectionError("SFTP client is not connected.")

        local_dir = Path(local_directory)
        local_dir.mkdir(parents=True, exist_ok=True)

        downloaded_files: list[Path] = []

        try:
            for entry in self.sftp_client.listdir_attr(remote_directory):
                filename = entry.filename
                remote_path = f"{remote_directory.rstrip('/')}/{filename}"

                if stat.S_ISDIR(entry.st_mode):
                    continue

                if allowed_extensions and not any(
                    filename.lower().endswith(ext.lower()) for ext in allowed_extensions
                ):
                    continue

                local_path = self._build_non_overwriting_local_path(local_dir / filename)
                self.sftp_client.get(remote_path, str(local_path))
                downloaded_files.append(local_path)

            return downloaded_files

        except Exception as exc:
            raise SFTPDownloadError(
                f"Failed to download response files from '{remote_directory}': {exc}"
            ) from exc

    def remote_file_exists(self, remote_path: str) -> bool:
        if not self.sftp_client:
            raise SFTPConnectionError("SFTP client is not connected.")

        try:
            self.sftp_client.stat(remote_path)
            return True
        except FileNotFoundError:
            return False
        except IOError:
            return False

    def disconnect_sftp(self) -> None:
        if self.sftp_client:
            try:
                self.sftp_client.close()
            finally:
                self.sftp_client = None

        if self.ssh_client:
            try:
                self.ssh_client.close()
            finally:
                self.ssh_client = None

    @staticmethod
    def _load_private_key(private_key_path: str) -> paramiko.PKey:
        key_path = Path(private_key_path)
        if not key_path.exists():
            raise SFTPConnectionError(f"Private key file not found: {key_path}")

        key_loaders = (
            paramiko.Ed25519Key.from_private_key_file,
            paramiko.RSAKey.from_private_key_file,
            paramiko.ECDSAKey.from_private_key_file,
        )

        last_error: Exception | None = None
        for loader in key_loaders:
            try:
                return loader(str(key_path))
            except Exception as exc:
                last_error = exc

        raise SFTPConnectionError(
            f"Unable to load private key from path: {key_path}. {last_error}"
        )

    @staticmethod
    def _build_non_overwriting_local_path(path: Path) -> Path:
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1

        while True:
            candidate = parent / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1