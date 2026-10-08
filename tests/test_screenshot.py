from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont, ImageStat

from bili_recipe_notes.recipe_extractor import RecipeStep
from bili_recipe_notes.screenshot import (
    FFMPEG_SCREENSHOT_TIMEOUT_SECONDS,
    MAX_SAVED_STEP_IMAGES,
    MAX_COVER_DIMENSION,
    MAX_SCREENSHOT_DIMENSION,
    MAX_SCREENSHOT_BYTES,
    MIN_AUTOMATIC_SCREENSHOT_SCORE,
    ScreenshotCandidate,
    capture_finished_dish_cover,
    capture_screenshot_at,
    crop_screenshot_content,
    crop_screenshot_box_content,
    finished_dish_candidate_timestamps,
    capture_step_screenshots,
    optimize_screenshot,
    optimize_cover_screenshot_content,
    optimize_screenshot_content,
    score_screenshot,
    generate_screenshot_candidates,
    subtitle_likelihood,
    select_key_step_indices,
    scale_crop_box,
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


def test_finished_dish_candidates_favor_last_steps_and_video_tail() -> None:
    steps = [
        RecipeStep(title="切配", start_time=10, end_time=30, action="切菜"),
        RecipeStep(title="炒制", start_time=40, end_time=70, action="翻炒"),
        RecipeStep(title="装盘", start_time=75, end_time=90, action="成品出锅"),
    ]

    timestamps = finished_dish_candidate_timestamps(steps, 100)

    assert min(timestamps) >= 40
    assert any(75 < value < 90 for value in timestamps)
    assert 95.0 in timestamps

    long_ad_tail = finished_dish_candidate_timestamps(steps, 300)
    assert max(long_ad_tail) < 100


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


def test_subtitle_detection_prefers_clean_candidate(monkeypatch, tmp_path: Path) -> None:
    clean = Image.effect_noise((960, 540), 55).convert("RGB")
    subtitled = clean.copy()
    draw = ImageDraw.Draw(subtitled)
    font = ImageFont.truetype("DejaVuSans.ttf", 42)
    draw.text(
        (480, 430),
        "COOK UNTIL GOLDEN",
        font=font,
        fill="white",
        stroke_width=4,
        stroke_fill="black",
        anchor="mm",
    )

    def _capture(video_path, timestamp, output_path):
        (subtitled if timestamp == 1.0 else clean).save(output_path, format="JPEG", quality=95)
        return output_path

    monkeypatch.setattr("bili_recipe_notes.screenshot.capture_screenshot_at", _capture)
    candidates = generate_screenshot_candidates(tmp_path / "video.mp4", [1.0, 2.0])

    assert candidates[0].timestamp == 2.0
    assert candidates[0].subtitle_score < candidates[1].subtitle_score
    assert subtitle_likelihood(candidates[1].content) >= 0.58


def test_menu_crop_enforces_four_by_three_and_moves_focal_area() -> None:
    source = Image.new("RGB", (1600, 900), "black")
    source.paste((240, 30, 30), (800, 0, 1600, 900))
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", quality=95)

    left_crop = crop_screenshot_content(buffer.getvalue(), zoom=1.6, horizontal_position=0, vertical_position=0.5)
    right_crop = crop_screenshot_content(buffer.getvalue(), zoom=1.6, horizontal_position=1, vertical_position=0.5)

    with Image.open(io.BytesIO(left_crop)) as left, Image.open(io.BytesIO(right_crop)) as right:
        assert left.width / left.height == pytest.approx(4 / 3, rel=0.002)
        assert right.width / right.height == pytest.approx(4 / 3, rel=0.002)
        assert ImageStat.Stat(right).mean[0] > ImageStat.Stat(left).mean[0]


def test_menu_crop_rejects_invalid_controls() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, format="JPEG")

    with pytest.raises(ValueError, match="zoom"):
        crop_screenshot_content(buffer.getvalue(), zoom=0.5)


def test_preview_crop_coordinates_preserve_high_resolution_source() -> None:
    source = Image.new("RGB", (1920, 1080), "black")
    source.paste((230, 40, 20), (480, 0, 1920, 1080))
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", quality=95)

    crop_box = scale_crop_box(
        {"left": 120, "top": 0, "width": 360, "height": 270},
        from_size=(480, 270),
        to_size=(1920, 1080),
    )
    cropped_content = crop_screenshot_box_content(buffer.getvalue(), crop_box)

    assert crop_box == {"left": 480, "top": 0, "width": 1440, "height": 1080}
    with Image.open(io.BytesIO(cropped_content)) as cropped:
        assert cropped.size == (1440, 1080)
        assert cropped.width / cropped.height == pytest.approx(4 / 3)


def test_cover_optimization_preserves_more_detail_than_step_images() -> None:
    buffer = io.BytesIO()
    Image.effect_noise((2400, 1600), 75).convert("RGB").save(buffer, format="JPEG", quality=95)

    cover_content = optimize_cover_screenshot_content(buffer.getvalue())
    step_content = optimize_screenshot_content(buffer.getvalue())

    with Image.open(io.BytesIO(cover_content)) as cover, Image.open(io.BytesIO(step_content)) as step:
        assert max(cover.size) == MAX_COVER_DIMENSION
        assert max(step.size) == MAX_SCREENSHOT_DIMENSION
        assert cover.width > step.width


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


