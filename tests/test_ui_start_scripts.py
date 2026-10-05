"""Exercise startup selection with fake interpreters: no installs or servers."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPTS = ["start-ui-linux.sh", "start-ui-mac.command"]
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").is_file(), reason="POSIX startup scripts require bash")


def interpreter(path, *, supported=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("""#!/bin/sh
printf '%s|%s\\n' "$0" "$*" >> "$TEST_INTERPRETER_LOG"
case "$*" in
  *sys.version_info*) exit VERSION_EXIT ;;
  *--version*) echo 'Python fake-version' ;;
  *data_root*) printf '%s\\n' "$TEST_DATA_ROOT" ;;
  *'-m bili_recipe_notes.ui_launcher'*) exit "${TEST_SERVER_EXIT:-0}" ;;
  *'-m pip'*) echo 'An install must never run in this test' >&2; exit 91 ;;
  *'-m venv'*) echo 'A real environment must never be created in this test' >&2; exit 92 ;;
esac
""".replace("VERSION_EXIT", "0" if supported else "1"))
    path.chmod(0o755)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    for name in SCRIPTS:
        shutil.copyfile(ROOT / name, root / name)
    (root / "web/dist").mkdir(parents=True)
    (root / "web/dist/index.html").write_text("synthetic")
    requirements = b"# no real dependencies in this test\n"
    (root / "requirements.txt").write_bytes(requirements)
    interpreter(root / ".venv/bin/python")
    (root / ".venv/.bili-recipe-notes-requirements").write_text(hashlib.sha256(requirements).hexdigest() + "\n")
    interpreter(root / "fake-bin/python3", supported=False)
    (root / "fake-bin/ipconfig").write_text("#!/bin/sh\necho 127.0.0.1\n")
    (root / "fake-bin/ipconfig").chmod(0o755)
    env = {**os.environ, "PATH": str(root / "fake-bin") + ":/usr/bin:/bin",
           "TEST_INTERPRETER_LOG": str(root / "calls.log"), "TEST_DATA_ROOT": str(root / "synthetic-data")}
    env.pop("PYTHON", None)
    return root, env


def run_script(workspace, name):
    root, env = workspace
    return subprocess.run(["/bin/bash", str(root / name)], input="\n", text=True,
                          capture_output=True, env=env, timeout=10)


@pytest.mark.parametrize("name", SCRIPTS)
def test_valid_venv_is_used_before_unsupported_system_python(workspace, name):
    root, env = workspace
    env["TEST_SERVER_EXIT"] = "7"
    result = run_script(workspace, name)
    assert result.returncode == 7, result.stdout + result.stderr
    calls = (root / "calls.log").read_text()
    assert "fake-bin/python3" not in calls
    assert "-m pip" not in calls and "-m venv" not in calls
    assert ".venv/bin/python|-m bili_recipe_notes.ui_launcher --host 0.0.0.0 --port 8765" in calls


@pytest.mark.parametrize("name", SCRIPTS)
def test_explicit_python_keeps_priority_and_supports_spaces(workspace, name):
    root, env = workspace
    explicit = root / "chosen python/python"
    interpreter(explicit)
    env["PYTHON"] = str(explicit)
    result = run_script(workspace, name)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (root / "calls.log").read_text().splitlines()
    assert calls[0].startswith(str(explicit) + "|-c import sys")
    assert any(".venv/bin/python|-m bili_recipe_notes.ui_launcher" in line for line in calls)


@pytest.mark.parametrize("name", SCRIPTS)
def test_invalid_explicit_python_is_not_silently_ignored(workspace, name):
    root, env = workspace
    explicit = root / "old python/python"
    interpreter(explicit, supported=False)
    env["PYTHON"] = str(explicit)
    result = run_script(workspace, name)
    assert result.returncode == 1
    calls = (root / "calls.log").read_text()
    assert str(explicit) + "|--version" in calls
    assert "ui_launcher" not in calls and "-m pip" not in calls


@pytest.mark.parametrize("name", SCRIPTS)
def test_invalid_existing_venv_stops_before_installation(workspace, name):
    root, env = workspace
    interpreter(root / ".venv/bin/python", supported=False)
    explicit = root / "chosen python/python"
    interpreter(explicit)
    env["PYTHON"] = str(explicit)
    result = run_script(workspace, name)
    assert result.returncode == 1 and ".venv" in result.stdout
    calls = (root / "calls.log").read_text()
    assert "-m pip" not in calls and "ui_launcher" not in calls
