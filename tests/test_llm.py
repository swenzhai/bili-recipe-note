from __future__ import annotations

import subprocess

from bili_recipe_notes.llm import normalize_markdown_image_paths, summarize_note_with_opencode


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


def test_normalize_markdown_image_paths() -> None:
    md = "\n".join(
        [
            "![](./images/step_01.jpg)",
            "![](/tmp/step_02.png)",
            "![x](https://example.com/a.jpg)",
        ]
    )
    normalized = normalize_markdown_image_paths(md)
    assert "![](images/step_01.jpg)" in normalized
    assert "![](images/step_02.png)" in normalized
    assert "![x](https://example.com/a.jpg)" in normalized
