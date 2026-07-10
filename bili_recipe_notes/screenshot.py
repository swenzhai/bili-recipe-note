from __future__ import annotations

import subprocess
from pathlib import Path

from rich.console import Console

from .recipe_extractor import RecipeStep

console = Console()
FFMPEG_SCREENSHOT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_STEP_IMAGES = 4


def capture_screenshot_at(video_path: Path, timestamp: float, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Never let a stale image make a failed ffmpeg invocation look successful.
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


def select_key_step_indices(steps: list[RecipeStep], max_images: int = DEFAULT_MAX_STEP_IMAGES) -> list[int]:
    """Select a small, evenly distributed visual timeline of the recipe."""

    count = min(len(steps), max(0, int(max_images)))
    if count <= 0:
        return []
    if count == 1:
        return [len(steps) // 2]
    return list(dict.fromkeys(round(index * (len(steps) - 1) / (count - 1)) for index in range(count)))


def capture_step_screenshots(
    video_path: Path,
    steps: list[RecipeStep],
    images_dir: Path,
    max_images: int = DEFAULT_MAX_STEP_IMAGES,
) -> None:
    images_dir.mkdir(parents=True, exist_ok=True)
    for old_image in images_dir.glob("step_*.jpg"):
        old_image.unlink(missing_ok=True)
    for step in steps:
        step.screenshot_path = None
    for step_index in select_key_step_indices(steps, max_images=max_images):
        step = steps[step_index]
        idx = step_index + 1
        timestamp = max(0.0, step.start_time + 1.5)
        output_path = images_dir / f"step_{idx:02d}.jpg"
        try:
            capture_screenshot_at(video_path, timestamp, output_path)
            step.screenshot_path = f"images/{output_path.name}"
        except (subprocess.SubprocessError, OSError, RuntimeError) as exc:
            console.print(f"[yellow]Warning:[/yellow] screenshot failed for step {idx}: {exc}")
