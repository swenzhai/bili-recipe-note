"""Resolve mutable application data independently of the source checkout.

Resolution is deliberately lazy so CLI environment overrides and child workers
all use the same root. These helpers only compute paths; reads never create data.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from .file_cache import FileFingerprint, file_fingerprint, read_json_cached
from .storage import atomic_write_json, file_lock, read_json


DATA_DIR_ENV = "BILI_RECIPE_DATA_DIR"


def data_root(project_root: str | Path | None = None) -> Path:
    root = Path(project_root or Path.cwd()).expanduser().resolve()
    configured = os.environ.get(DATA_DIR_ENV, "").strip()
    path = Path(configured).expanduser() if configured else Path("data")
    return (path if path.is_absolute() else root / path).resolve()


def recipes_dir(project_root: str | Path | None = None) -> Path:
    return data_root(project_root) / "recipes"


def creators_dir(project_root: str | Path | None = None) -> Path:
    return data_root(project_root) / "creators"


def state_dir(project_root: str | Path | None = None) -> Path:
    return data_root(project_root) / "state"


def exports_dir(project_root: str | Path | None = None) -> Path:
    return data_root(project_root) / "exports"


def logs_dir(project_root: str | Path | None = None) -> Path:
    return data_root(project_root) / "logs"


def ensure_data_layout(project_root: str | Path | None = None) -> Path:
    """Remember locations before writes so references survive successive moves."""

    root = data_root(project_root)
    path = state_dir(project_root) / "layout.json"
    with file_lock(path):
        previous = read_json(path, expected_type=dict) if path.exists() else {}
        layout = dict(previous)
        recorded_roots = layout.get("data_roots") or []
        if not isinstance(recorded_roots, list):
            raise ValueError(f"Invalid data root history: {path}")
        history = [*recorded_roots, layout.get("data_root"), str(root)]
        layout["data_roots"] = list(dict.fromkeys(str(value) for value in history if value))
        layout["data_root"] = str(root)
        layout.setdefault("schema_version", 1)
        layout.setdefault("project_root", str(Path(project_root or Path.cwd()).expanduser().resolve()))
        layout.setdefault("path_mappings", {})
        if layout != previous:
            atomic_write_json(path, layout)
    return path


def _path_relocations(project_root: str | Path | None) -> dict[str, str]:
    current = data_root(project_root)
    return _compiled_path_relocations(str(current), file_fingerprint(current / "state" / "layout.json"))


@lru_cache(maxsize=16)
def _compiled_path_relocations(current_root: str, fingerprint: FileFingerprint | None) -> dict[str, str]:
    # The fingerprint is part of the cache key: updated layouts and newly
    # mounted libraries invalidate compiled mappings without scanning them per
    # recipe. Failed reads are deliberately not cached by lru_cache.
    current = Path(current_root)
    layout_path = current / "state" / "layout.json"
    try:
        layout = read_json_cached(layout_path)
    except FileNotFoundError:
        layout = {}
    if not isinstance(layout, dict):
        raise ValueError(f"Invalid data layout: {layout_path}")
    pairs: dict[str, str] = {}
    previous_roots = layout.get("data_roots") or []
    if not isinstance(previous_roots, list):
        raise ValueError(f"Invalid data root history: {layout_path}")
    for root in [*previous_roots, layout.get("data_root")]:
        previous_root = str(root or "").replace("\\", "/").rstrip("/")
        if previous_root:
            pairs[previous_root] = str(current)
    previous_project = str(layout.get("project_root") or "").replace("\\", "/").rstrip("/")
    mappings = layout.get("path_mappings") or {}
    if not isinstance(mappings, dict):
        raise ValueError(f"Invalid path mappings: {layout_path}")
    for source, relative in mappings.items():
        source = str(source).replace("\\", "/").rstrip("/")
        if not source:
            raise ValueError(f"Empty source path in data layout: {layout_path}")
        destination = (current / str(relative).replace("\\", "/")).resolve()
        if not destination.is_relative_to(current):
            raise ValueError(f"Data layout destination escapes data directory: {relative}")
        pairs[source] = str(destination)
        if previous_project and source.startswith(previous_project + "/"):
            pairs[source[len(previous_project) + 1:]] = str(destination)
    for directory in ("recipes", "state", "creators", "exports", "logs", "sources"):
        pairs[f"data/{directory}"] = str(current / directory)
    return pairs


def relocate_value(value: Any, project_root: str | Path | None = None) -> Any:
    """Rebase complete stored paths while preserving prose, URLs and JSON shape."""

    replacements = _path_relocations(project_root)

    def relocate(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: relocate(child) for key, child in item.items()}
        if isinstance(item, list):
            return [relocate(child) for child in item]
        if not isinstance(item, (str, Path)):
            return item
        text = str(item).replace("\\", "/")
        source = text.rstrip("/")
        while source:
            destination = replacements.get(source)
            if destination is not None:
                suffix = text[len(source):].lstrip("/")
                return str(Path(destination) / suffix) if suffix else destination
            source = source.rpartition("/")[0]
        return item

    return relocate(value)


def resolve_stored_path(value: str | Path, project_root: str | Path | None = None) -> Path:
    """Resolve a persisted path after the entire data directory has been moved."""

    path = Path(relocate_value(value, project_root)).expanduser()
    root = Path(project_root or Path.cwd()).expanduser().resolve()
    return (path if path.is_absolute() else root / path).resolve()
