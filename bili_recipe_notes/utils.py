from __future__ import annotations

import re
from pathlib import Path


def sec_to_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def sanitize_filename(name: str, max_length: int = 120) -> str:
    safe = re.sub(r"[\\/:*?\"<>|]", "_", name).strip()
    safe = re.sub(r"\s+", " ", safe)
    safe = safe.rstrip(".")
    if not safe:
        safe = "untitled"
    return safe[:max_length]


def build_output_folder_name(title: str, uploader: str | None, max_length: int = 120) -> str:
    """Build clean output folder name with only title and uploader."""
    raw = f"{title} - {uploader or 'unknown'}"
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff\- _]", "", raw)
    safe = re.sub(r"\s+", " ", safe).strip(" ._-")
    if not safe:
        safe = "untitled - unknown"
    return safe[:max_length]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
