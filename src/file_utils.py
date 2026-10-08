from __future__ import annotations

import shutil
import time
from pathlib import Path


def archive_file(
    source_file: str | Path,
    archive_directory: str | Path,
    retries: int = 8,
    delay_seconds: int = 2,
) -> tuple[Path, bool]:
    """
    Archive a file without overwriting an existing file.

    Returns:
        (archived_path, source_deleted)

    Behavior:
    - Copies source to archive
    - Verifies archive exists
    - Attempts to delete source as best effort
    - If delete fails due to file lock, returns (archived_path, False)
      instead of raising, because archive copy already succeeded
    """
    source_path = Path(source_file)
    archive_dir = Path(archive_directory)
    archive_dir.mkdir(parents=True, exist_ok=True)

    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found for archive: {source_path}")

    target_path = archive_dir / source_path.name
    target_path = build_non_overwriting_path(target_path)

    # Step 1: copy to archive
    shutil.copy2(str(source_path), str(target_path))

    if not target_path.exists():
        raise FileNotFoundError(f"Archive copy was not created as expected: {target_path}")

    # Step 2: best-effort delete original
    for attempt in range(1, retries + 1):
        try:
            if source_path.exists():
                source_path.unlink()
            return target_path, True
        except PermissionError:
            if attempt < retries:
                time.sleep(delay_seconds)
            else:
                return target_path, False

    return target_path, not source_path.exists()


def build_non_overwriting_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    counter = 1

    while True:
        candidate = path.parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1