from __future__ import annotations

import json
from pathlib import Path

import pytest

from bili_recipe_notes import optimizer, pipeline
from bili_recipe_notes.batch_queue import create_batch_state, load_batch_state
from bili_recipe_notes.optimizer import OptimizeOptions, optimize_existing_note
from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.pipeline import BatchJobOptions, RawJobResult, RecipeJobResult, run_batch
from bili_recipe_notes.quality import analyze_recipe_quality, write_quality_report
from bili_recipe_notes.recipe_extractor import Recipe, RecipeIngredient, RecipeStep


def _write_recipe(folder: Path, *, complete: bool = True, note_summary: bool = True) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    recipe = Recipe(
        title="Demo",
        source_url="https://example.com/video",
        video_title="Demo Video",
        uploader="UP",
        ingredients=[RecipeIngredient(name="鸡蛋", amount="2个")] if complete else [],
        seasonings=[RecipeIngredient(name="盐", amount="少许")] if complete else [],
        tools=["炒锅"],
        steps=[
            RecipeStep(
                title="步骤1",
                start_time=0.0,
                end_time=5.0,
                action="先打散鸡蛋并加少许盐",
                heat="中火" if complete else None,
                duration="1分钟" if complete else None,
                screenshot_path="images/step_01.jpg" if complete else None,
            ),
            RecipeStep(
                title="步骤2",
                start_time=5.0,
                end_time=10.0,
                action="下锅翻炒至凝固后出锅",
                heat="中火" if complete else None,
                duration="2分钟" if complete else None,
                screenshot_path="images/step_02.jpg" if complete else None,
            ),
        ]
        if complete
        else [RecipeStep(title="步骤1", start_time=0.0, action="炒")],
        summary_tips=["火不要太大"] if complete else [],
        uncertain_points=[] if complete else ["未能稳定识别食材，请手动补充"],
    )
    (folder / "recipe.json").write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
    if complete:
        images = folder / "images"
        images.mkdir(exist_ok=True)
        (images / "step_01.jpg").write_bytes(b"image")
        (images / "step_02.jpg").write_bytes(b"image")
    note = (
        "# Demo\n\n原视频：https://example.com/video\n\n## 关键点速查\n\n- 火不要太大\n"
        if note_summary
        else "# Demo\n\n## 步骤\n\n炒\n"
    )
    (folder / "note.md").write_text(note, encoding="utf-8")


def test_quality_report_scores_complete_recipe_high(tmp_path) -> None:
    folder = tmp_path / "demo"
    _write_recipe(folder, complete=True, note_summary=True)

    report = analyze_recipe_quality(folder)

    assert report.score >= 85
    assert report.issues == []


def test_quality_report_finds_missing_fields(tmp_path) -> None:
    folder = tmp_path / "demo"
    _write_recipe(folder, complete=False, note_summary=False)

    report = analyze_recipe_quality(folder)
    codes = {issue.code for issue in report.issues}

    assert report.score < 70
    assert {"missing_ingredients", "missing_seasonings", "too_few_steps", "missing_summary"} <= codes


def test_quality_report_can_be_written(tmp_path) -> None:
    folder = tmp_path / "demo"
    _write_recipe(folder)

    path = write_quality_report(folder)

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["score"] >= 85


