from __future__ import annotations

import subprocess

from bili_recipe_notes import environment


def test_codex_cli_ready_when_help_runs(monkeypatch) -> None:
    monkeypatch.setattr(environment.shutil, "which", lambda name: "codex-path" if name == "codex" else None)
    monkeypatch.setattr(
        environment.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    ok, detail = environment._codex_cli_ready()

    assert ok is True
    assert detail == "codex-path"


def test_opencode_cli_ready_uses_windows_launcher(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(environment.os, "name", "nt")
    monkeypatch.setattr(
        environment.shutil,
        "which",
        lambda name: "opencode-cmd-path" if name == "opencode.cmd" else None,
    )

    def _run(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(environment.subprocess, "run", _run)

    ok, detail = environment._opencode_cli_ready()

    assert ok is True
    assert detail == "opencode-cmd-path"
    assert calls[0] == ["opencode-cmd-path", "run", "--help"]


def test_codex_cli_not_ready_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)

    ok, detail = environment._codex_cli_ready()

    assert ok is False
    assert detail == "not found in PATH"
