from __future__ import annotations

import json
from pathlib import Path

import pytest

from bili_recipe_notes import pipeline
from bili_recipe_notes.dish_recognition import identify_dishes
from bili_recipe_notes.history import find_history_by_url, find_history_record_by_url, is_complete_output
from bili_recipe_notes.recipe_extractor import extract_recipe_rule_based
from bili_recipe_notes.storage import atomic_write_json


URL = "https://www.bilibili.com/video/BV1multidish"
ROWS = [
    {"start": 0, "end": 8, "text": "第一道炒鸡蛋，先把鸡蛋打散"},
    {"start": 8, "end": 18, "text": "然后鸡蛋下锅翻炒，炒熟出锅"},
    {"start": 30, "end": 38, "text": "第二道炒土豆，先把土豆切丝"},
    {"start": 38, "end": 50, "text": "然后土豆下锅翻炒，最后加盐出锅"},
]


def make_raw(tmp_path: Path) -> Path:
    folder = tmp_path / "recipes" / "creator" / "raw"
    folder.mkdir(parents=True)
    atomic_write_json(folder / "source.json", {"source_url": URL, "video_title": "两道家常菜", "bvid": "BV1multidish", "duration": 55})
    atomic_write_json(folder / "transcript.json", ROWS)
    atomic_write_json(folder / "job.json", {"source_url": URL, "status": "raw_ready", "stages": {"raw": {"status": "done"}}})
    return folder


def recognition(transcript, metadata, **kwargs):
    dishes = []
    for title, first in (("炒鸡蛋", 0), ("炒土豆", 2)):
        dishes.append({"title": title, "independent_dish": True, "confidence": .99,
                       "segment_ranges": [{"start_index": first, "end_index": first + 1}],
                       "evidence": [{"kind": kind, "segment_index": i, "quote": transcript[i].text}
                                    for kind, i in (("identity", first), ("cooking", first + 1))]})
    return identify_dishes(transcript, metadata, completion=lambda _: json.dumps({"status": "identified", "dishes": dishes}))


def extract(transcript, metadata, **kwargs):
    recipe = extract_recipe_rule_based(transcript, metadata)
    recipe.extraction_method = "llm"
    return recipe


def options(tmp_path: Path, **kwargs):
    return pipeline.RecipeJobOptions(URL, out=str(tmp_path / "recipes"), no_screenshot=True, require_llm=True, **kwargs)


@pytest.fixture
def models(monkeypatch):
    monkeypatch.setattr(pipeline, "identify_dishes", recognition)
    monkeypatch.setattr(pipeline, "extract_recipe_with_llm", extract)


def test_two_dishes_are_independent_and_batch_skips_only_whole_source(tmp_path, models):
    folder = make_raw(tmp_path)
    raw_bytes = [(folder / name).read_bytes() for name in ("source.json", "transcript.json")]
    result = pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    assert result.output_folder == folder
    assert len(result.recipes) == 2
    assert is_complete_output(folder)
    assert find_history_by_url(tmp_path / "recipes", URL).output_folder == folder
    assert not (folder / "recipe.json").exists()
    recipes = [json.loads(child.recipe_path.read_text()) for child in result.recipes]
    assert [r["title"] for r in recipes] == ["炒鸡蛋", "炒土豆"]
    assert recipes[0]["dish_id"] != recipes[1]["dish_id"]
    assert recipes[0]["source_time_ranges"] == [{"start": 0., "end": 18.}]
    assert recipes[1]["source_time_ranges"] == [{"start": 30., "end": 50.}]
    assert "土豆" not in result.recipes[0].final_note
    assert "鸡蛋" not in result.recipes[1].final_note
    assert json.loads(result.recipes[1].transcript_path.read_text())[0]["start"] == 30
    assert [(folder / name).read_bytes() for name in ("source.json", "transcript.json")] == raw_bytes
    batch = pipeline.run_batch(pipeline.BatchJobOptions([URL], out=str(tmp_path / "recipes"), no_screenshot=True))
    assert batch.items[0].status == "skipped"
    assert batch.items[0].output_folder == folder


def test_failed_dish_does_not_erase_success_and_resume_reuses_plan(tmp_path, models, monkeypatch):
    folder = make_raw(tmp_path)

    def partial(transcript, metadata, **kwargs):
        if metadata["dish_index"] == 2:
            raise RuntimeError("test model unavailable")
        return extract(transcript, metadata)

    monkeypatch.setattr(pipeline, "extract_recipe_with_llm", partial)
    with pytest.raises(pipeline.PipelineStageError, match="已完成 1/2"):
        pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    plan = json.loads((folder / "dish-plan.json").read_text())
    assert [d["status"] for d in plan["dishes"]] == ["done", "failed"]
    assert not is_complete_output(folder)
    assert find_history_by_url(tmp_path / "recipes", URL) is None
    assert find_history_record_by_url(tmp_path / "recipes", URL).output_folder == folder
    first = folder / plan["dishes"][0]["output_folder"]
    atomic_write_json(first / "recipe.ai-review.json", {"visibility_override": "hidden", "manual_review": {"note": "keep"}})
    snapshot = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    calls = []

    def resumed(transcript, metadata, **kwargs):
        calls.append(metadata["dish_index"])
        return extract(transcript, metadata)

    monkeypatch.setattr(pipeline, "identify_dishes", lambda *a, **k: pytest.fail("must reuse identified plan"))
    monkeypatch.setattr(pipeline, "extract_recipe_with_llm", resumed)
    result = pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    assert calls == [2]
    assert len(result.recipes) == 2
    assert snapshot == {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}


