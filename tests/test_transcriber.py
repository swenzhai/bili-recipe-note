from __future__ import annotations

import types

from bili_recipe_notes import transcriber


class _FakeModel:
    def __init__(self, model_size: str, device: str, compute_type: str) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


def test_build_whisper_model_prefers_cuda(monkeypatch) -> None:
    monkeypatch.setattr(transcriber, "_has_cuda_gpu", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_FakeModel))

    model = transcriber._build_whisper_model("small")

    assert model.device == "cuda"
    assert model.compute_type == "float16"


def test_build_whisper_model_falls_back_to_cpu(monkeypatch) -> None:
    monkeypatch.setattr(transcriber, "_has_cuda_gpu", lambda: False)
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_FakeModel))

    model = transcriber._build_whisper_model("small")

    assert model.device == "cpu"
    assert model.compute_type == "int8"


def test_transcribe_audio(monkeypatch, tmp_path) -> None:
    class _TranscribeModel:
        def transcribe(self, *_args, **_kwargs):
            return [_FakeSegment(0.0, 1.2, " 文本 ")], None

    monkeypatch.setattr(transcriber, "_build_whisper_model", lambda _size: _TranscribeModel())
    audio = tmp_path / "a.wav"
    audio.write_text("x", encoding="utf-8")

    result = transcriber.transcribe_audio(audio)

    assert len(result) == 1
    assert result[0].text == "文本"
