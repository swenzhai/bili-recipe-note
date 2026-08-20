from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import pytest

rich_module = types.ModuleType("rich")
rich_console_module = types.ModuleType("rich.console")


class _FakeConsole:
    def print(self, *args, **kwargs):
        return None


rich_console_module.Console = _FakeConsole
rich_module.console = rich_console_module
sys.modules.setdefault("rich", rich_module)
sys.modules.setdefault("rich.console", rich_console_module)

from bili_recipe_notes import cli, pipeline
from bili_recipe_notes.batch_queue import create_batch_state, save_batch_state
from bili_recipe_notes.downloader import CreatorCrawlResult, CreatorVideo
from bili_recipe_notes.pipeline import BatchJobItemResult, BatchJobOptions, RecipeJobOptions, RecipeJobResult
from bili_recipe_notes.recipe_extractor import TranscriptSegment


def test_generate_recipe_note_falls_back_when_subtitle_download_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pipeline, "fetch_video_info", lambda url, cookies=None: {"title": "demo", "uploader": "up"})

    def _raise_subtitle(*args, **kwargs):
        raise RuntimeError("Subtitles are only available when logged in")

    monkeypatch.setattr(pipeline, "download_subtitles", _raise_subtitle)

    def _download_audio(url, output_dir: Path, cookies=None):
        audio_file = output_dir / "audio.m4a"
        audio_file.write_text("x", encoding="utf-8")
        return audio_file

    monkeypatch.setattr(pipeline, "download_audio", _download_audio)
    monkeypatch.setattr(
        pipeline,
        "transcribe_audio",
        lambda *args, **kwargs: [TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋")],
    )

    result = pipeline.generate_recipe_note(
        RecipeJobOptions(
            url="https://example.com/video",
            out=str(tmp_path / "out"),
            no_screenshot=True,
            keep_media=True,
            no_llm_summary=True,
        )
    )

    assert result.note_path.exists()
    assert result.recipe_path.exists()
    assert result.transcript_path.exists()
    assert (result.output_folder / "quality.json").exists()
    assert (result.output_folder / "media" / "audio.m4a").exists()
    assert "先准备鸡蛋" in result.transcript_path.read_text(encoding="utf-8")


def test_generate_recipe_note_cleans_or_keeps_media(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pipeline, "fetch_video_info", lambda url, cookies=None: {"title": "demo", "uploader": "up"})
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, output_dir, **kwargs: [output_dir / "subtitle.vtt"])
    monkeypatch.setattr(
        pipeline,
        "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋")],
    )

    kept = pipeline.generate_recipe_note(
        RecipeJobOptions(
            url="https://example.com/video",
            out=str(tmp_path / "keep"),
            no_screenshot=True,
            keep_media=True,
            no_llm_summary=True,
        )
    )
    cleaned = pipeline.generate_recipe_note(
        RecipeJobOptions(
            url="https://example.com/video",
            out=str(tmp_path / "clean"),
            no_screenshot=True,
            keep_media=False,
            no_llm_summary=True,
        )
    )

    assert (kept.output_folder / "media").exists()
    assert not (cleaned.output_folder / "media").exists()


def test_generate_recipe_note_uses_deterministic_fallback_when_structured_llm_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pipeline, "fetch_video_info", lambda url, cookies=None: {"title": "demo", "uploader": "up"})
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, output_dir, **kwargs: [output_dir / "subtitle.vtt"])
    monkeypatch.setattr(
        pipeline,
        "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋，然后下锅翻炒")],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_recipe_with_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("structured output unavailable")),
    )
    captured = {}

    def _summarize_note(*args, **kwargs):
        captured.update(kwargs)
        return "## 配料信息\n\n- 鸡蛋\n\n## 备菜\n\n打蛋\n\n## 烹饪\n\n炒熟\n"

    monkeypatch.setattr(pipeline, "summarize_note", _summarize_note)

    result = pipeline.generate_recipe_note(
        RecipeJobOptions(
            url="https://example.com/video",
            out=str(tmp_path / "out"),
            no_screenshot=True,
            keep_media=False,
            no_llm_summary=False,
            llm_provider="codex",
            codex_model="gpt-test",
            codex_profile="work",
        )
    )

    note = result.note_path.read_text(encoding="utf-8")
    assert "## 关键点速查" in note
    assert "用量可能未在视频中明确说明" in note
    assert captured == {}
    assert result.stage_errors and "structured output unavailable" in result.stage_errors[0]
    assert "抽取方式" not in note


