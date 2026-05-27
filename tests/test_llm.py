from __future__ import annotations

import subprocess

from bili_recipe_notes.llm import (
    append_missing_image_links,
    extract_markdown_image_links,
    markdown_has_image_links,
    normalize_markdown_image_paths,
    summarize_note_with_opencode,
)


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


def test_summarize_note_with_opencode_windows_winerror_206_fallback_to_stdin(monkeypatch) -> None:
    calls = []

    def _run(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            err = OSError(22, "bad", "opencode")
            err.winerror = 206
            raise err
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="stdin总结\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr("bili_recipe_notes.llm.os.name", "nt")

    assert summarize_note_with_opencode("# note") == "stdin总结"
    assert calls[1][0][0] == ["opencode", "run"]
    assert "input" in calls[1][1]


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


def test_markdown_has_image_links() -> None:
    assert markdown_has_image_links("![](images/step_01.jpg)")
    assert not markdown_has_image_links("no image")


def test_append_missing_image_links() -> None:
    source_links = extract_markdown_image_links("![](images/step_01.jpg)\n![](images/step_02.jpg)")
    merged = append_missing_image_links("## 烹饪\n\n步骤文字", source_links)
    assert "## 步骤配图补全" in merged
    assert "![](images/step_01.jpg)" in merged
    assert "![](images/step_02.jpg)" in merged
