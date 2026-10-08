"""Discover generated outputs in both flat and creator-grouped libraries."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterator

from .file_cache import read_json_cached

DISH_PLAN_FILE_NAME = "dish-plan.json"
OUTPUT_MARKERS = frozenset({"recipe.json", "job.json", "source.json", "transcript.json", "note.md", DISH_PLAN_FILE_NAME})
_SKIP = frozenset({"creators", "curation-review", "images", "media", "handoffs", "exports", "sources"})


def iter_output_folders(root: str | Path) -> Iterator[Path]:
    """Stop at each output, ignore symlinks, and surface inaccessible subtrees.

    Raising on an I/O failure lets the mobile index retain its last good view
    instead of interpreting an inaccessible creator directory as deletion.
    """
    base = Path(root)
    if not base.exists() or base.is_symlink():
        return

    def failed(error: OSError) -> None:
        raise error

    for directory, children, files in os.walk(base, followlinks=False, onerror=failed):
        folder = Path(directory)
        if OUTPUT_MARKERS.intersection(files):
            # Only the reserved collection directory can contain further outputs.
            # Discovery deliberately does not depend on a readable plan: a damaged
            # plan must not make already indexed child recipes look deleted.
            children[:] = ["dishes"] if "dishes" in children and not (folder / "dishes").is_symlink() else []
            yield folder
            continue
        children[:] = sorted(
            name for name in children
            if not name.startswith(".") and name not in _SKIP and not (folder / name).is_symlink()
        )


def source_output_folder(folder: str | Path) -> Path:
    """Map a self-contained child dish to its video output, preserving legacy paths."""
    path = Path(folder)
    if path.parent.name == "dishes" and any((path.parent.parent / name).exists() for name in OUTPUT_MARKERS):
        return path.parent.parent
    return path


def iter_source_output_folders(root: str | Path) -> Iterator[Path]:
    """Yield one durable output per video, never a completed subset of its dishes."""
    for folder in iter_output_folders(root):
        if source_output_folder(folder) == folder:
            yield folder


def iter_dish_output_folders(folder: str | Path) -> Iterator[Path]:
    directory = Path(folder) / "dishes"
    if directory.is_symlink() or not directory.is_dir():
        return
    for child in sorted(directory.iterdir()):
        if child.is_dir() and not child.is_symlink() and not child.name.startswith("."):
            yield child


def load_dish_plan(folder: str | Path) -> dict | None:
    """Read a plan with safe stable child paths; malformed plans are not absence."""
    path = Path(folder) / DISH_PLAN_FILE_NAME
    if path.is_symlink():
        raise ValueError("多菜计划不能是符号链接")
    try:
        plan = read_json_cached(path)
    except FileNotFoundError:
        return None
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or not isinstance(plan.get("dishes"), list):
        raise ValueError("多菜计划格式无效")
    ids: set[str] = set()
    for dish in plan["dishes"]:
        if not isinstance(dish, dict):
            raise ValueError("多菜计划条目无效")
        identity = dish.get("dish_id")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identity) or identity in ids:
            raise ValueError("多菜计划的菜品标识无效或重复")
        ids.add(identity)
        output = dish.get("output_folder", f"dishes/{identity}")
        if output != f"dishes/{identity}" and not (len(plan["dishes"]) == 1 and output == "."):
            raise ValueError("多菜计划的输出目录不匹配菜品标识")
    return plan


def review_location(folder: Path) -> tuple[Path, str]:
    """Locate the library's review state, retaining legacy flat libraries."""
    from .data_paths import recipes_dir

    folder = folder.resolve()
    configured = recipes_dir()
    for parent in folder.parents:
        if (parent / ".ai-review").exists() or parent == configured:
            return parent, folder.relative_to(parent).as_posix()
    return folder.parent, folder.name
