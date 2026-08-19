from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from bili_recipe_notes.recipe_extractor import RecipeStep
from bili_recipe_notes.screenshot import (
    FFMPEG_SCREENSHOT_TIMEOUT_SECONDS,
    MAX_SAVED_STEP_IMAGES,
    MAX_SCREENSHOT_BYTES,
    MIN_AUTOMATIC_SCREENSHOT_SCORE,
    capture_screenshot_at,
    capture_step_screenshots,
    optimize_screenshot,
    score_screenshot,
    select_key_step_indices,
    step_candidate_timestamps,
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
        Image.effect_noise((640, 360), 80).convert("RGB").save(output_path, format="JPEG")
        return output_path

    monkeypatch.setattr("bili_recipe_notes.screenshot.capture_screenshot_at", _capture)
    capture_step_screenshots(tmp_path / "video.mp4", steps, images, max_images=4)

    assert select_key_step_indices(steps, 4) == [0, 3, 6, 9]
    assert len(list(images.glob("step_*.jpg"))) == 4
    assert sum(step.screenshot_path is not None for step in steps) == 4
    assert not (images / "step_99.jpg").exists()


def test_candidate_windows_use_the_step_range_and_storage_is_capped() -> None:
    step = RecipeStep(title="炒制", start_time=10, end_time=20, action="翻炒")

    timestamps = step_candidate_timestamps(step, count=5)

    assert timestamps[0] > 10
    assert timestamps[-1] < 20
    assert len(timestamps) == 5
    steps = [RecipeStep(title=str(index), start_time=float(index), action="cook") for index in range(10)]
    assert len(select_key_step_indices(steps, 99)) == MAX_SAVED_STEP_IMAGES


def test_quality_scoring_rejects_blank_frames_and_compresses_selected_images(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    detailed = tmp_path / "detailed.png"
    Image.new("RGB", (1800, 1200), "black").save(blank)
    Image.effect_noise((1800, 1200), 90).convert("RGB").save(detailed)

    blank_content = optimize_screenshot(blank)
    detailed_content = optimize_screenshot(detailed)

    assert score_screenshot(blank_content) == 0
    assert score_screenshot(detailed_content) >= MIN_AUTOMATIC_SCREENSHOT_SCORE
    assert len(detailed_content) <= MAX_SCREENSHOT_BYTES


def test_key_step_selection_prefers_visual_action_near_timeline_target() -> None:
    steps = [RecipeStep(title=f"步骤{index}", start_time=float(index), action="讲解") for index in range(7)]
    steps[2].action = "下锅翻炒至上色"
    steps[-1].action = "装盘出锅"

    selected = select_key_step_indices(steps, 3)

    assert selected == [0, 2, 6]


def test_low_quality_candidates_are_not_persisted(monkeypatch, tmp_path: Path) -> None:
    steps = [RecipeStep(title="等待", start_time=1, end_time=5, action="等待")]

    def _capture(video_path, timestamp, output_path):
        Image.new("RGB", (640, 360), "black").save(output_path, format="JPEG")
        return output_path

    monkeypatch.setattr("bili_recipe_notes.screenshot.capture_screenshot_at", _capture)
    capture_step_screenshots(tmp_path / "video.mp4", steps, tmp_path / "images", max_images=1)

    assert steps[0].screenshot_path is None
    assert steps[0].screenshot_status == "needs_review"
    assert not list((tmp_path / "images").glob("*.jpg"))
