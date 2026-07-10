from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bili_recipe_notes.recipe_extractor import RecipeStep
from bili_recipe_notes.screenshot import (
    FFMPEG_SCREENSHOT_TIMEOUT_SECONDS,
    capture_screenshot_at,
    capture_step_screenshots,
    select_key_step_indices,
)


def test_capture_screenshot_uses_utf8_replace(monkeypatch, tmp_path) -> None:
    calls = []

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        Path(cmd[-1]).write_bytes(b"jpeg")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)

    output = capture_screenshot_at(tmp_path / "video.mp4", 1.2, tmp_path / "images" / "step.jpg")

    assert output == tmp_path / "images" / "step.jpg"
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["errors"] == "replace"
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["timeout"] == FFMPEG_SCREENSHOT_TIMEOUT_SECONDS


def test_capture_screenshot_rejects_empty_or_stale_output(monkeypatch, tmp_path) -> None:
    output_path = tmp_path / "images" / "step.jpg"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"stale")

    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)

    with pytest.raises(RuntimeError, match="no usable screenshot"):
        capture_screenshot_at(tmp_path / "video.mp4", 1.2, output_path)

    assert not output_path.exists()


def test_capture_step_screenshots_keeps_only_four_key_images(monkeypatch, tmp_path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    (images / "step_99.jpg").write_bytes(b"stale")
    steps = [RecipeStep(title=str(index), start_time=float(index), action="cook") for index in range(10)]

    def _capture(video_path, timestamp, output_path):
        output_path.write_bytes(b"jpeg")
        return output_path

    monkeypatch.setattr("bili_recipe_notes.screenshot.capture_screenshot_at", _capture)
    capture_step_screenshots(tmp_path / "video.mp4", steps, images, max_images=4)

    assert select_key_step_indices(steps, 4) == [0, 3, 6, 9]
    assert len(list(images.glob("step_*.jpg"))) == 4
    assert sum(step.screenshot_path is not None for step in steps) == 4
    assert not (images / "step_99.jpg").exists()
