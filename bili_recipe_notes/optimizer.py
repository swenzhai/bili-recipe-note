from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .llm import (
    finalize_rewritten_note,
    get_last_llm_error,
    normalize_markdown_image_paths,
    summarize_note,
)
from .markdown_writer import render_markdown
from .quality import QualityReport, analyze_recipe_quality, write_quality_report
from .recipe_extractor import Recipe
from .storage import atomic_write_text


@dataclass
class OptimizeOptions:
    llm_provider: str = "opencode"
    openai_model: str = "gpt-5.5"
    local_llm_command: str | None = None
    codex_model: str | None = None
    codex_profile: str | None = None
    llm_cli_extra_instructions: str | None = None
    no_llm_summary: bool = False


@dataclass
class OptimizeResult:
    output_folder: Path
    note_path: Path
    backup_path: Path
    quality_before: QualityReport
    quality_after: QualityReport


def _load_recipe(recipe_path: Path) -> Recipe:
    data = json.loads(recipe_path.read_text(encoding="utf-8"))
    if hasattr(Recipe, "model_validate"):
        return Recipe.model_validate(data)
    return Recipe(**data)


def optimize_existing_note(output_folder: str | Path, options: OptimizeOptions) -> OptimizeResult:
    folder = Path(output_folder)
    recipe_path = folder / "recipe.json"
    note_path = folder / "note.md"
    backup_path = folder / "note.before-optimize.md"

    if not recipe_path.exists():
        raise FileNotFoundError(f"Missing recipe.json: {recipe_path}")
    if not note_path.exists():
        raise FileNotFoundError(f"Missing note.md: {note_path}")

    quality_before = analyze_recipe_quality(folder)
    current_note = note_path.read_text(encoding="utf-8")
    shutil.copy2(note_path, backup_path)
    versions_dir = folder / ".versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    version_path = versions_dir / f"note-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.md"
    shutil.copy2(note_path, version_path)

    recipe = _load_recipe(recipe_path)
    rendered_recipe = normalize_markdown_image_paths(render_markdown(recipe))
    base_note = normalize_markdown_image_paths(current_note) or rendered_recipe
    final_note = base_note
    if not options.no_llm_summary and options.llm_provider != "none":
        llm_note = summarize_note(
            base_note,
            provider=options.llm_provider,
            openai_model=options.openai_model,
            local_llm_command=options.local_llm_command,
            codex_model=options.codex_model,
            codex_profile=options.codex_profile,
            cli_extra_instructions=options.llm_cli_extra_instructions,
        )
        if not llm_note:
            detail = get_last_llm_error()
            message = f"LLM optimize failed: {options.llm_provider}"
            if detail:
                message = f"{message}: {detail}"
            raise RuntimeError(message)
        final_note = finalize_rewritten_note(llm_note, base_note)

    atomic_write_text(note_path, final_note)
    quality_after = analyze_recipe_quality(folder)
    write_quality_report(folder, quality_after)
    return OptimizeResult(
        output_folder=folder,
        note_path=note_path,
        backup_path=version_path,
        quality_before=quality_before,
        quality_after=quality_after,
    )
