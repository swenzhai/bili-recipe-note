from __future__ import annotations

import argparse
import json
import sys
import types

rich_module = types.ModuleType("rich")
rich_console_module = types.ModuleType("rich.console")


class _FakeConsole:
    def print(self, *args, **kwargs):
        return None


rich_console_module.Console = _FakeConsole
rich_module.console = rich_console_module
sys.modules.setdefault("rich", rich_module)
sys.modules.setdefault("rich.console", rich_console_module)

from bili_recipe_notes import cli
from bili_recipe_notes.recipe_extractor import TranscriptSegment


class _FakeRecipe:
    def __init__(self) -> None:
        self.steps = []

    def model_dump_json(self, indent: int = 2) -> str:
        return json.dumps({"title": "demo"}, ensure_ascii=False, indent=indent)


def test_run_falls_back_when_subtitle_download_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "fetch_video_info", lambda url, cookies=None: {"title": "demo", "uploader": "up"})

    def _raise_subtitle(*args, **kwargs):
        raise RuntimeError("Subtitles are only available when logged in")

    monkeypatch.setattr(cli, "download_subtitles", _raise_subtitle)

    audio_file = tmp_path / "audio.m4a"

    def _download_audio(*args, **kwargs):
        audio_file.write_text("x", encoding="utf-8")
        return audio_file

    monkeypatch.setattr(cli, "download_audio", _download_audio)
    monkeypatch.setattr(
        cli,
        "transcribe_audio",
        lambda *args, **kwargs: [TranscriptSegment(start=0.0, end=1.0, text="先准备鸡蛋")],
    )
    monkeypatch.setattr(cli, "extract_recipe_rule_based", lambda *args, **kwargs: _FakeRecipe())
    monkeypatch.setattr(cli, "render_markdown", lambda recipe: "# demo")

    args = argparse.Namespace(
        url="https://example.com/video",
        cookies=None,
        out=str(tmp_path / "out"),
        no_screenshot=True,
        whisper_model="small",
        language="zh",
        keep_media=True,
        no_llm_summary=True,
    )

    code = cli.run(args)

    assert code == 0
    note_path = tmp_path / "out" / "demo - up" / "note.md"
    transcript_path = tmp_path / "out" / "demo - up" / "transcript.json"
    assert note_path.exists()
    assert transcript_path.exists()
    assert "先准备鸡蛋" in transcript_path.read_text(encoding="utf-8")
