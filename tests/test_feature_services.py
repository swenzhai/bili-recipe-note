from __future__ import annotations

import json
from pathlib import Path

from bili_recipe_notes import content_analysis, pipeline
from bili_recipe_notes.config import UIConfig, load_config, save_config
from bili_recipe_notes.content_analysis import ContentAnalysisOptions, analyze_video_content
from bili_recipe_notes.exports import export_note
from bili_recipe_notes.history import scan_history
from bili_recipe_notes.pipeline import (
    BatchJobOptions,
    RecipeJobResult,
    recapture_step_screenshot,
    regenerate_note_from_recipe,
    regenerate_recipe_from_transcript,
    run_batch,
)
from bili_recipe_notes.recipe_extractor import Recipe, RecipeIngredient, RecipeStep, TranscriptSegment


def _recipe() -> Recipe:
    return Recipe(
        title="Demo Recipe",
        source_url="https://example.com/video",
        video_title="Demo Video",
        uploader="UP",
        servings="2 servings",
        total_time="10 min",
        difficulty="easy",
        ingredients=[RecipeIngredient(name="egg", amount="2")],
        seasonings=[RecipeIngredient(name="salt")],
        tools=["pan"],
        prep_items=["beat eggs"],
        shopping_list=["egg"],
        steps=[RecipeStep(title="step", start_time=1.0, end_time=2.0, action="cook eggs")],
        summary_tips=["hot pan"],
        uncertain_points=[],
    )


