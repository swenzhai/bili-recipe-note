from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bili_recipe_notes import frame_selection


@pytest.fixture
def tool(tmp_path, monkeypatch):
    root = tmp_path / "external tools" / "video-frame-choose"
    root.mkdir(parents=True)
    (root / "select_frame.py").write_text("# synthetic entry point\n", encoding="utf-8")
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"synthetic interpreter")
    python.chmod(0o755)
    monkeypatch.setattr(frame_selection, "TOOL_ROOT", root)
    monkeypatch.delenv(frame_selection.FRAME_PYTHON_ENV, raising=False)
    return root, python


def test_defaults_use_submodule_environment_and_keep_sampling_arguments(tool, monkeypatch):
    root, python = tool
    calls = []
    monkeypatch.setattr(frame_selection.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0))
    assert frame_selection.main(["--input", "video with space.mp4", "--output", "new results", "--coarse-limit", "6", "--fine-limit", "3", "--threads", "8"]) == 0
    command, kwargs = calls[0]
    assert command[:2] == [str(python), str(root / "select_frame.py")]
    assert command[2:10] == ["--input", "video with space.mp4", "--output", "new results", "--requirement", frame_selection.DEFAULT_REQUIREMENT, "--model", str(root / "models" / "Qwen2.5-VL-3B-Instruct")]
    assert command[10:] == ["--coarse-limit", "6", "--fine-limit", "3", "--threads", "8"]
    assert "--allow-fallback" not in command
    assert kwargs == {"check": False}


def test_explicit_overrides_beat_environment_and_allow_only_requested_fallback(tool, monkeypatch):
    monkeypatch.setenv(frame_selection.FRAME_PYTHON_ENV, "/missing/env/python")
    calls = []
    monkeypatch.setattr(frame_selection.subprocess, "run", lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0))
    assert frame_selection.main(["--input", "video.mp4", "--output", "dish.png", "--python", sys.executable, "--prompt", "清楚的成品菜", "--model", "models/my model", "--allow-fallback", "--device", "cpu"]) == 0
    command = calls[0]
    assert Path(command[0]) == Path(sys.executable)
    assert command[command.index("--requirement") + 1] == "清楚的成品菜"
    assert command[command.index("--model") + 1] == "models/my model"
    assert command[-3:] == ["--allow-fallback", "--device", "cpu"]


@pytest.mark.parametrize("exit_code", [0, 1, 2, 130])
def test_child_exit_code_and_separate_streams_are_preserved(tool, monkeypatch, capfd, tmp_path, exit_code):
    root, _ = tool
    (root / "select_frame.py").write_text(
        "import json, sys\n"
        "print(json.dumps({'args': sys.argv[1:]}))\n"
        "print('model progress', file=sys.stderr)\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(frame_selection.FRAME_PYTHON_ENV, sys.executable)
    monkeypatch.chdir(tmp_path)
    output = "independent output/dish.png"
    assert frame_selection.main(["--input", "input with space.mp4", "--output", output]) == exit_code
    captured = capfd.readouterr()
    arguments = json.loads(captured.out)["args"]
    assert arguments[1] == "input with space.mp4" and arguments[3] == output
    assert captured.err == "model progress\n"
    assert not (tmp_path / "independent output").exists()


def test_missing_submodule_has_actionable_error_without_starting_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(frame_selection, "TOOL_ROOT", tmp_path / "missing")
    monkeypatch.setattr(frame_selection.subprocess, "run", lambda *a, **k: pytest.fail("must not launch"))
    assert frame_selection.main(["--input", "in.mp4", "--output", "result"]) == 1
    assert "git submodule update --init" in capsys.readouterr().err


def test_missing_independent_python_does_not_fall_back_to_app_environment(tool, monkeypatch, capsys):
    _, python = tool
    python.unlink()
    monkeypatch.setattr(frame_selection.subprocess, "run", lambda *a, **k: pytest.fail("must not use app Python"))
    assert frame_selection.main(["--input", "in.mp4", "--output", "result"]) == 1
    assert frame_selection.FRAME_PYTHON_ENV in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [[], ["--input", "in.mp4"], ["--output", "result"]])
def test_input_and_explicit_output_are_required(arguments):
    with pytest.raises(SystemExit) as error:
        frame_selection.main(arguments)
    assert error.value.code == 2