@pytest.mark.parametrize("status", ["needs_review", "unavailable"])
def test_uncertain_discovery_is_durable_and_never_merges_dishes(tmp_path, monkeypatch, status):
    folder = make_raw(tmp_path)
    monkeypatch.setattr(pipeline, "identify_dishes", lambda *a, **k: {"status": status, "reason": "菜品归属待确认", "dishes": []})
    monkeypatch.setattr(pipeline, "extract_recipe_with_llm", lambda *a, **k: pytest.fail("must not merge uncertain dishes"))
    with pytest.raises(pipeline.PipelineStageError, match="菜品归属待确认"):
        pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    assert json.loads((folder / "dish-plan.json").read_text())["status"] == status
    assert not list(folder.rglob("recipe.json"))
    assert json.loads((folder / "job.json").read_text())["stages"]["raw"]["status"] == "done"


def test_input_change_and_corrupt_plan_preserve_completed_children(tmp_path, models):
    folder = make_raw(tmp_path)
    result = pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    original = result.recipes[0].recipe_path.read_bytes()
    atomic_write_json(folder / "transcript.json", [*ROWS, {"start": 51, "end": 52, "text": "附加内容"}])
    with pytest.raises(pipeline.PipelineStageError, match="字幕或识别规则已变化"):
        pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    assert result.recipes[0].recipe_path.read_bytes() == original
    (folder / "dish-plan.json").write_text("{broken")
    with pytest.raises(Exception, match="Invalid JSON"):
        pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    assert (folder / "dish-plan.json").read_text() == "{broken"
    assert result.recipes[0].recipe_path.read_bytes() == original


def test_scoped_screenshots_share_video_and_keep_source_coordinates(tmp_path, models, monkeypatch):
    folder = make_raw(tmp_path)
    downloaded, ranges = [], []

    def download(url, media, **kwargs):
        downloaded.append(url)
        path = media / "video.mp4"
        path.write_bytes(b"synthetic video")
        return path

    def screenshots(video, steps, output, **kwargs):
        ranges.append(kwargs["time_ranges"])

    monkeypatch.setattr(pipeline, "download_lowres_video", download)
    monkeypatch.setattr(pipeline, "capture_step_screenshots", screenshots)
    monkeypatch.setattr(pipeline, "capture_finished_dish_cover", lambda *a, **k: None)
    opts = options(tmp_path)
    opts.no_screenshot = False
    result = pipeline.generate_recipe_from_raw(folder, opts)
    assert downloaded == [URL]
    assert ranges == [[{"start": 0., "end": 18.}], [{"start": 30., "end": 50.}]]
    recipe = pipeline._load_recipe(result.recipes[1].recipe_path)
    assert all(30 <= value < 50 for value in pipeline.recipe_cover_candidate_timestamps(recipe, 55))
    assert not (folder / "media").exists()