def _write_recipe_folder(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    recipe = _recipe()
    (folder / "recipe.json").write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
    (folder / "transcript.json").write_text(
        json.dumps([TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋").model_dump()], ensure_ascii=False),
        encoding="utf-8",
    )
    (folder / "note.md").write_text("# Demo Recipe\n\n## 步骤\n\ncook eggs\n", encoding="utf-8")


def test_config_read_write_and_corrupt_fallback(tmp_path) -> None:
    config = UIConfig(
        out_dir="my_outputs",
        cookies="cookies.txt",
        llm_provider="codex",
        codex_model="gpt-test",
        codex_profile="work",
    )

    path = save_config(config, tmp_path)
    assert path.exists()
    loaded = load_config(tmp_path)
    assert loaded.out_dir == "my_outputs"
    assert loaded.cookies == "cookies.txt"
    assert loaded.llm_provider == "codex"
    assert loaded.codex_model == "gpt-test"
    assert loaded.codex_profile == "work"

    path.write_text("{bad json", encoding="utf-8")
    assert load_config(tmp_path) == UIConfig()


def test_scan_history_handles_new_and_legacy_outputs(tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    (folder / "job.json").write_text(
        json.dumps({"status": "done", "finished_at": "now", "source_url": "https://example.com/video"}),
        encoding="utf-8",
    )
    legacy = tmp_path / "outputs" / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "note.md").write_text("# Legacy\n", encoding="utf-8")

    items = scan_history(tmp_path / "outputs")

    assert {item.title for item in items} >= {"Demo Recipe", "legacy"}
    assert next(item for item in items if item.title == "Demo Recipe").status == "done"


def test_run_batch_skips_existing_and_continues_after_failure(monkeypatch, tmp_path) -> None:
    existing_folder = tmp_path / "outputs" / "existing"
    _write_recipe_folder(existing_folder)
    monkeypatch.setattr(
        pipeline,
        "find_history_by_url",
        lambda out, url: scan_history(tmp_path / "outputs")[0] if url.endswith("skip") else None,
    )

    generated_options = []

    def _generate(options, log=None):
        generated_options.append(options)
        if options.url.endswith("fail"):
            raise RuntimeError("boom")
        folder = tmp_path / "outputs" / "new"
        _write_recipe_folder(folder)
        return RecipeJobResult(
            output_folder=folder,
            note_path=folder / "note.md",
            recipe_path=folder / "recipe.json",
            transcript_path=folder / "transcript.json",
            final_note="# Demo Recipe",
        )

    monkeypatch.setattr(pipeline, "generate_recipe_note", _generate)

    result = run_batch(
        BatchJobOptions(
            urls=["https://x/skip", "https://x/fail", "https://x/new"],
            out=str(tmp_path / "outputs"),
            llm_provider="codex",
            codex_model="gpt-test",
            codex_profile="work",
        )
    )

    assert [item.status for item in result.items] == ["skipped", "failed", "done"]
    assert [options.codex_model for options in generated_options] == ["gpt-test", "gpt-test"]
    assert [options.codex_profile for options in generated_options] == ["work", "work"]


def test_regenerate_note_and_recipe_from_edited_files(tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)

    note_result = regenerate_note_from_recipe(folder)
    assert "Demo Recipe" in note_result.note_path.read_text(encoding="utf-8")
    assert "2 servings" in note_result.note_path.read_text(encoding="utf-8")

    recipe_result = regenerate_recipe_from_transcript(folder)
    assert recipe_result.recipe_path.exists()
    assert "先准备鸡蛋" in recipe_result.note_path.read_text(encoding="utf-8")


def test_regenerate_note_passes_codex_options(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    captured = {}

    def _summarize_note(*args, **kwargs):
        captured.update(kwargs)
        return "## 配料信息\n\n- egg\n\n## 备菜\n\nbeat eggs\n\n## 烹饪\n\ncook eggs\n"

    monkeypatch.setattr(pipeline, "summarize_note", _summarize_note)

    result = regenerate_note_from_recipe(
        folder,
        no_llm_summary=False,
        llm_provider="codex",
        codex_model="gpt-test",
        codex_profile="work",
    )

    assert "## 菜谱总结" in result.note_path.read_text(encoding="utf-8")
    assert captured["provider"] == "codex"
    assert captured["codex_model"] == "gpt-test"
    assert captured["codex_profile"] == "work"


def test_recapture_step_screenshot_updates_recipe(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    media = folder / "media"
    media.mkdir()
    (media / "video.mp4").write_text("fake video", encoding="utf-8")

    def _capture(video_path, timestamp, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"{video_path}:{timestamp}", encoding="utf-8")
        return output_path

    monkeypatch.setattr(pipeline, "capture_screenshot_at", _capture)

    image_path = recapture_step_screenshot(folder, 1, 12.5)

    assert image_path.exists()
    recipe = json.loads((folder / "recipe.json").read_text(encoding="utf-8"))
    assert recipe["steps"][0]["start_time"] == 12.5
    assert recipe["steps"][0]["screenshot_path"] == "images/step_01.jpg"


def test_analyze_video_content_writes_markdown_and_metadata(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    captured = {}

    def _complete(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "# 通用烹饪技巧\n\n- 热锅再下蛋。"

    monkeypatch.setattr(content_analysis, "complete_markdown_prompt", _complete)

    result = analyze_video_content(
        folder,
        request="总结通用技巧",
        options=ContentAnalysisOptions(llm_provider="codex", codex_model="gpt-test", output_filename="tips.md"),
    )

    assert result.analysis_path == folder / "tips.md"
    assert result.analysis_path.read_text(encoding="utf-8").startswith("# 通用烹饪技巧")
    assert json.loads((folder / "tips.json").read_text(encoding="utf-8"))["request"] == "总结通用技巧"
    assert "先准备鸡蛋" in captured["prompt"]
    assert "总结通用技巧" in captured["prompt"]
    assert captured["provider"] == "codex"
    assert captured["codex_model"] == "gpt-test"


def test_analyze_video_content_reports_llm_failure_detail(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    monkeypatch.setattr(content_analysis, "complete_markdown_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(content_analysis, "get_last_llm_error", lambda: "opencode: quota exceeded")

    try:
        analyze_video_content(folder, options=ContentAnalysisOptions(llm_provider="opencode"))
    except RuntimeError as exc:
        assert "quota exceeded" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_export_note_creates_all_formats(tmp_path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Demo Recipe\n\n## Steps\n\nCook eggs.\n", encoding="utf-8")

    obsidian = export_note(note, "obsidian")
    pdf = export_note(note, "pdf")
    docx = export_note(note, "docx")

    assert "Demo Recipe" in obsidian.read_text(encoding="utf-8")
    assert pdf.exists() and pdf.stat().st_size > 20
    assert docx.exists() and docx.stat().st_size > 20