def test_required_llm_failure_marks_recipe_stage_failed(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "raw"
    folder.mkdir(parents=True)
    (folder / "source.json").write_text(
        json.dumps({"source_url": "https://example.com/video", "video_title": "demo"}),
        encoding="utf-8",
    )
    (folder / "transcript.json").write_text(
        json.dumps([{"start": 0, "end": 1, "text": "先切菜，然后下锅翻炒"}]),
        encoding="utf-8",
    )
    (folder / "job.json").write_text(
        json.dumps({"status": "raw_ready", "stages": {"raw": {"status": "done"}, "recipe": {"status": "pending"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pipeline,
        "extract_recipe_with_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("empty output")),
    )

    with pytest.raises(pipeline.PipelineStageError, match="extract_recipe_llm"):
        pipeline.generate_recipe_from_raw(
            folder,
            RecipeJobOptions(
                url="https://example.com/video",
                out=str(tmp_path / "outputs"),
                no_screenshot=True,
                llm_provider="codex",
                require_llm=True,
            ),
        )

    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert job["status"] == "failed"
    assert job["stages"]["recipe"]["status"] == "failed"


def test_required_screenshot_failure_marks_recipe_stage_failed(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "raw"
    folder.mkdir(parents=True)
    (folder / "source.json").write_text(
        json.dumps({"source_url": "https://example.com/video", "video_title": "demo"}),
        encoding="utf-8",
    )
    (folder / "transcript.json").write_text(
        json.dumps([{"start": 0, "end": 1, "text": "先切菜，然后下锅翻炒"}]),
        encoding="utf-8",
    )
    (folder / "job.json").write_text(
        json.dumps({"status": "raw_ready", "stages": {"raw": {"status": "done"}, "recipe": {"status": "pending"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pipeline,
        "download_lowres_video",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("HTTP 412")),
    )

    with pytest.raises(pipeline.PipelineStageError, match="screenshot"):
        pipeline.generate_recipe_from_raw(
            folder,
            RecipeJobOptions(
                url="https://example.com/video",
                out=str(tmp_path / "outputs"),
                no_llm_summary=True,
                require_screenshot=True,
            ),
        )

    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert job["status"] == "failed"
    assert job["stages"]["recipe"]["status"] == "failed"


def test_generate_recipe_note_does_not_force_an_unrelated_fallback_image(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        pipeline,
        "fetch_video_info",
        lambda url, cookies=None: {"title": "demo", "uploader": "up", "duration": 120},
    )
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, output_dir, **kwargs: [output_dir / "subtitle.vtt"])
    monkeypatch.setattr(
        pipeline,
        "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=10.0, end=12.0, text="先准备鸡蛋，然后下锅翻炒")],
    )

    def _download_video(url, output_dir: Path, cookies=None):
        video = output_dir / "video.mp4"
        video.write_text("fake video", encoding="utf-8")
        return video

    def _reject_candidates(video_path, steps, images_dir, max_images=3):
        for step in steps:
            step.screenshot_status = "needs_review"

    monkeypatch.setattr(pipeline, "download_lowres_video", _download_video)
    monkeypatch.setattr(pipeline, "capture_step_screenshots", _reject_candidates)

    result = pipeline.generate_recipe_note(
        RecipeJobOptions(
            url="https://example.com/video",
            out=str(tmp_path / "out"),
            no_screenshot=False,
            keep_media=False,
            no_llm_summary=True,
        )
    )

    assert not list((result.output_folder / "images").glob("*.jpg"))
    assert "(images/" not in result.note_path.read_text(encoding="utf-8")
    recipe = json.loads(result.recipe_path.read_text(encoding="utf-8"))
    assert recipe["steps"][0]["screenshot_status"] == "needs_review"


def test_generate_recipe_note_keeps_image_inline_on_deterministic_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pipeline, "fetch_video_info", lambda url, cookies=None: {"title": "demo", "uploader": "up"})
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, output_dir, **kwargs: [output_dir / "subtitle.vtt"])
    monkeypatch.setattr(
        pipeline,
        "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋，然后下锅翻炒")],
    )
    monkeypatch.setattr(
        pipeline,
        "extract_recipe_with_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("structured output unavailable")),
    )

    def _download_video(url, output_dir: Path, cookies=None):
        video = output_dir / "video.mp4"
        video.write_text("fake video", encoding="utf-8")
        return video

    def _capture_steps(video_path, steps, images_dir, max_images=4):
        images_dir.mkdir(parents=True, exist_ok=True)
        image = images_dir / "step_01.jpg"
        image.write_text("image", encoding="utf-8")
        steps[0].screenshot_path = "images/step_01.jpg"

    monkeypatch.setattr(pipeline, "download_lowres_video", _download_video)
    monkeypatch.setattr(pipeline, "capture_step_screenshots", _capture_steps)
    monkeypatch.setattr(
        pipeline,
        "summarize_note",
        lambda *args, **kwargs: "## 配料信息\n\n- 鸡蛋\n\n## 备菜\n\n打蛋\n\n## 烹饪\n\n炒熟\n\n## 菜谱总结\n\n- 火不要太大\n",
    )

    result = pipeline.generate_recipe_note(
        RecipeJobOptions(
            url="https://example.com/video",
            out=str(tmp_path / "out"),
            no_screenshot=False,
            keep_media=False,
            no_llm_summary=False,
        )
    )

    note = result.note_path.read_text(encoding="utf-8")
    assert "## 步骤配图补全" not in note
    assert "(images/step_01.jpg)" in note


def test_generate_recipe_note_reports_llm_failure_detail(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pipeline, "fetch_video_info", lambda url, cookies=None: {"title": "demo", "uploader": "up"})
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, output_dir, **kwargs: [output_dir / "subtitle.vtt"])
    monkeypatch.setattr(
        pipeline,
        "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋，然后下锅翻炒")],
    )
    monkeypatch.setattr(pipeline, "summarize_note", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "get_last_llm_error", lambda: "opencode: auth failed")
    monkeypatch.setattr(
        pipeline,
        "extract_recipe_with_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("opencode: auth failed")),
    )

    result = pipeline.generate_recipe_note(
        RecipeJobOptions(
            url="https://example.com/video",
            out=str(tmp_path / "out"),
            no_screenshot=True,
            keep_media=False,
            no_llm_summary=False,
            llm_provider="opencode",
        )
    )

    assert result.stage_errors
    assert any("opencode: auth failed" in error for error in result.stage_errors)
    assert "opencode: auth failed" in (result.job_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]


def test_generate_recipe_note_uses_structured_recipe_without_freeform_rewrite(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        pipeline,
        "fetch_video_info",
        lambda url, cookies=None: {
            "title": "同名菜谱",
            "uploader": "up",
            "id": "BV1stable",
            "cid": 9988,
        },
    )
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, output_dir, **kwargs: [output_dir / "subtitle.vtt"])
    monkeypatch.setattr(
        pipeline,
        "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0.0, end=2.0, text="先切番茄，然后下锅翻炒")],
    )
    captured = {}

    def _extract(transcript, metadata, **kwargs):
        captured.update(kwargs)
        recipe = pipeline.extract_recipe_rule_based(transcript, metadata)
        recipe.extraction_method = "llm"
        return recipe

    monkeypatch.setattr(pipeline, "extract_recipe_with_llm", _extract)
    monkeypatch.setattr(
        pipeline,
        "summarize_note",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("freeform rewrite must not run")),
    )

    result = pipeline.generate_recipe_note(
        RecipeJobOptions(
            url="https://www.bilibili.com/video/BV1stable?p=1",
            out=str(tmp_path / "out"),
            no_screenshot=True,
            no_llm_summary=False,
            llm_provider="codex",
            codex_model="gpt-test",
            creator_name="统一 UP 主名",
        )
    )

    assert result.output_folder.name.endswith("BV1stable-cid9988")
    recipe = json.loads(result.recipe_path.read_text(encoding="utf-8"))
    assert recipe["extraction_method"] == "llm"
    assert recipe["creator_name"] == "统一 UP 主名"
    assert json.loads((result.output_folder / "source.json").read_text(encoding="utf-8"))["creator_name"] == "统一 UP 主名"
    assert captured["provider"] == "codex"
    assert captured["codex_model"] == "gpt-test"