def test_failed_fresh_batch_retains_parent_for_retry(tmp_path, models, monkeypatch):
    folder = make_raw(tmp_path)
    monkeypatch.setattr(pipeline, "capture_raw_material", lambda *a, **k: pipeline.RawJobResult(folder, folder / "source.json", folder / "transcript.json", folder / "job.json"))
    monkeypatch.setattr(pipeline, "extract_recipe_with_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    result = pipeline.run_batch(pipeline.BatchJobOptions([URL], out=str(tmp_path / "recipes"), no_screenshot=True, require_llm=True, skip_existing=False))
    assert result.items[0].status == "failed"
    assert result.items[0].output_folder == folder


def test_rebuild_from_transcript_preserves_dish_identity_and_scope(tmp_path, models):
    folder = make_raw(tmp_path)
    result = pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    child = result.recipes[1].output_folder
    before = json.loads((child / "recipe.json").read_text())
    rebuilt = pipeline.regenerate_recipe_from_transcript(child)
    after = json.loads(rebuilt.recipe_path.read_text())
    for key in ("dish_id", "dish_count", "dish_index", "source_time_ranges", "source_segment_ranges"):
        assert after[key] == before[key]
    assert "鸡蛋" not in rebuilt.final_note


def test_corrupt_cached_scope_cannot_expand_into_another_dish(tmp_path, models, monkeypatch):
    folder = make_raw(tmp_path)
    result = pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    plan = json.loads((folder / "dish-plan.json").read_text())
    plan["dishes"][0]["segment_ranges"][0]["end_index"] = 3
    atomic_write_json(folder / "dish-plan.json", plan)
    snapshot = result.recipes[0].recipe_path.read_bytes()
    monkeypatch.setattr(pipeline, "extract_recipe_with_llm", lambda *a, **k: pytest.fail("bad plan must not extract"))
    with pytest.raises(pipeline.PipelineStageError, match="校验失败"):
        pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    assert result.recipes[0].recipe_path.read_bytes() == snapshot


def test_missing_plan_does_not_rediscover_over_completed_children(tmp_path, models, monkeypatch):
    folder = make_raw(tmp_path)
    result = pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    (folder / "dish-plan.json").unlink()
    snapshot = result.recipes[0].recipe_path.read_bytes()
    monkeypatch.setattr(pipeline, "identify_dishes", lambda *a, **k: pytest.fail("must restore original plan"))
    with pytest.raises(pipeline.PipelineStageError, match="识别计划缺失"):
        pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    assert result.recipes[0].recipe_path.read_bytes() == snapshot


def test_completed_child_with_changed_source_is_not_accepted_as_current(tmp_path, models):
    folder = make_raw(tmp_path)
    result = pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    first = result.recipes[0]
    atomic_write_json(first.transcript_path, ROWS)
    snapshot = first.recipe_path.read_bytes()
    with pytest.raises(pipeline.PipelineStageError, match="子菜谱来源与已保存的识别计划不一致"):
        pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    assert first.recipe_path.read_bytes() == snapshot
    assert json.loads((folder / "dish-plan.json").read_text())["dishes"][0]["status"] == "failed"


def test_repeated_url_reuses_saved_raw_and_preserves_manual_recipe(tmp_path, models, monkeypatch):
    folder = make_raw(tmp_path)
    result = pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    first = result.recipes[0]
    payload = json.loads(first.recipe_path.read_text())
    payload.update({"title": "人工修改菜名", "cover_image_status": "manual"})
    atomic_write_json(first.recipe_path, payload)
    snapshot = {path: path.read_bytes() for path in (folder / "source.json", folder / "transcript.json", first.recipe_path)}
    monkeypatch.setattr(pipeline, "capture_raw_material", lambda *a, **k: pytest.fail("must reuse saved source"))
    repeated = pipeline.generate_recipe_note(options(tmp_path))
    assert len(repeated.recipes) == 2
    assert snapshot == {path: path.read_bytes() for path in snapshot}


def test_batch_records_source_folder_before_dish_extraction(tmp_path, models, monkeypatch):
    from bili_recipe_notes.batch_queue import load_batch_state
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(tmp_path / "data"))
    folder = make_raw(tmp_path)

    def check_progress(transcript, metadata, **kwargs):
        state = load_batch_state("visible-progress")
        item = state.items[0]
        assert item.output_folder == str(folder)
        assert item.stages["raw"].status == "done"
        assert item.stages["recipe"].status == "running"
        assert (folder / "dish-plan.json").is_file()
        return extract(transcript, metadata)

    monkeypatch.setattr(pipeline, "extract_recipe_with_llm", check_progress)
    result = pipeline.run_batch(pipeline.BatchJobOptions([URL], out=str(tmp_path / "recipes"), no_screenshot=True,
                                                       batch_id="visible-progress", skip_existing=False))
    assert result.items[0].status == "done"


def test_resume_half_saved_recipe_keeps_user_edits_and_review(tmp_path, models, monkeypatch):
    folder = make_raw(tmp_path)
    result = pipeline.generate_recipe_from_raw(folder, options(tmp_path))
    child = result.recipes[0].output_folder
    recipe_path = child / "recipe.json"
    recipe = json.loads(recipe_path.read_text())
    recipe.update(title="人工修订炒蛋", cover_image_status="manual")
    atomic_write_json(recipe_path, recipe)
    (child / "note.md").write_text("# 人工备注\n保留此内容")
    atomic_write_json(child / "recipe.review.json", {"manual": "confirmed"})
    job = json.loads((child / "job.json").read_text())
    job.update(status="failed", error="interrupted before completion")
    job["stages"]["recipe"]["status"] = "failed"
    atomic_write_json(child / "job.json", job)
    snapshot = {p: p.read_bytes() for p in (recipe_path, child / "note.md", child / "recipe.review.json")}
    monkeypatch.setattr(pipeline, "extract_recipe_with_llm", lambda *a, **k: pytest.fail("must resume saved draft"))
    pipeline.generate_recipe_from_raw(folder, options(tmp_path, enable_recipe_review=True))
    assert snapshot == {p: p.read_bytes() for p in snapshot}
    assert is_complete_output(folder)
