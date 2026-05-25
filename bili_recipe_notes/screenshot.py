from __future__ import annotations

import subprocess
from pathlib import Path

from rich.console import Console

from .recipe_extractor import RecipeStep

console = Console()


def capture_step_screenshots(video_path: Path, steps: list[RecipeStep], images_dir: Path) -> None:
    images_dir.mkdir(parents=True, exist_ok=True)
    for idx, step in enumerate(steps, start=1):
        timestamp = max(0.0, step.start_time + 1.5)
        output_path = images_dir / f"step_{idx:02d}.jpg"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{timestamp}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            step.screenshot_path = f"images/{output_path.name}"
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            console.print(f"[yellow]Warning:[/yellow] screenshot failed for step {idx}: {exc}")