def test_same_title_videos_and_parts_get_distinct_stable_folders(monkeypatch, tmp_path) -> None:
    def _info(url, cookies=None):
        if url.endswith("one"):
            return {"title": "demo", "uploader": "up", "id": "BV1one", "cid": 101}
        return {"title": "demo", "uploader": "up", "id": "BV1one", "cid": 202}

    monkeypatch.setattr(pipeline, "fetch_video_info", _info)
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, output_dir, **kwargs: [output_dir / "subtitle.vtt"])
    monkeypatch.setattr(
        pipeline,
        "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋")],
    )

    first = pipeline.generate_recipe_note(
        RecipeJobOptions(url="https://example.com/one", out=str(tmp_path / "out"), no_screenshot=True, no_llm_summary=True)
    )
    second = pipeline.generate_recipe_note(
        RecipeJobOptions(url="https://example.com/two", out=str(tmp_path / "out"), no_screenshot=True, no_llm_summary=True)
    )

    assert first.output_folder != second.output_folder
    assert first.output_folder.name.endswith("BV1one-cid101")
    assert second.output_folder.name.endswith("BV1one-cid202")


def test_stable_identity_uses_bvid_and_page_when_cid_is_unavailable() -> None:
    assert pipeline._stable_video_identity(
        {"id": "BV1multipart"},
        "https://www.bilibili.com/video/BV1multipart?p=3",
    ) == ("BV1multipart", None, "3", "p")


