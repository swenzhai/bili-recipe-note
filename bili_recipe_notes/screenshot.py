from __future__ import annotations

import io
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat
from rich.console import Console

from .recipe_extractor import RecipeStep

console = Console()
FFMPEG_SCREENSHOT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_STEP_IMAGES = 3
MAX_SAVED_STEP_IMAGES = 4
DEFAULT_CANDIDATES_PER_STEP = 5
MIN_AUTOMATIC_SCREENSHOT_SCORE = 0.38
MIN_AUTOMATIC_COVER_SCORE = 0.34
MAX_SCREENSHOT_DIMENSION = 1280
MAX_SCREENSHOT_BYTES = 450 * 1024
MAX_COVER_DIMENSION = 1920
MAX_COVER_BYTES = 1536 * 1024
SUBTITLE_SCORE_PENALTY = 0.32
VISUAL_STEP_KEYWORDS = {
    "装盘": 6,
    "出锅": 6,
    "成品": 6,
    "收汁": 5,
    "上色": 5,
    "翻炒": 4,
    "煎": 4,
    "炸": 4,
    "烤": 4,
    "下锅": 3,
    "倒入": 3,
    "搅拌": 2,
    "切": 2,
    "腌": 2,
    "静置": -2,
    "等待": -2,
}
FINISHED_DISH_KEYWORDS = {"成品", "装盘", "摆盘", "出锅", "盛出", "上桌", "完成", "收汁"}


@dataclass(frozen=True)
class ScreenshotCandidate:
    timestamp: float | None
    score: float
    content: bytes
    subtitle_score: float = 0.0


def capture_screenshot_at(video_path: Path, timestamp: float, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, timestamp)}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_path),
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FFMPEG_SCREENSHOT_TIMEOUT_SECONDS,
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg produced no usable screenshot: {output_path}")
    return output_path


def _encoded_jpeg(image: Image.Image, quality: int) -> bytes:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
    return output.getvalue()


def _optimize_image(
    source: Image.Image,
    *,
    max_dimension: int = MAX_SCREENSHOT_DIMENSION,
    max_bytes: int = MAX_SCREENSHOT_BYTES,
) -> bytes:
    image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    content = b""
    for quality in (84, 78, 72, 66, 60):
        content = _encoded_jpeg(image, quality)
        if len(content) <= max_bytes:
            return content
    while len(content) > max_bytes and min(image.size) > 180:
        image.thumbnail(
            (max(180, int(image.width * 0.82)), max(180, int(image.height * 0.82))),
            Image.Resampling.LANCZOS,
        )
        content = _encoded_jpeg(image, 60)
    if len(content) > max_bytes:
        content = _encoded_jpeg(image, 42)
    if len(content) > max_bytes:
        raise ValueError("screenshot could not be compressed within the storage limit")
    return content


def optimize_screenshot(path: Path) -> bytes:
    """Normalize a selected frame so persistent recipe images stay compact."""

    with Image.open(path) as source:
        return _optimize_image(source)


def optimize_screenshot_content(content: bytes) -> bytes:
    with Image.open(io.BytesIO(content)) as source:
        return _optimize_image(source)


def optimize_cover_screenshot(path: Path) -> bytes:
    with Image.open(path) as source:
        return _optimize_image(source, max_dimension=MAX_COVER_DIMENSION, max_bytes=MAX_COVER_BYTES)


def optimize_cover_screenshot_content(content: bytes) -> bytes:
    with Image.open(io.BytesIO(content)) as source:
        return _optimize_image(source, max_dimension=MAX_COVER_DIMENSION, max_bytes=MAX_COVER_BYTES)


def crop_screenshot_content(
    content: bytes,
    *,
    zoom: float = 1.0,
    horizontal_position: float = 0.5,
    vertical_position: float = 0.5,
    aspect_ratio: float = 4 / 3,
) -> bytes:
    """Crop around a chosen focal point while enforcing the menu-card ratio."""

    if not 1.0 <= float(zoom) <= 4.0:
        raise ValueError("zoom must be between 1 and 4")
    if not 0.0 <= float(horizontal_position) <= 1.0:
        raise ValueError("horizontal_position must be between 0 and 1")
    if not 0.0 <= float(vertical_position) <= 1.0:
        raise ValueError("vertical_position must be between 0 and 1")
    if float(aspect_ratio) <= 0:
        raise ValueError("aspect_ratio must be positive")
    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if image.width / image.height >= aspect_ratio:
            base_height = float(image.height)
            base_width = base_height * aspect_ratio
        else:
            base_width = float(image.width)
            base_height = base_width / aspect_ratio
        crop_width = max(2, min(image.width, int(round(base_width / zoom))))
        crop_height = max(2, min(image.height, int(round(base_height / zoom))))
        left = int(round((image.width - crop_width) * horizontal_position))
        top = int(round((image.height - crop_height) * vertical_position))
        cropped = image.crop((left, top, left + crop_width, top + crop_height))
        return _optimize_image(cropped)


