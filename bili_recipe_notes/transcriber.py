from __future__ import annotations

from pathlib import Path

from .recipe_extractor import TranscriptSegment


def _has_cuda_gpu() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _build_whisper_model(model_size: str):
    from faster_whisper import WhisperModel

    if _has_cuda_gpu():
        return WhisperModel(model_size, device="cuda", compute_type="float16")
    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe_audio(audio_path: Path, model_size: str = "small", language: str = "zh") -> list[TranscriptSegment]:
    model = _build_whisper_model(model_size)
    segments, _ = model.transcribe(str(audio_path), language=language)
    return [TranscriptSegment(start=s.start, end=s.end, text=s.text.strip()) for s in segments]
