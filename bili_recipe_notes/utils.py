from __future__ import annotations

import hashlib
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


def build_output_folder_name(
    title: str,
    uploader: str | None,
    max_length: int = 120,
    *,
    video_id: str | int | None = None,
    part_id: str | int | None = None,
    part_label: str = "cid",
    source_url: str | None = None,
) -> str:
    """Build a concise folder name with a stable video identity suffix.

    ``uploader`` remains in the signature for caller compatibility, but it is
    intentionally excluded from the folder name. Bilibili titles and uploader
    names can change over time; the stable identity suffix prevents collisions.
    """
    del uploader
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff（）()·\- _]", "", title)
    safe = re.sub(r"\s+", " ", safe).strip(" ._-")
    if not safe:
        safe = "untitled"

    identity_parts: list[str] = []
    if video_id is not None and str(video_id).strip():
        cleaned_video_id = re.sub(r"[^0-9A-Za-z_-]", "", str(video_id).strip())
        if cleaned_video_id:
            identity_parts.append(cleaned_video_id)
    if part_id is not None and str(part_id).strip():
        cleaned_part_id = re.sub(r"[^0-9A-Za-z_-]", "", str(part_id).strip())
        if cleaned_part_id:
            cleaned_part_label = re.sub(r"[^0-9A-Za-z_-]", "", part_label.strip()) or "part"
            identity_parts.append(f"{cleaned_part_label}{cleaned_part_id}")
    if not identity_parts and source_url and source_url.strip():
        digest = hashlib.sha256(source_url.strip().encode("utf-8")).hexdigest()[:12]
        identity_parts.append(f"url-{digest}")

    if not identity_parts:
        return safe[:max_length]

    suffix = f"--{'-'.join(identity_parts)}"
    if len(suffix) >= max_length:
        return suffix.lstrip("-")[:max_length]
    readable_length = max_length - len(suffix)
    readable = safe[:readable_length].rstrip(" ._-") or "video"
    return f"{readable}{suffix}"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
