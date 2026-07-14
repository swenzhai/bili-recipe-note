from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .quality import analyze_recipe_quality, load_quality_report, write_quality_report
from .obsidian_archive import recipe_archive_status
from .recipe_extractor import Recipe, normalize_recipe_taxonomy


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
    workflow_status: str = "draft"
    archive_note_path: Path | None = None
    archived_at: str | None = None
    category: str = "未分类"
    cuisine: str = "未分类"
    tags: list[str] | None = None
    taste_rating: int | None = None
    difficulty_rating: int | None = None
    time_rating: int | None = None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _has_nonempty_text(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0 and bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _has_usable_transcript(path: Path) -> bool:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(raw, list) and any(
        isinstance(segment, dict) and bool(str(segment.get("text") or "").strip()) for segment in raw
    )


def _has_usable_recipe(path: Path) -> bool:
    raw = _read_json(path)
    if not raw:
        return False
    try:
        recipe = Recipe.model_validate(raw) if hasattr(Recipe, "model_validate") else Recipe(**raw)
    except Exception:
        return False
    return bool(recipe.steps)


def is_complete_output(folder: str | Path) -> bool:
    """Return whether a folder is safe to treat as a completed batch item."""
    output_folder = Path(folder)
    job = _read_json(output_folder / "job.json")
    return bool(
        job.get("status") == "done"
        and _has_usable_recipe(output_folder / "recipe.json")
        and _has_nonempty_text(output_folder / "note.md")
        and _has_usable_transcript(output_folder / "transcript.json")
    )


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
        normalized_recipe: Recipe | None = None
        if recipe:
            try:
                normalized_recipe = Recipe.model_validate(recipe) if hasattr(Recipe, "model_validate") else Recipe(**recipe)
                normalized_recipe = normalize_recipe_taxonomy(normalized_recipe)
            except Exception:
                normalized_recipe = None
        job = _read_json(job_path) if job_path.exists() else {}
        archive = _read_json(folder / "archive.json")
        workflow_status = recipe_archive_status(folder)
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
        status = str(job.get("status") or ("legacy" if note_path.exists() else "unknown"))
        if status == "done" and not is_complete_output(folder):
            status = "incomplete"
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
                status=status,
                started_at=job.get("started_at"),
                finished_at=job.get("finished_at"),
                error=job.get("error"),
                quality_score=quality.score if quality else None,
                quality_summary=quality.summary if quality else None,
                workflow_status=workflow_status,
                archive_note_path=Path(str(archive["note_path"])) if archive.get("note_path") else None,
                archived_at=str(archive.get("archived_at")) if archive.get("archived_at") else None,
                category=str(recipe.get("category") or "未分类"),
                cuisine=str(recipe.get("cuisine") or "未分类"),
                tags=[str(tag) for tag in recipe.get("tags", []) if str(tag).strip()]
                if isinstance(recipe.get("tags"), list)
                else [],
                taste_rating=normalized_recipe.taste_rating if normalized_recipe else None,
                difficulty_rating=normalized_recipe.difficulty_rating if normalized_recipe else None,
                time_rating=normalized_recipe.time_rating if normalized_recipe else None,
            )
        )
    return items


def find_history_by_url(out_dir: str | Path, url: str) -> HistoryItem | None:
    target = url.strip()
    if not target:
        return None
    for item in scan_history(out_dir):
        if item.source_url.strip() == target and item.status == "done" and is_complete_output(item.output_folder):
            return item
    return None
