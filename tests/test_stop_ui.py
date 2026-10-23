from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

import pytest

from bili_recipe_notes import stop_ui


pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc stop script")


_FAKE_SERVER = """
import os
from pathlib import Path
import signal
import time

if os.environ.get("FAKE_IGNORE_TERM") == "1":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(os.environ["FAKE_READY_FILE"]).write_text("ready", encoding="utf-8")
while True:
    time.sleep(0.05)
"""


@pytest.fixture
def fake_servers(tmp_path, monkeypatch):
    """Only synthetic processes inside a unique temporary project are targeted."""
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(stop_ui, "PROJECT_ROOT", root)
    processes: list[subprocess.Popen] = []

    def spawn(args, *, cwd=None, ignore_term=False):
        cwd = cwd or root
        cwd.mkdir(parents=True, exist_ok=True)
        package = cwd / "bili_recipe_notes"
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        for name in ("ui_launcher", "ui", "frame_selection_worker"):
            (package / f"{name}.py").write_text(_FAKE_SERVER, encoding="utf-8")
        (cwd / "streamlit.py").write_text(_FAKE_SERVER, encoding="utf-8")
        (cwd / "uvicorn.py").write_text(_FAKE_SERVER, encoding="utf-8")
        ready = tmp_path / f"ready-{uuid.uuid4().hex}"
        environment = os.environ.copy()
        environment["FAKE_READY_FILE"] = str(ready)
        environment["FAKE_IGNORE_TERM"] = "1" if ignore_term else "0"
        process = subprocess.Popen(
            [sys.executable, *args], cwd=cwd, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, start_new_session=True,
        )
        processes.append(process)
        deadline = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None:
                raise AssertionError(process.stderr.read().decode("utf-8", errors="replace"))
            if time.monotonic() >= deadline:
                raise AssertionError("Synthetic server did not start")
            time.sleep(0.01)
        return process

    try:
        yield root, spawn
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stderr:
                process.stderr.close()


def test_stop_closes_both_ui_servers_and_is_repeatable(fake_servers):
    _, spawn = fake_servers
    react = spawn(["-m", "bili_recipe_notes.ui_launcher", "--port", "8765", "--no-browser"])
    legacy = spawn(["-m", "streamlit", "run", "bili_recipe_notes/ui.py", "--server.port=8501"])

    assert stop_ui.main(["--timeout", "2"]) == 0
    assert react.wait(timeout=2) == -signal.SIGTERM
    assert legacy.wait(timeout=2) == -signal.SIGTERM
    assert stop_ui.main(["--timeout", "0"]) == 0


def test_dry_run_lists_servers_without_stopping_them(fake_servers, capsys):
    _, spawn = fake_servers
    process = spawn(["-m", "bili_recipe_notes.ui_launcher"])

    assert stop_ui.main(["--dry-run"]) == 0
    assert str(process.pid) in capsys.readouterr().out
    assert process.poll() is None


def test_stop_leaves_other_projects_and_non_servers_running(fake_servers, tmp_path):
    _, spawn = fake_servers
    target = spawn(["-m", "bili_recipe_notes.ui_launcher"])
    unrelated = [
        spawn(["-m", "bili_recipe_notes.ui_launcher"], cwd=tmp_path / "another-project"),
        spawn(["-c", _FAKE_SERVER, "-m", "bili_recipe_notes.ui_launcher"]),
        spawn(["-m", "bili_recipe_notes.ui_launcher", "--frame-selection-worker", "out", "batch", "token"]),
        spawn(["-m", "bili_recipe_notes.frame_selection_worker", "out", "batch", "token"]),
        spawn(["-m", "streamlit", "run", "some-other-app.py"]),
    ]

    assert stop_ui.main(["--timeout", "2"]) == 0
    assert target.wait(timeout=2) == -signal.SIGTERM
    assert all(process.poll() is None for process in unrelated)


def test_timeout_keeps_unresponsive_server_alive_without_force(fake_servers):
    _, spawn = fake_servers
    process = spawn(["-m", "bili_recipe_notes.ui_launcher"], ignore_term=True)

    assert stop_ui.main(["--timeout", "0.1"]) != 0
    assert process.poll() is None


