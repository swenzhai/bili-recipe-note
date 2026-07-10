from __future__ import annotations

import json
from pathlib import Path

import pytest

from bili_recipe_notes import content_analysis, knowledge_base, pipeline
from bili_recipe_notes.config import UIConfig, load_config, save_config
from bili_recipe_notes.content_analysis import ContentAnalysisOptions, analyze_video_content
from bili_recipe_notes.exports import export_note
from bili_recipe_notes.history import scan_history
from bili_recipe_notes.knowledge_base import (
    CookingKnowledgeEntry,
    KnowledgeExtractionOptions,
    add_practice_record,
    delete_knowledge_entry,
    due_review_entries,
    export_knowledge_base,
    extract_knowledge_from_folders,
    extract_knowledge_from_video,
    load_knowledge_entries,
    merge_knowledge_entries,
    record_knowledge_review,
    related_knowledge_for_recipe,
    save_knowledge_entries,
    search_knowledge_entries,
    suggest_duplicate_groups,
    update_knowledge_entry,
    upsert_knowledge_entries,
    write_related_knowledge_to_note,
)
from bili_recipe_notes.pipeline import (
    BatchJobOptions,
    RecipeJobResult,
    recapture_step_screenshot,
    regenerate_note_from_recipe,
    regenerate_recipe_from_transcript,
    run_batch,
)
from bili_recipe_notes.recipe_extractor import Recipe, RecipeIngredient, RecipeStep, TranscriptSegment
from bili_recipe_notes.storage import CorruptDataError


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


def test_config_read_write_and_corrupt_file_is_explicit(tmp_path) -> None:
    config = UIConfig(
        out_dir="my_outputs",
        cookies="cookies.txt",
        llm_provider="codex",
        codex_model="gpt-test",
        codex_profile="work",
        llm_cli_extra_instructions="优先提取温度",
    )

    path = save_config(config, tmp_path)
    assert path.exists()
    loaded = load_config(tmp_path)
    assert loaded.out_dir == "my_outputs"
    assert loaded.cookies == "cookies.txt"
    assert loaded.llm_provider == "codex"
    assert loaded.codex_model == "gpt-test"
    assert loaded.codex_profile == "work"
    assert loaded.llm_cli_extra_instructions == "优先提取温度"

    path.write_text("{bad json", encoding="utf-8")
    with pytest.raises(CorruptDataError, match="Invalid JSON"):
        load_config(tmp_path)


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

    assert "## 关键点速查" in result.note_path.read_text(encoding="utf-8")
    assert captured["provider"] == "codex"
    assert captured["codex_model"] == "gpt-test"
    assert captured["codex_profile"] == "work"