@pytest.mark.parametrize("keep_media", [False, True])
def test_empty_transcription_fails_and_cleans_media_according_to_option(monkeypatch, tmp_path, keep_media) -> None:
    monkeypatch.setattr(
        pipeline,
        "fetch_video_info",
        lambda url, cookies=None: {"title": "demo", "uploader": "up", "id": "BV1empty"},
    )
    monkeypatch.setattr(pipeline, "download_subtitles", lambda *args, **kwargs: [])

    def _download_audio(url, output_dir: Path, cookies=None):
        audio = output_dir / "audio.m4a"
        audio.write_bytes(b"audio")
        return audio

    monkeypatch.setattr(pipeline, "download_audio", _download_audio)
    monkeypatch.setattr(
        pipeline,
        "transcribe_audio",
        lambda *args, **kwargs: [TranscriptSegment(start=0.0, end=1.0, text="   ")],
    )

    with pytest.raises(pipeline.PipelineStageError, match="no usable text"):
        pipeline.generate_recipe_note(
            RecipeJobOptions(
                url="https://example.com/empty",
                out=str(tmp_path / str(keep_media)),
                no_screenshot=True,
                keep_media=keep_media,
                no_llm_summary=True,
            )
        )

    output_folder = next((tmp_path / str(keep_media)).iterdir())
    assert (output_folder / "media").exists() is keep_media
    assert json.loads((output_folder / "job.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_batch_skip_requires_done_job_and_complete_artifacts(monkeypatch, tmp_path) -> None:
    url = "https://example.com/video"
    folder = tmp_path / "outputs" / "existing"
    folder.mkdir(parents=True)
    (folder / "note.md").write_text("# demo", encoding="utf-8")
    (folder / "recipe.json").write_text(
        json.dumps(
            {
                "title": "demo",
                "source_url": url,
                "ingredients": [],
                "seasonings": [],
                "tools": [],
                "steps": [{"title": "步骤1", "start_time": 0, "action": "先切菜"}],
                "summary_tips": [],
                "uncertain_points": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "transcript.json").write_text("[]", encoding="utf-8")
    (folder / "job.json").write_text(json.dumps({"status": "done", "source_url": url}), encoding="utf-8")
    generated = []

    def _generate(options, log=None):
        generated.append(options.url)
        return RecipeJobResult(
            output_folder=folder,
            note_path=folder / "note.md",
            recipe_path=folder / "recipe.json",
            transcript_path=folder / "transcript.json",
            final_note="# demo",
        )

    monkeypatch.setattr(pipeline, "generate_recipe_note", _generate)

    incomplete = pipeline.run_batch(BatchJobOptions(urls=[url], out=str(tmp_path / "outputs")))
    assert incomplete.items[0].status == "done"
    assert generated == [url]

    (folder / "transcript.json").write_text(
        json.dumps([{"start": 0, "end": 1, "text": "先切菜"}], ensure_ascii=False),
        encoding="utf-8",
    )
    complete = pipeline.run_batch(BatchJobOptions(urls=[url], out=str(tmp_path / "outputs")))
    assert complete.items[0].status == "skipped"
    assert generated == [url]


def test_strict_batch_requeues_degraded_completed_recipe(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    url = "https://example.com/video"
    folder = tmp_path / "outputs" / "degraded"
    folder.mkdir(parents=True)
    (folder / "source.json").write_text(json.dumps({"source_url": url}), encoding="utf-8")
    (folder / "transcript.json").write_text(
        json.dumps([{"start": 0, "end": 1, "text": "先切菜，然后下锅"}]),
        encoding="utf-8",
    )
    (folder / "recipe.json").write_text(
        json.dumps(
            {
                "title": "demo",
                "source_url": url,
                "ingredients": [],
                "seasonings": [],
                "tools": [],
                "steps": [{"title": "步骤1", "start_time": 0, "action": "切菜"}],
                "summary_tips": [],
                "uncertain_points": [],
                "extraction_method": "rule",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "note.md").write_text("# demo", encoding="utf-8")
    (folder / "job.json").write_text(
        json.dumps(
            {
                "status": "done",
                "source_url": url,
                "stages": {"raw": {"status": "done"}, "recipe": {"status": "done"}},
                "stage_errors": ["extract_recipe_llm: empty output", "screenshot: HTTP 412"],
            }
        ),
        encoding="utf-8",
    )
    state = create_batch_state([url], {}, batch_id="strict-repair", project_root=tmp_path)
    item = state.items[0]
    item.status = "done"
    item.output_folder = str(folder)
    item.note_path = str(folder / "note.md")
    item.stages["raw"].status = "done"
    item.stages["recipe"].status = "done"
    save_batch_state(state, project_root=tmp_path)
    processed = []

    def _process(options, item_url, log=None, *, output_folder=None, stage_callback=None):
        processed.append((item_url, output_folder))
        return BatchJobItemResult(item_url, "failed", output_folder=output_folder, error="test stop")

    monkeypatch.setattr(pipeline, "_process_batch_url", _process)

    pipeline.run_batch(
        BatchJobOptions(
            urls=[],
            out=str(tmp_path / "outputs"),
            batch_id="strict-repair",
            resume_mode="resume-unfinished",
            target_stage="recipe",
            llm_provider="codex",
            require_llm=True,
            require_screenshot=True,
        )
    )

    assert processed == [(url, folder)]


def test_extract_creator_links_writes_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        pipeline,
        "extract_creator_video_links",
        lambda url, cookies=None: [
            "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://www.bilibili.com/video/BV1ab411c7mE",
        ],
    )

    links_path = pipeline.extract_creator_links(
        url="https://space.bilibili.com/123456/video",
        cookies=None,
        out=str(tmp_path / "out"),
        filename="all_links.txt",
    )

    assert links_path.read_text(encoding="utf-8").splitlines() == [
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://www.bilibili.com/video/BV1ab411c7mE",
    ]


def test_cli_run_passes_options_to_pipeline(monkeypatch, tmp_path) -> None:
    captured: list[RecipeJobOptions] = []

    def _generate_recipe_note(options: RecipeJobOptions, log=None) -> RecipeJobResult:
        captured.append(options)
        return RecipeJobResult(
            output_folder=tmp_path / "out",
            note_path=tmp_path / "out" / "note.md",
            recipe_path=tmp_path / "out" / "recipe.json",
            transcript_path=tmp_path / "out" / "transcript.json",
            final_note="# demo",
        )

    monkeypatch.setattr(cli, "generate_recipe_note", _generate_recipe_note)

    code = cli.run(
        argparse.Namespace(
            url="https://example.com/video",
            cookies="cookies.txt",
            out=str(tmp_path / "out"),
            creator_home=False,
            creator_links_file="creator_video_links.txt",
            no_screenshot=True,
            whisper_model="base",
            language="zh",
            keep_media=True,
            no_llm_summary=True,
        )
    )

    assert code == 0
    assert captured == [
        RecipeJobOptions(
            url="https://example.com/video",
            cookies="cookies.txt",
            out=str(tmp_path / "out"),
            no_screenshot=True,
            whisper_model="base",
            language="zh",
            keep_media=True,
            no_llm_summary=True,
        )
    ]


def test_cli_reads_advanced_prompt_file(monkeypatch, tmp_path) -> None:
    prompt_file = tmp_path / "advanced-prompt.txt"
    prompt_file.write_text("重点提取温度和成熟判断", encoding="utf-8")
    captured = {}

    def _generate(options, log=None):
        captured["options"] = options
        return RecipeJobResult(
            tmp_path,
            tmp_path / "note.md",
            tmp_path / "recipe.json",
            tmp_path / "transcript.json",
            "# demo",
        )

    monkeypatch.setattr(cli, "generate_recipe_note", _generate)
    args = cli.build_parser().parse_args(
        [
            "https://example.com/video",
            "--llm-provider",
            "codex",
            "--llm-extra-instructions-file",
            str(prompt_file),
            "--no-screenshot",
        ]
    )

    assert cli.run(args) == 0
    assert captured["options"].llm_provider == "codex"
    assert captured["options"].llm_cli_extra_instructions == "重点提取温度和成熟判断"


def test_ui_module_imports() -> None:
    import bili_recipe_notes.ui as ui

    assert callable(ui.main)
    assert "codex" in ui.LLM_PROVIDERS


def test_ui_regenerate_note_kwargs_preserve_llm_provider() -> None:
    import bili_recipe_notes.ui as ui
    from bili_recipe_notes.config import UIConfig

    kwargs = ui._regenerate_note_kwargs(
        UIConfig(
            enable_llm_summary=True,
            llm_provider="codex",
            codex_model="gpt-test",
            codex_profile="work",
            llm_cli_extra_instructions="严格保留证据",
        )
    )

    assert kwargs["no_llm_summary"] is False
    assert kwargs["llm_provider"] == "codex"
    assert kwargs["codex_model"] == "gpt-test"
    assert kwargs["codex_profile"] == "work"
    assert kwargs["llm_cli_extra_instructions"] == "严格保留证据"


def test_ui_cleans_ansi_error_text() -> None:
    import bili_recipe_notes.ui as ui

    assert ui._clean_error(Exception("\x1b[0;31mERROR:\x1b[0m failed")) == "ERROR: failed"


def test_capture_raw_material_stops_before_recipe_generation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        pipeline,
        "fetch_video_info",
        lambda url, cookies=None: {"title": "demo", "uploader": "up", "id": "BV1xx411c7mD"},
    )
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, output_dir, **kwargs: [output_dir / "subtitle.vtt"])
    monkeypatch.setattr(
        pipeline,
        "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋")],
    )

    result = pipeline.capture_raw_material(
        RecipeJobOptions(
            url="https://www.bilibili.com/video/BV1xx411c7mD",
            out=str(tmp_path / "outputs"),
            no_screenshot=True,
            no_llm_summary=True,
        )
    )

    assert result.source_path.is_file()
    assert result.transcript_path.is_file()
    assert not (result.output_folder / "recipe.json").exists()
    assert not (result.output_folder / "note.md").exists()
    job = json.loads(result.job_path.read_text(encoding="utf-8"))
    assert job["status"] == "raw_ready"
    assert job["stages"]["raw"]["status"] == "done"
    assert job["stages"]["recipe"]["status"] == "pending"


def test_generate_recipe_from_raw_does_not_refetch_source(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "outputs" / "raw"
    folder.mkdir(parents=True)
    (folder / "source.json").write_text(
        json.dumps(
            {
                "source_url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "video_title": "demo",
                "uploader": "up",
                "bvid": "BV1xx411c7mD",
            }
        ),
        encoding="utf-8",
    )
    (folder / "transcript.json").write_text(
        json.dumps([{"start": 0, "end": 1, "text": "先准备鸡蛋"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (folder / "job.json").write_text(
        json.dumps({"status": "raw_ready", "stages": {"raw": {"status": "done"}, "recipe": {"status": "pending"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "fetch_video_info", lambda *args, **kwargs: pytest.fail("must not fetch info"))
    monkeypatch.setattr(pipeline, "download_subtitles", lambda *args, **kwargs: pytest.fail("must not fetch subtitles"))

    result = pipeline.generate_recipe_from_raw(
        folder,
        RecipeJobOptions(
            url="https://www.bilibili.com/video/BV1xx411c7mD",
            out=str(tmp_path / "outputs"),
            no_screenshot=True,
            no_llm_summary=True,
        ),
    )

    assert result.recipe_path.is_file()
    assert result.note_path.is_file()
    assert result.output_folder.name == "demo--BV1xx411c7mD"
    assert json.loads((result.output_folder / "job.json").read_text(encoding="utf-8"))["stages"]["recipe"]["status"] == "done"


def test_creator_archive_uses_stable_folder_and_keeps_previous_list(monkeypatch, tmp_path) -> None:
    crawls = iter(
        [
            CreatorCrawlResult(
                uid="123",
                uploader="厨师",
                videos=[CreatorVideo("BV1xx411c7mD", "菜谱一", "https://www.bilibili.com/video/BV1xx411c7mD")],
            ),
            CreatorCrawlResult(
                uid="123",
                uploader="厨师改名",
                videos=[CreatorVideo("BV1ab411c7mE", "菜谱二", "https://www.bilibili.com/video/BV1ab411c7mE")],
            ),
        ]
    )
    monkeypatch.setattr(pipeline, "crawl_creator_videos", lambda *args, **kwargs: next(crawls))

    first = pipeline.crawl_and_archive_creator("https://space.bilibili.com/123/video", None, str(tmp_path / "out"))
    second = pipeline.crawl_and_archive_creator("https://space.bilibili.com/123/video", None, str(tmp_path / "out"))

    assert first.creator_dir == second.creator_dir
    assert "BV1ab411c7mE" in second.links_path.read_text(encoding="utf-8")
    assert "BV1xx411c7mD" in second.links_path.with_name("video_links.txt.bak").read_text(encoding="utf-8")
