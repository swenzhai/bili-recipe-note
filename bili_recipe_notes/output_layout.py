"""Discover generated outputs in both flat and creator-grouped libraries."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

OUTPUT_MARKERS = frozenset({"recipe.json", "job.json", "source.json", "transcript.json", "note.md"})
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
            children[:] = []
            yield folder
            continue
        children[:] = sorted(
            name for name in children
            if not name.startswith(".") and name not in _SKIP and not (folder / name).is_symlink()
        )


def review_location(folder: Path) -> tuple[Path, str]:
    """Locate the library's review state, retaining legacy flat libraries."""
    from .data_paths import recipes_dir

    folder = folder.resolve()
    configured = recipes_dir()
    for parent in folder.parents:
        if (parent / ".ai-review").exists() or parent == configured:
            return parent, folder.relative_to(parent).as_posix()
    return folder.parent, folder.name