def test_force_stops_unresponsive_server_after_timeout(fake_servers):
    _, spawn = fake_servers
    process = spawn(["-m", "bili_recipe_notes.ui_launcher"], ignore_term=True)

    assert stop_ui.main(["--timeout", "0.1", "--force"]) == 0
    assert process.wait(timeout=2) == -signal.SIGKILL


def test_direct_uvicorn_server_can_be_stopped(fake_servers):
    _, spawn = fake_servers
    process = spawn(["-m", "uvicorn", "bili_recipe_notes.mobile_api:app", "--port", "8765"])

    assert stop_ui.main(["--timeout", "2"]) == 0
    assert process.wait(timeout=2) == -signal.SIGTERM


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "-inf"])
def test_invalid_timeout_is_rejected_before_process_discovery(fake_servers, value):
    _, spawn = fake_servers
    process = spawn(["-m", "bili_recipe_notes.ui_launcher"])

    with pytest.raises(SystemExit) as error:
        stop_ui.main([f"--timeout={value}"])
    assert error.value.code == 2
    assert process.poll() is None


@pytest.mark.parametrize("flag", [
    "--admin-creator-worker", "--frame-selection-supervisor", "--frame-selection-worker",
    "--dish-info-worker", "--ai-review-worker", "--batch-worker-request",
])
def test_launcher_worker_modes_are_not_servers(tmp_path, flag):
    assert stop_ui.server_label(
        [sys.executable, "-m", "bili_recipe_notes.ui_launcher", flag, "request"], tmp_path, tmp_path,
    ) is None


def test_streamlit_console_entry_requires_the_projects_ui(tmp_path):
    ui = tmp_path / "bili_recipe_notes" / "ui.py"
    ui.parent.mkdir()
    ui.touch()

    assert stop_ui.server_label(["/venv/bin/streamlit", "run", str(ui)], tmp_path, tmp_path)
    assert stop_ui.server_label(
        ["/venv/bin/streamlit", "run", str(tmp_path / "another.py")], tmp_path, tmp_path,
    ) is None
    assert stop_ui.server_label(
        [sys.executable, "-m", "bili_recipe_notes.ui_launcher"], tmp_path / "other", tmp_path,
    ) is None


@pytest.mark.parametrize("entrypoint,arguments", [
    ("streamlit", ["run", "bili_recipe_notes/ui.py"]),
    ("uvicorn", ["bili_recipe_notes.mobile_api:app"]),
])
def test_python_console_entry_is_recognized(tmp_path, entrypoint, arguments):
    assert stop_ui.server_label(
        [sys.executable, f"/venv/bin/{entrypoint}", *arguments], tmp_path, tmp_path,
    )


def test_shell_can_stop_its_project_from_another_directory(fake_servers, tmp_path):
    _, spawn = fake_servers
    isolated_root = tmp_path / "project with spaces"
    process = spawn(["-m", "bili_recipe_notes.ui_launcher"], cwd=isolated_root)
    source_root = Path(__file__).resolve().parents[1]
    shell = isolated_root / "stop-ui-linux.sh"
    shutil.copy2(source_root / "stop-ui-linux.sh", shell)
    shutil.copy2(source_root / "bili_recipe_notes/stop_ui.py", isolated_root / "bili_recipe_notes/stop_ui.py")
    python_link = isolated_root / ".venv/bin/python"
    python_link.parent.mkdir(parents=True)
    python_link.symlink_to(sys.executable)
    environment = os.environ.copy()
    environment.pop("PYTHON", None)

    dry_run = subprocess.run(
        [str(shell), "--dry-run"], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=10,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert str(process.pid) in dry_run.stdout
    assert process.poll() is None
    stopped = subprocess.run(
        [str(shell), "--timeout", "2"], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=10,
    )
    assert stopped.returncode == 0, stopped.stderr
    assert process.wait(timeout=2) == -signal.SIGTERM
