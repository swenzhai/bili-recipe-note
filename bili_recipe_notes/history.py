from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .quality import analyze_recipe_quality, load_quality_report, write_quality_report


@dataclass
class HistoryItem:
    output_folder: Path
    title: str
    source_url: str
    video_title: str | None
    uploader: str | None
    note_path: Path | None
    recipe_path: Path | None
    transcript_path: Path | None
    job_path: Path | None
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    quality_score: int | None = None
    quality_summary: str | None = None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def scan_history(out_dir: str | Path) -> list[HistoryItem]:
    root = Path(out_dir)
    if not root.exists():
        return []

    items: list[HistoryItem] = []
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):
        recipe_path = folder / "recipe.json"
        note_path = folder / "note.md"
        transcript_path = folder / "transcript.json"
        job_path = folder / "job.json"
        recipe = _read_json(recipe_path) if recipe_path.exists() else {}
        job = _read_json(job_path) if job_path.exists() else {}
        quality = load_quality_report(folder)
        if not quality and (recipe_path.exists() or note_path.exists()):
            quality = analyze_recipe_quality(folder)
            try:
                write_quality_report(folder, quality)
            except Exception:
                pass

        if not recipe and not note_path.exists() and not transcript_path.exists() and not job:
            continue

        title = str(recipe.get("title") or job.get("title") or folder.name)
        source_url = str(recipe.get("source_url") or job.get("source_url") or "")
        items.append(
            HistoryItem(
                output_folder=folder,
                title=title,
                source_url=source_url,
                video_title=recipe.get("video_title") or job.get("video_title"),
                uploader=recipe.get("uploader") or job.get("uploader"),
                note_path=note_path if note_path.exists() else None,
                recipe_path=recipe_path if recipe_path.exists() else None,
                transcript_path=transcript_path if transcript_path.exists() else None,
                job_path=job_path if job_path.exists() else None,
                status=str(job.get("status") or ("done" if note_path.exists() else "unknown")),
                started_at=job.get("started_at"),
                finished_at=job.get("finished_at"),
                error=job.get("error"),
                quality_score=quality.score if quality else None,
                quality_summary=quality.summary if quality else None,
            )
        )
    return items


def find_history_by_url(out_dir: str | Path, url: str) -> HistoryItem | None:
    target = url.strip()
    if not target:
        return None
    for item in scan_history(out_dir):
        if item.source_url.strip() == target and item.note_path:
            return item
    return None
