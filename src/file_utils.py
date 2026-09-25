from __future__ import annotations

import shutil
from pathlib import Path


def archive_file(source_file: str | Path, archive_directory: str | Path) -> Path:
    """
    Move a file to archive without overwriting an existing file.
    """
    source_path = Path(source_file)
    archive_dir = Path(archive_directory)
    archive_dir.mkdir(parents=True, exist_ok=True)

    target_path = archive_dir / source_path.name
    target_path = build_non_overwriting_path(target_path)

    shutil.move(str(source_path), str(target_path))
    return target_path


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