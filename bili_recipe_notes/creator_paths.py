from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .utils import sanitize_filename


def creator_uid(metadata: dict[str, Any]) -> str | None:
    """Read the stable Bilibili account ID from source or downloader metadata."""
    owner = metadata.get("owner")
    owner_uid = owner.get("mid") if isinstance(owner, dict) else None
    for value in (
        metadata.get("creator_uid"), metadata.get("uid"), metadata.get("uploader_id"),
        metadata.get("channel_id"), owner_uid,
    ):
        text = str(value or "").strip()
        if re.fullmatch(r"[0-9]+", text):
            return text
    for key in ("uploader_url", "channel_url", "creator_url", "source_url"):
        parsed = urlparse(str(metadata.get(key) or ""))
        if parsed.hostname != "space.bilibili.com":
            continue
        match = re.match(r"/([0-9]+)(?:/|$)", parsed.path)
        if match:
            return match.group(1)
    return None


def creator_directory(root: str | Path, metadata: dict[str, Any]) -> Path:
    """Choose a readable creator group, reusing its UID after display-name changes."""
    parent = Path(root)
    uid = creator_uid(metadata)
    display_name = str(metadata.get("creator_name") or metadata.get("uploader") or metadata.get("channel") or "").strip()
    name = sanitize_filename(
        display_name or "未知作者",
        max_length=70,
    )
    if uid:
        existing = sorted(
            path for path in parent.glob(f"{uid}-*") if path.is_dir() and not path.is_symlink()
        )
        if existing:
            return existing[0]
        if (parent / uid).is_dir() and not (parent / uid).is_symlink():
            return parent / uid
        # Early archives used only a display name. Their manifest is enough
        # to resume updates without duplicating or renaming that directory.
        for manifest in sorted(parent.glob("*/creator.json")):
            if manifest.is_symlink() or manifest.parent.is_symlink():
                continue
            try:
                archived = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(archived, dict) and creator_uid(archived) == uid:
                return manifest.parent
        return parent / (f"{uid}-{name}" if display_name else uid)
    # A creator name is source data, and must not turn into a hidden/system
    # folder or a reserved Windows device name when the library is relocated.
    if (name.startswith(".") or name.lower() in {
        "creators", "curation-review", "images", "media", "handoffs", "exports", "sources",
        "con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10)),
    }):
        name = f"UP主-{name}"
    return parent / name
