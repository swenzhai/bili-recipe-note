from __future__ import annotations

import io
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat
from rich.console import Console

from .recipe_extractor import RecipeStep

console = Console()
FFMPEG_SCREENSHOT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_STEP_IMAGES = 3
MAX_SAVED_STEP_IMAGES = 4
DEFAULT_CANDIDATES_PER_STEP = 5
MIN_AUTOMATIC_SCREENSHOT_SCORE = 0.38
MAX_SCREENSHOT_DIMENSION = 1280
MAX_SCREENSHOT_BYTES = 450 * 1024
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


@dataclass(frozen=True)
class ScreenshotCandidate:
    timestamp: float | None
    score: float
    content: bytes


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


def _optimize_image(source: Image.Image) -> bytes:
    image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((MAX_SCREENSHOT_DIMENSION, MAX_SCREENSHOT_DIMENSION), Image.Resampling.LANCZOS)
    content = b""
    for quality in (84, 78, 72, 66, 60):
        content = _encoded_jpeg(image, quality)
        if len(content) <= MAX_SCREENSHOT_BYTES:
            return content
    while len(content) > MAX_SCREENSHOT_BYTES and min(image.size) > 180:
        image.thumbnail(
            (max(180, int(image.width * 0.82)), max(180, int(image.height * 0.82))),
            Image.Resampling.LANCZOS,
        )
        content = _encoded_jpeg(image, 60)
    if len(content) > MAX_SCREENSHOT_BYTES:
        content = _encoded_jpeg(image, 42)
    if len(content) > MAX_SCREENSHOT_BYTES:
        raise ValueError("screenshot could not be compressed within the storage limit")
    return content


def optimize_screenshot(path: Path) -> bytes:
    """Normalize a selected frame so persistent recipe images stay compact."""

    with Image.open(path) as source:
        return _optimize_image(source)


def optimize_screenshot_content(content: bytes) -> bytes:
    with Image.open(io.BytesIO(content)) as source:
        return _optimize_image(source)


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
) -> list[ScreenshotCandidate]:
    candidates: list[ScreenshotCandidate] = []
    with tempfile.TemporaryDirectory(prefix="bili-recipe-frames-") as temp_dir:
        root = Path(temp_dir)
        for index, timestamp in enumerate(timestamps):
            path = root / f"candidate-{index:02d}.jpg"
            try:
                capture_screenshot_at(video_path, timestamp, path)
                content = optimize_screenshot(path)
                candidates.append(
                    ScreenshotCandidate(
                        timestamp=timestamp,
                        score=score_screenshot(content),
                        content=content,
                    )
                )
            except (subprocess.SubprocessError, OSError, RuntimeError, ValueError) as exc:
                console.print(f"[yellow]Warning:[/yellow] screenshot candidate failed at {timestamp:.1f}s: {exc}")
    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


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