def test_regenerate_note_reports_llm_failure_without_downloading(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    old_recipe = (folder / "recipe.json").read_text(encoding="utf-8")
    monkeypatch.setattr(pipeline, "summarize_note", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "get_last_llm_error", lambda: "opencode: command failed")

    result = regenerate_note_from_recipe(folder, no_llm_summary=False, llm_provider="opencode")

    assert result.stage_errors
    assert "command failed" in result.stage_errors[0]
    assert "Demo Recipe" in result.note_path.read_text(encoding="utf-8")
    assert (folder / "recipe.json").read_text(encoding="utf-8") == old_recipe


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


def test_knowledge_base_upsert_and_search(tmp_path) -> None:
    entry = CookingKnowledgeEntry(
        id="heat-pan",
        title="热锅再下蛋",
        category="火候",
        content="炒蛋前先把锅烧热，可以减少粘锅并帮助快速定型。",
        tags=["炒蛋", "火候"],
        source_title="Demo Recipe",
    )

    path, added, updated = upsert_knowledge_entries([entry], project_root=tmp_path)

    assert path == tmp_path / ".bili-recipe-notes" / "knowledge_base.json"
    assert added == 1
    assert updated == 0
    results = search_knowledge_entries("定型", "火候", project_root=tmp_path)
    assert [item.title for item in results] == ["热锅再下蛋"]


def test_knowledge_base_auto_dedupes_similar_entries(tmp_path) -> None:
    first = CookingKnowledgeEntry(
        id="a",
        title="热锅再下蛋",
        category="火候",
        content="炒蛋前先热锅，蛋液更快凝固。",
        tags=["鸡蛋", "火候"],
        source_title="视频A",
        source_output_folder="outputs/a",
    )
    second = CookingKnowledgeEntry(
        id="b",
        title="热锅再下蛋",
        category="火候",
        content="炒鸡蛋前热锅可以让蛋更快定型。",
        tags=["鸡蛋"],
        source_title="视频B",
        source_output_folder="outputs/b",
    )

    path, added, updated = upsert_knowledge_entries([first, second], project_root=tmp_path)
    entries = load_knowledge_entries(project_root=tmp_path)

    assert path.exists()
    assert added == 1
    assert updated == 1
    assert len(entries) == 1
    assert {ref["title"] for ref in entries[0].source_refs} == {"视频A", "视频B"}


def test_knowledge_base_edit_review_practice_export_and_delete(tmp_path) -> None:
    entry = CookingKnowledgeEntry(
        id="heat-pan",
        title="热锅再下蛋",
        category="火候",
        content="先热锅。",
        tags=["鸡蛋"],
    )
    upsert_knowledge_entries([entry], project_root=tmp_path)

    updated = update_knowledge_entry("heat-pan", {"content": "先热锅，再下蛋。", "tags": ["鸡蛋", "火候"]}, project_root=tmp_path)
    reviewed = record_knowledge_review("heat-pan", "还模糊", project_root=tmp_path)
    practiced = add_practice_record("heat-pan", "番茄炒蛋", "成功", "没有粘锅", project_root=tmp_path)
    due = due_review_entries(project_root=tmp_path)
    md = export_knowledge_base("markdown", project_root=tmp_path)
    csv_path = export_knowledge_base("csv", project_root=tmp_path)
    anki_path = export_knowledge_base("anki", category="火候", project_root=tmp_path)
    delete_knowledge_entry("heat-pan", project_root=tmp_path)

    assert updated.content == "先热锅，再下蛋。"
    assert reviewed.mastery == "还模糊"
    assert reviewed.next_review_at
    assert practiced.practice_records[0]["dish"] == "番茄炒蛋"
    assert due == []
    assert "热锅再下蛋" in md.read_text(encoding="utf-8")
    assert "热锅再下蛋" in csv_path.read_text(encoding="utf-8-sig")
    assert "热锅再下蛋" in anki_path.read_text(encoding="utf-8-sig")
    assert anki_path.name == "knowledge_base_火候_anki.tsv"
    assert load_knowledge_entries(project_root=tmp_path) == []


def test_knowledge_base_manual_merge_and_duplicate_suggestions(tmp_path) -> None:
    entries = [
        CookingKnowledgeEntry(id="a", title="热锅再下蛋", category="火候", content="炒蛋前先热锅。", source_title="A"),
        CookingKnowledgeEntry(id="b", title="热锅再下蛋", category="火候", content="炒蛋先热锅更容易定型。", source_title="B"),
    ]
    save_knowledge_entries(entries, project_root=tmp_path)

    groups = suggest_duplicate_groups(project_root=tmp_path)
    merged = merge_knowledge_entries("a", ["b"], project_root=tmp_path)
    loaded = load_knowledge_entries(project_root=tmp_path)

    assert groups and {item.id for item in groups[0]} == {"a", "b"}
    assert merged.id == "a"
    assert len(loaded) == 1
    assert {ref["title"] for ref in loaded[0].source_refs} == {"A", "B"}


def test_extract_knowledge_from_video_writes_independent_kb(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    captured = {}

    def _complete(prompt, **kwargs):
        captured["prompt"] = prompt
        return json.dumps(
            [
                {
                    "title": "热锅再下蛋",
                    "category": "火候",
                    "content": "炒蛋前先热锅，蛋液更快凝固。",
                    "rationale": "锅温足够时蛋白质快速变性。",
                    "applicable_to": ["炒蛋", "滑蛋"],
                    "evidence": "先准备鸡蛋",
                    "tags": ["鸡蛋", "火候"],
                    "confidence": 0.8,
                }
            ],
            ensure_ascii=False,
        )

    monkeypatch.setattr(knowledge_base, "complete_markdown_prompt", _complete)

    result = extract_knowledge_from_video(
        folder,
        options=KnowledgeExtractionOptions(llm_provider="opencode"),
        project_root=tmp_path,
    )

    assert result.knowledge_path == tmp_path / ".bili-recipe-notes" / "knowledge_base.json"
    assert result.added_count == 1
    assert result.updated_count == 0
    assert "只提取能迁移到其他菜" in captured["prompt"]
    loaded = load_knowledge_entries(project_root=tmp_path)
    assert loaded[0].title == "热锅再下蛋"
    assert loaded[0].source_title == "Demo Recipe"
    assert loaded[0].source_url == "https://example.com/video"
    assert loaded[0].source_output_folder == str(folder)


def test_extract_knowledge_from_folders_skips_existing_sources(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    calls = []

    def _complete(prompt, **kwargs):
        calls.append(prompt)
        return json.dumps(
            [
                {
                    "title": "热锅再下蛋",
                    "category": "火候",
                    "content": "炒蛋前先热锅。",
                    "applicable_to": ["炒蛋"],
                    "evidence": "先准备鸡蛋",
                    "tags": ["鸡蛋"],
                    "confidence": 0.8,
                }
            ],
            ensure_ascii=False,
        )

    monkeypatch.setattr(knowledge_base, "complete_markdown_prompt", _complete)

    first = extract_knowledge_from_folders([folder], project_root=tmp_path)
    second = extract_knowledge_from_folders([folder], project_root=tmp_path, skip_existing=True)

    assert first.added_count == 1
    assert second.skipped_count == 1
    assert len(calls) == 1


def test_related_knowledge_can_be_written_to_note(tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    upsert_knowledge_entries(
        [
            CookingKnowledgeEntry(
                id="heat-pan",
                title="热锅再下蛋",
                category="火候",
                content="炒蛋前先热锅。",
                tags=["鸡蛋"],
            )
        ],
        project_root=tmp_path,
    )

    related = related_knowledge_for_recipe(folder, project_root=tmp_path)
    note_path = write_related_knowledge_to_note(folder, project_root=tmp_path)

    assert related[0].title == "热锅再下蛋"
    assert "## 相关知识库条目" in note_path.read_text(encoding="utf-8")


def test_extract_knowledge_from_video_reports_llm_failure(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "demo"
    _write_recipe_folder(folder)
    monkeypatch.setattr(knowledge_base, "complete_markdown_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(knowledge_base, "get_last_llm_error", lambda: "opencode: login required")

    try:
        extract_knowledge_from_video(folder, project_root=tmp_path)
    except RuntimeError as exc:
        assert "login required" in str(exc)
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
