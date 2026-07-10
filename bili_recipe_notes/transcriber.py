from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .recipe_extractor import TranscriptSegment


def _has_cuda_gpu() -> bool:
    # faster-whisper runs on CTranslate2, so ask that runtime first instead of
    # requiring PyTorch solely for device detection.
    try:
        import ctranslate2

        return bool(ctranslate2.get_cuda_device_count())
    except Exception:
        try:
            import torch

            return bool(torch.cuda.is_available())
        except Exception:
            return False


@lru_cache(maxsize=2)
def _load_whisper_model(model_size: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device=device, compute_type=compute_type)


def _build_whisper_model(model_size: str):
    if _has_cuda_gpu():
        try:
            return _load_whisper_model(model_size, device="cuda", compute_type="float16")
        except Exception:
            # A visible CUDA device can still be unusable because its runtime
            # libraries or compute capability do not match CTranslate2.
            pass
    return _load_whisper_model(model_size, device="cpu", compute_type="int8")


def transcribe_audio(audio_path: Path, model_size: str = "small", language: str = "zh") -> list[TranscriptSegment]:
    if not audio_path.is_file() or audio_path.stat().st_size <= 0:
        raise FileNotFoundError(f"Audio file is missing or empty: {audio_path}")
    model = _build_whisper_model(model_size)
    segments, _ = model.transcribe(str(audio_path), language=language)
    transcript: list[TranscriptSegment] = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            transcript.append(TranscriptSegment(start=segment.start, end=segment.end, text=text))
    return transcript
