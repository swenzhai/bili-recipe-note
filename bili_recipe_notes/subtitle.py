from __future__ import annotations

import json
import re
from pathlib import Path

from .recipe_extractor import TranscriptSegment


def _parse_timecode_to_seconds(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Invalid subtitle timecode: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_timing_line(line: str) -> tuple[float, float] | None:
    if "-->" not in line:
        return None
    start_raw, end_with_settings = [part.strip() for part in line.split("-->", maxsplit=1)]
    # WebVTT allows cue settings after the end timestamp, for example
    # ``align:start position:0%``.
    end_raw = end_with_settings.split(maxsplit=1)[0] if end_with_settings else ""
    if not start_raw or not end_raw:
        return None
    try:
        return _parse_timecode_to_seconds(start_raw), _parse_timecode_to_seconds(end_raw)
    except (TypeError, ValueError):
        return None


def _is_metadata_block(first_line: str) -> bool:
    upper = first_line.upper()
    return bool(
        upper.startswith("WEBVTT")
        or upper in {"NOTE", "STYLE", "REGION"}
        or upper.startswith("NOTE ")
    )


def parse_srt(content: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", normalized.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines or _is_metadata_block(lines[0]):
            continue
        timing: tuple[float, float] | None = None
        time_line_idx = -1
        for idx, line in enumerate(lines):
            timing = _parse_timing_line(line)
            if timing is not None:
                time_line_idx = idx
                break
        if timing is None:
            continue
        text = " ".join(lines[time_line_idx + 1 :])
        if not text:
            continue
        start, end = timing
        segments.append(TranscriptSegment(start=start, end=end, text=text))
    return segments


def parse_vtt(content: str) -> list[TranscriptSegment]:
    return parse_srt(content)


def parse_json3(content: str) -> list[TranscriptSegment]:
    data = json.loads(content)
    segments: list[TranscriptSegment] = []
    for evt in data.get("events", []):
        if "segs" not in evt:
            continue
        start = evt.get("tStartMs", 0) / 1000.0
        dur = evt.get("dDurationMs", 0) / 1000.0
        text = "".join(seg.get("utf8", "") for seg in evt.get("segs", []))
        text = text.strip()
        if text:
            segments.append(TranscriptSegment(start=start, end=start + dur, text=text))
    return segments


def parse_subtitle_file(path: Path) -> list[TranscriptSegment]:
    content = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return parse_srt(content)
    if suffix == ".vtt":
        return parse_vtt(content)
    if suffix == ".json3":
        return parse_json3(content)
    raise ValueError(f"Unsupported subtitle format: {path}")