@pytest.mark.parametrize("end_time,next_start", [(None, 40), (48, 40), (None, None)])
def test_scoped_step_candidates_stop_before_another_dish(end_time, next_start):
    step = RecipeStep(title="炒制", start_time=16, end_time=end_time, action="翻炒")
    timestamps = step_candidate_timestamps(step, next_start, time_ranges=[{"start": 10, "end": 18}, {"start": 40, "end": 48}])
    assert timestamps
    assert all(16 <= time < 18 for time in timestamps)


def test_scoped_step_in_another_dish_does_not_seek_nearest_allowed_frame():
    step = RecipeStep(title="别的菜", start_time=25, end_time=30, action="切菜")
    assert step_candidate_timestamps(step, time_ranges=[{"start": 10, "end": 18}, {"start": 40, "end": 48}]) == []


@pytest.mark.parametrize("start,end", [(10.004, 10.006), (10.001, 10.002), (0, 0.0001)])
def test_scoped_short_window_never_rounds_across_exclusive_end(start, end):
    step = RecipeStep(title="快速成品展示", start_time=start, end_time=end, action="装盘")
    values = step_candidate_timestamps(step, time_ranges=[{"start": start, "end": end}])
    assert values
    assert all(start <= value < end for value in values)


def test_finished_dish_candidates_use_own_final_range_instead_of_video_tail():
    ranges = [{"start": 10, "end": 18}, {"start": 60, "end": 70}]
    steps = [RecipeStep(title="初炸", start_time=10, end_time=18, action="出锅"),
             RecipeStep(title="复炸", start_time=60, end_time=70, action="再次下锅复炸后装盘")]
    values = finished_dish_candidate_timestamps(steps, 100, time_ranges=ranges)
    assert values
    assert any(10 <= value < 18 for value in values)
    assert any(60 <= value < 70 for value in values)
    assert all(any(span["start"] <= value < span["end"] for span in ranges) for value in values)
    assert not set(values) & {72, 82, 90, 95}
    assert 69.5 in values


@pytest.mark.parametrize("ranges", [
    [], {}, [{"start": 1}], [{"start": True, "end": 10}], [{"start": -1, "end": 10}],
    [{"start": 10, "end": 10}], [{"start": 10, "end": 5}], [{"start": 0, "end": float("inf")}],
    [{"start": float("nan"), "end": 5}], [{"start": "0", "end": 5}],
    [{"start": 0, "end": 10}, {"start": 9, "end": 15}],
    [{"start": 10, "end": 15}, {"start": 0, "end": 5}],
])
def test_invalid_scope_is_rejected_before_existing_images_are_removed(tmp_path, ranges):
    images = tmp_path / "images"
    images.mkdir()
    (images / "cover.jpg").write_bytes(b"manual cover")
    (images / "step_01.jpg").write_bytes(b"manual step")
    step = RecipeStep(title="步骤", start_time=0, end_time=5, action="炒", screenshot_path="images/step_01.jpg")
    with pytest.raises(ValueError):
        step_candidate_timestamps(step, time_ranges=ranges)
    with pytest.raises(ValueError):
        finished_dish_candidate_timestamps([step], 100, time_ranges=ranges)
    with pytest.raises(ValueError):
        capture_finished_dish_cover(tmp_path / "video.mp4", [step], images, time_ranges=ranges)
    with pytest.raises(ValueError):
        capture_step_screenshots(tmp_path / "video.mp4", [step], images, time_ranges=ranges)
    assert (images / "cover.jpg").read_bytes() == b"manual cover"
    assert (images / "step_01.jpg").read_bytes() == b"manual step"
    assert step.screenshot_path == "images/step_01.jpg"


def test_scoped_capture_keeps_original_video_seconds_for_steps_and_cover(monkeypatch, tmp_path):
    ranges = [{"start": 100, "end": 108}, {"start": 200, "end": 210}]
    steps = [RecipeStep(title="初炸", start_time=100, action="炸后出锅"),
             RecipeStep(title="复炸", start_time=200, end_time=210, action="复炸出锅")]
    captures = []

    def generate(video, timestamps, **kwargs):
        captures.append(list(timestamps))
        return [ScreenshotCandidate(time, 0.8, b"test jpeg") for time in timestamps]

    monkeypatch.setattr("bili_recipe_notes.screenshot.generate_screenshot_candidates", generate)
    capture_step_screenshots(tmp_path / "video.mp4", steps, tmp_path / "images", max_images=2, time_ranges=ranges)
    cover = capture_finished_dish_cover(tmp_path / "video.mp4", steps, tmp_path / "images",
                                       video_duration=300, time_ranges=ranges)
    assert len(captures) == 3
    assert all(100 <= time < 108 for time in captures[0])
    assert all(200 <= time < 210 for time in captures[1])
    assert all(any(span["start"] <= time < span["end"] for span in ranges) for time in captures[2])
    assert cover is not None and 100 <= cover.timestamp < 210
    assert 100 <= steps[0].screenshot_time < 108
    assert 200 <= steps[1].screenshot_time < 210


def test_adjacent_scope_boundary_belongs_to_later_span():
    step = RecipeStep(title="下一阶段", start_time=10, end_time=15, action="拌匀")
    values = step_candidate_timestamps(step, time_ranges=[{"start": 0, "end": 10}, {"start": 10, "end": 20}])
    assert values and all(10 <= time < 15 for time in values)
