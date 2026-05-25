from __future__ import annotations

from pathlib import Path

from .recipe_extractor import TranscriptSegment


def transcribe_audio(audio_path: Path, model_size: str = "small", language: str = "zh") -> list[TranscriptSegment]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), language=language)
    return [TranscriptSegment(start=s.start, end=s.end, text=s.text.strip()) for s in segments]