def test_optimize_existing_note_success_backs_up_and_updates_quality(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "demo"
    _write_recipe(folder, complete=False, note_summary=False)
    old_note = (folder / "note.md").read_text(encoding="utf-8")

    monkeypatch.setattr(
        optimizer,
        "summarize_note",
        lambda *args, **kwargs: "## 配料信息\n\n- 鸡蛋\n\n## 备菜\n\n打蛋\n\n## 烹饪\n\n炒熟\n",
    )

    result = optimize_existing_note(folder, OptimizeOptions(llm_provider="codex"))

    assert result.backup_path.read_text(encoding="utf-8") == old_note
    assert "## 关键点速查" in result.note_path.read_text(encoding="utf-8")
    assert (folder / "quality.json").exists()


def test_optimize_existing_note_failure_does_not_overwrite(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "demo"
    _write_recipe(folder, complete=False, note_summary=False)
    old_note = (folder / "note.md").read_text(encoding="utf-8")
    monkeypatch.setattr(optimizer, "summarize_note", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError):
        optimize_existing_note(folder, OptimizeOptions(llm_provider="codex"))

    assert (folder / "note.md").read_text(encoding="utf-8") == old_note
    assert (folder / "note.before-optimize.md").exists()


def test_run_batch_persists_new_batch_state(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    def _generate(options, log=None):
        folder = tmp_path / "outputs" / options.url.rsplit("/", 1)[-1]
        _write_recipe(folder)
        return RecipeJobResult(
            output_folder=folder,
            note_path=folder / "note.md",
            recipe_path=folder / "recipe.json",
            transcript_path=folder / "transcript.json",
            final_note="# Demo",
        )

    monkeypatch.setattr(pipeline, "generate_recipe_note", _generate)

    result = run_batch(BatchJobOptions(urls=["https://x/a", "https://x/b"], out=str(tmp_path / "outputs"), batch_id="batch1"))
    state = load_batch_state("batch1", project_root=tmp_path)

    assert [item.status for item in result.items] == ["done", "done"]
    assert [item.status for item in state.items] == ["done", "done"]


def test_persistent_batch_skips_database_non_recipe_sources(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    source_url = "https://www.bilibili.com/video/BV1ad"
    database = tmp_path / ".bili-recipe-notes" / "mobile-sync.sqlite3"
    store = MobileSyncStore(tmp_path, out_dir=tmp_path / "outputs", database_path=database)
    store.set_video_classifications([source_url], "non_recipe", creator_name="测试 UP")
    processed: list[str] = []
    monkeypatch.setattr(pipeline, "generate_recipe_note", lambda options, log=None: processed.append(options.url))

    result = run_batch(
        BatchJobOptions(
            urls=[source_url],
            out=str(tmp_path / "outputs"),
            batch_id="non-recipe-batch",
            source_database_path=str(database),
        )
    )
    state = load_batch_state("non-recipe-batch", project_root=tmp_path)

    assert result.items == []
    assert processed == []
    assert state.items[0].status == "non_recipe"
    assert state.items[0].stages["raw"].status == "done"
    assert state.items[0].stages["recipe"].status == "done"


def test_persistent_batch_skips_database_technique_sources(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    source_url = "https://www.bilibili.com/video/BV1technique"
    database = tmp_path / ".bili-recipe-notes" / "mobile-sync.sqlite3"
    store = MobileSyncStore(tmp_path, out_dir=tmp_path / "outputs", database_path=database)
    store.set_video_classifications([source_url], "technique")
    processed: list[str] = []
    monkeypatch.setattr(pipeline, "generate_recipe_note", lambda options, log=None: processed.append(options.url))

    result = run_batch(
        BatchJobOptions(
            urls=[source_url],
            out=str(tmp_path / "outputs"),
            batch_id="technique-batch",
            source_database_path=str(database),
        )
    )
    state = load_batch_state("technique-batch", project_root=tmp_path)

    assert result.items == []
    assert processed == []
    assert state.items[0].status == "technique"
    assert state.items[0].stages["raw"].status == "done"
    assert state.items[0].stages["recipe"].status == "done"


def test_run_batch_resume_unfinished_processes_pending_and_failed(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    state = create_batch_state(["https://x/done", "https://x/pending", "https://x/failed"], {}, batch_id="batch2", project_root=tmp_path)
    state.items[0].status = "done"
    state.items[1].status = "pending"
    state.items[2].status = "failed"
    from bili_recipe_notes.batch_queue import save_batch_state

    save_batch_state(state, project_root=tmp_path)

    processed: list[str] = []

    def _generate(options, log=None):
        processed.append(options.url)
        folder = tmp_path / "outputs" / options.url.rsplit("/", 1)[-1]
        _write_recipe(folder)
        return RecipeJobResult(folder, folder / "note.md", folder / "recipe.json", folder / "transcript.json", "# Demo")

    monkeypatch.setattr(pipeline, "generate_recipe_note", _generate)

    result = run_batch(BatchJobOptions(urls=[], out=str(tmp_path / "outputs"), batch_id="batch2", resume_mode="resume-unfinished"))

    assert [item.url for item in result.items] == ["https://x/pending", "https://x/failed"]
    assert processed == ["https://x/pending", "https://x/failed"]


def test_run_batch_retry_failed_only_processes_failed(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    state = create_batch_state(["https://x/pending", "https://x/failed"], {}, batch_id="batch3", project_root=tmp_path)
    state.items[0].status = "pending"
    state.items[1].status = "failed"
    from bili_recipe_notes.batch_queue import save_batch_state

    save_batch_state(state, project_root=tmp_path)
    processed: list[str] = []

    def _generate(options, log=None):
        processed.append(options.url)
        folder = tmp_path / "outputs" / "failed"
        _write_recipe(folder)
        return RecipeJobResult(folder, folder / "note.md", folder / "recipe.json", folder / "transcript.json", "# Demo")

    monkeypatch.setattr(pipeline, "generate_recipe_note", _generate)

    result = run_batch(BatchJobOptions(urls=[], out=str(tmp_path / "outputs"), batch_id="batch3", resume_mode="retry-failed"))

    assert [item.url for item in result.items] == ["https://x/failed"]
    assert processed == ["https://x/failed"]


def test_run_batch_can_stop_at_raw_stage_and_persist_stage_state(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    def _capture(options, log=None):
        folder = tmp_path / "outputs" / "raw"
        folder.mkdir(parents=True)
        source = folder / "source.json"
        transcript = folder / "transcript.json"
        job = folder / "job.json"
        source.write_text(json.dumps({"source_url": options.url}), encoding="utf-8")
        transcript.write_text(json.dumps([{"start": 0, "end": 1, "text": "切菜"}]), encoding="utf-8")
        job.write_text(
            json.dumps({"status": "raw_ready", "source_url": options.url, "stages": {"raw": {"status": "done"}}}),
            encoding="utf-8",
        )
        return RawJobResult(folder, source, transcript, job)

    monkeypatch.setattr(pipeline, "capture_raw_material", _capture)

    result = run_batch(
        BatchJobOptions(
            urls=["https://x/raw"],
            out=str(tmp_path / "outputs"),
            batch_id="raw-batch",
            target_stage="raw",
        )
    )
    state = load_batch_state("raw-batch", project_root=tmp_path)

    assert result.items[0].status == "raw_ready"
    assert state.items[0].status == "raw_ready"
    assert state.items[0].stages["raw"].status == "done"
    assert state.items[0].stages["recipe"].status == "pending"
