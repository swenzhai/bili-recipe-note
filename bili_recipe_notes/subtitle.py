from __future__ import annotations

import json
import re
from pathlib import Path

from .recipe_extractor import TranscriptSegment


def _parse_timecode_to_seconds(value: str) -> float:
    value = value.strip().replace(",", ".")
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_srt(content: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        time_line_idx = 1 if re.match(r"^\d+$", lines[0]) else 0
        if "-->" not in lines[time_line_idx]:
            continue
        start_raw, end_raw = [p.strip() for p in lines[time_line_idx].split("-->")]
        text = " ".join(lines[time_line_idx + 1 :])
        segments.append(TranscriptSegment(start=_parse_timecode_to_seconds(start_raw), end=_parse_timecode_to_seconds(end_raw), text=text))
    return segments


def parse_vtt(content: str) -> list[TranscriptSegment]:
    body = content.replace("\r\n", "\n")
    body = re.sub(r"^WEBVTT\s*", "", body)
    return parse_srt(body)


def parse_json3(content: str) -> list[TranscriptSegment]:
    data = json.loads(content)
    segments: list[TranscriptSegment] = []
    for evt in data.get("events", []):
        if "segs" not in evt:
            continue
        start = evt.get("tStartMs", 0) / 1000.0
        dur = evt.get("dDurationMs", 0) / 1000.0
        text = "".join(seg.get("utf8", "") for seg in evt.get("segs", []))
        segments.append(TranscriptSegment(start=start, end=start + dur, text=text.strip()))
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
