from __future__ import annotations

import subprocess

from bili_recipe_notes.llm import summarize_note_with_opencode


def test_summarize_note_with_opencode_success(monkeypatch) -> None:
    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="总结内容\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    assert summarize_note_with_opencode("# note") == "总结内容"


def test_summarize_note_with_opencode_failure(monkeypatch) -> None:
    def _run(*args, **kwargs):
        raise FileNotFoundError("opencode not found")

    monkeypatch.setattr(subprocess, "run", _run)
    assert summarize_note_with_opencode("# note") is None