def score_screenshot(content: bytes) -> float:
    """Estimate whether a frame is readable, detailed and visually informative."""

    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((320, 320), Image.Resampling.BILINEAR)
        gray = ImageOps.grayscale(image)
        gray_stats = ImageStat.Stat(gray)
        brightness = float(gray_stats.mean[0])
        contrast = float(gray_stats.stddev[0])
        entropy = float(gray.entropy())
        edge_stats = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
        edge_detail = float(edge_stats.stddev[0])
        saturation = float(ImageStat.Stat(image.convert("HSV").getchannel("S")).mean[0])

    if brightness < 14 or brightness > 245 or contrast < 4 or entropy < 2:
        return 0.0
    brightness_score = max(0.0, 1.0 - abs(brightness - 128.0) / 128.0)
    contrast_score = min(1.0, contrast / 55.0)
    entropy_score = min(1.0, max(0.0, (entropy - 2.0) / 5.0))
    detail_score = min(1.0, edge_detail / 42.0)
    saturation_score = min(1.0, saturation / 90.0)
    return round(
        0.18 * brightness_score
        + 0.22 * contrast_score
        + 0.25 * entropy_score
        + 0.25 * detail_score
        + 0.10 * saturation_score,
        4,
    )


def subtitle_likelihood(content: bytes) -> float:
    """Estimate bright, outlined subtitle text in the lower-center video area."""

    with Image.open(io.BytesIO(content)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((480, 480), Image.Resampling.BILINEAR)
        left = int(image.width * 0.08)
        right = max(left + 1, int(image.width * 0.92))
        top = int(image.height * 0.55)
        bottom = max(top + 1, int(image.height * 0.94))
        gray = ImageOps.grayscale(image.crop((left, top, right, bottom)))

    bright = gray.point(lambda value: 255 if value >= 198 else 0)
    dark = gray.point(lambda value: 255 if value <= 82 else 0)
    nearby_dark = dark.filter(ImageFilter.MaxFilter(7))
    outlined_bright = ImageChops.multiply(bright, nearby_dark)
    width, height = outlined_bright.size
    if width <= 0 or height <= 0:
        return 0.0

    pixels = list(outlined_bright.get_flattened_data())
    row_counts = [
        sum(1 for value in pixels[row * width : (row + 1) * width] if value)
        for row in range(height)
    ]
    column_counts = [
        sum(1 for row in range(height) if pixels[row * width + column])
        for column in range(width)
    ]
    window_height = max(6, min(height, int(round(height * 0.22))))
    peak_density = max(
        sum(row_counts[start : start + window_height]) / (width * window_height)
        for start in range(0, height - window_height + 1)
    )
    active_row_fraction = sum(count >= width * 0.015 for count in row_counts) / height
    horizontal_spread = sum(count > 0 for count in column_counts) / width

    density_score = min(1.0, max(0.0, (peak_density - 0.006) / 0.045))
    row_score = min(1.0, max(0.0, (active_row_fraction - 0.025) / 0.18))
    spread_score = min(1.0, max(0.0, (horizontal_spread - 0.10) / 0.58))
    return round(0.5 * density_score + 0.2 * row_score + 0.3 * spread_score, 4)


def screenshot_preference_score(candidate: ScreenshotCandidate) -> float:
    return candidate.score - SUBTITLE_SCORE_PENALTY * candidate.subtitle_score


def step_candidate_timestamps(
    step: RecipeStep,
    next_step_start: float | None = None,
    *,
    count: int = DEFAULT_CANDIDATES_PER_STEP,
) -> list[float]:
    start = max(0.0, float(step.start_time))
    explicit_end = float(step.end_time) if step.end_time is not None else None
    end = explicit_end if explicit_end is not None and explicit_end > start + 0.5 else next_step_start
    if end is None or end <= start + 0.5:
        end = start + 7.0
    lower = min(end, start + 0.6)
    upper = max(lower, end - 0.35)
    count = max(1, int(count))
    if count == 1 or upper <= lower:
        return [round((lower + upper) / 2, 2)]
    return [round(lower + (upper - lower) * index / (count - 1), 2) for index in range(count)]


def generate_screenshot_candidates(
    video_path: Path,
    timestamps: list[float],
    *,
    optimizer: Callable[[Path], bytes] = optimize_screenshot,
) -> list[ScreenshotCandidate]:
    candidates: list[ScreenshotCandidate] = []
    with tempfile.TemporaryDirectory(prefix="bili-recipe-frames-") as temp_dir:
        root = Path(temp_dir)
        for index, timestamp in enumerate(timestamps):
            path = root / f"candidate-{index:02d}.jpg"
            try:
                capture_screenshot_at(video_path, timestamp, path)
                content = optimizer(path)
                candidates.append(
                    ScreenshotCandidate(
                        timestamp=timestamp,
                        score=score_screenshot(content),
                        content=content,
                        subtitle_score=subtitle_likelihood(content),
                    )
                )
            except (subprocess.SubprocessError, OSError, RuntimeError, ValueError) as exc:
                console.print(f"[yellow]Warning:[/yellow] screenshot candidate failed at {timestamp:.1f}s: {exc}")
    return sorted(candidates, key=screenshot_preference_score, reverse=True)


def finished_dish_candidate_timestamps(
    steps: list[RecipeStep],
    video_duration: float | None = None,
) -> list[float]:
    """Choose late, food-relevant moments instead of early preparation frames."""

    timestamps: list[float] = []
    for reverse_index, step_index in enumerate(range(len(steps) - 1, -1, -1)):
        step = steps[step_index]
        text = f"{step.title} {step.action}"
        if reverse_index < 2 or any(keyword in text for keyword in FINISHED_DISH_KEYWORDS):
            next_start = steps[step_index + 1].start_time if step_index + 1 < len(steps) else None
            candidates = step_candidate_timestamps(step, next_start, count=4)
            timestamps.extend(candidates[-2:])
        if len(timestamps) >= 5:
            break
    final_step_end = 0.0
    if steps:
        final_step = steps[-1]
        final_step_end = float(final_step.end_time or (final_step.start_time + 7.0))
    if video_duration and video_duration > 10 and final_step_end >= video_duration * 0.55:
        timestamps.extend(video_duration * ratio for ratio in (0.72, 0.82, 0.9, 0.95))
    return sorted({round(max(0.0, value), 2) for value in timestamps})


def capture_finished_dish_cover(
    video_path: Path,
    steps: list[RecipeStep],
    images_dir: Path,
    *,
    video_duration: float | None = None,
) -> ScreenshotCandidate | None:
    """Capture a dedicated menu cover, biased toward the finished dish."""

    cover_path = images_dir / "cover.jpg"
    cover_path.unlink(missing_ok=True)
    timestamps = finished_dish_candidate_timestamps(steps, video_duration)
    candidates = generate_screenshot_candidates(
        video_path,
        timestamps,
        optimizer=optimize_cover_screenshot,
    )
    if not candidates:
        return None
    latest = max(timestamps, default=1.0)
    best = max(
        candidates,
        key=lambda candidate: screenshot_preference_score(candidate)
        + 0.08 * min(1.0, float(candidate.timestamp or 0.0) / max(1.0, latest)),
    )
    if best.score < MIN_AUTOMATIC_COVER_SCORE:
        return best
    images_dir.mkdir(parents=True, exist_ok=True)
    cover_path.write_bytes(best.content)
    return best


def select_key_step_indices(steps: list[RecipeStep], max_images: int = DEFAULT_MAX_STEP_IMAGES) -> list[int]:
    """Select a compact timeline while preferring visually useful nearby steps."""

    count = min(len(steps), MAX_SAVED_STEP_IMAGES, max(0, int(max_images)))
    if count <= 0:
        return []
    if count == 1:
        return [len(steps) // 2]
    targets = [round(index * (len(steps) - 1) / (count - 1)) for index in range(count)]
    selected: list[int] = []
    for target in targets:
        if target == len(steps) - 1:
            candidates = [target]
        else:
            candidates = [
                index
                for index in range(max(0, target - 1), min(len(steps), target + 2))
                if index not in selected
            ]
        if not candidates:
            candidates = [target]

        def candidate_rank(index: int) -> tuple[int, int]:
            text = f"{steps[index].title} {steps[index].action}"
            visual_score = sum(weight for keyword, weight in VISUAL_STEP_KEYWORDS.items() if keyword in text)
            return visual_score, -abs(index - target)

        selected.append(max(candidates, key=candidate_rank))
    return sorted(dict.fromkeys(selected))


def capture_step_screenshots(
    video_path: Path,
    steps: list[RecipeStep],
    images_dir: Path,
    max_images: int = DEFAULT_MAX_STEP_IMAGES,
) -> None:
    images_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("step_*.jpg", "key_*.jpg"):
        for old_image in images_dir.glob(pattern):
            old_image.unlink(missing_ok=True)
    for step in steps:
        step.screenshot_path = None
        step.screenshot_time = None
        step.screenshot_status = None
        step.screenshot_score = None
    for step_index in select_key_step_indices(steps, max_images=max_images):
        step = steps[step_index]
        next_start = steps[step_index + 1].start_time if step_index + 1 < len(steps) else None
        timestamps = step_candidate_timestamps(step, next_start)
        candidates = generate_screenshot_candidates(video_path, timestamps)
        best = candidates[0] if candidates else None
        if best is None or best.score < MIN_AUTOMATIC_SCREENSHOT_SCORE:
            step.screenshot_status = "needs_review"
            step.screenshot_score = best.score if best else None
            continue
        output_path = images_dir / f"step_{step_index + 1:02d}.jpg"
        output_path.write_bytes(best.content)
        step.screenshot_path = f"images/{output_path.name}"
        step.screenshot_time = best.timestamp
        step.screenshot_status = "auto"
        step.screenshot_score = best.score
