from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

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
from bili_recipe_notes.pipeline import RecipeJobOptions, RecipeJobResult
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


def test_generate_recipe_note_adds_recipe_summary_when_llm_omits_it(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pipeline, "fetch_video_info", lambda url, cookies=None: {"title": "demo", "uploader": "up"})
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, output_dir, **kwargs: [output_dir / "subtitle.vtt"])
    monkeypatch.setattr(
        pipeline,
        "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋，然后下锅翻炒")],
    )
    monkeypatch.setattr(
        pipeline,
        "summarize_note",
        lambda *args, **kwargs: "## 配料信息\n\n- 鸡蛋\n\n## 备菜\n\n打蛋\n\n## 烹饪\n\n炒熟\n",
    )

    result = pipeline.generate_recipe_note(
        RecipeJobOptions(
            url="https://example.com/video",
            out=str(tmp_path / "out"),
            no_screenshot=True,
            keep_media=False,
            no_llm_summary=False,
        )
    )

    note = result.note_path.read_text(encoding="utf-8")
    assert "## 菜谱总结" in note
    assert "用量可能未在视频中明确说明" in note


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


def test_ui_module_imports() -> None:
    import bili_recipe_notes.ui as ui

    assert callable(ui.main)


def test_ui_cleans_ansi_error_text() -> None:
    import bili_recipe_notes.ui as ui

    assert ui._clean_error(Exception("\x1b[0;31mERROR:\x1b[0m failed")) == "ERROR: failed"
