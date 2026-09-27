from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import replace

import pytest

from bili_recipe_notes import batch_runner
from bili_recipe_notes.batch_queue import create_batch_state, list_batch_states
from bili_recipe_notes.pipeline import BatchJobItemResult, BatchJobOptions, BatchJobResult


def test_background_batch_returns_immediately_and_records_completion(monkeypatch, tmp_path) -> None:
    def fake_run(options, log=None):
        if log:
            log("processing one item")
        return BatchJobResult([BatchJobItemResult(url="https://x/a", status="done")])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(batch_runner, "run_batch", fake_run)
    options = BatchJobOptions(urls=[], batch_id="background-1", resume_mode="resume-unfinished")

    started = time.monotonic()
    initial = batch_runner.start_background_batch(options)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert initial.status == "running"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = batch_runner.get_background_batch_status("background-1")
        if status and status.status == "done":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("background batch did not finish")

    assert "processing one item" in batch_runner.read_batch_log("background-1")


def test_background_batch_does_not_start_duplicate_worker(monkeypatch, tmp_path) -> None:
    calls = 0

    def fake_run(options, log=None):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return BatchJobResult([])

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(batch_runner, "run_batch", fake_run)
    options = BatchJobOptions(urls=[], batch_id="background-2", resume_mode="resume-unfinished")

    first = batch_runner.start_background_batch(options)
    second = batch_runner.start_background_batch(options)

    assert first.started_at == second.started_at
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = batch_runner.get_background_batch_status("background-2")
        if status and status.status == "done":
            break
        time.sleep(0.01)
    assert calls == 1


def test_production_background_batch_runs_in_separate_process(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    create_batch_state([], {}, batch_id="process-batch", project_root=tmp_path)
    options = BatchJobOptions(
        urls=[],
        batch_id="process-batch",
        resume_mode="resume-unfinished",
        target_stage="raw",
    )

    initial = batch_runner.start_background_batch(options, project_root=tmp_path)

    assert initial.pid and initial.pid != __import__("os").getpid()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = batch_runner.get_background_batch_status("process-batch", project_root=tmp_path)
        if status and status.status == "done":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("background subprocess did not finish")
    assert [state.batch_id for state in list_batch_states(project_root=tmp_path)] == ["process-batch"]


def _wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("controlled worker did not reach expected state")


@pytest.fixture
def controlled_worker(tmp_path):
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        pytest.skip("process group pause requires Linux or macOS")
    # Both counters are written by real processes in a new, dedicated session.
    # These workers never touch the user's queue, network, models, or files.
    tick = """
import sys, time
from pathlib import Path
while True:
    with Path(sys.argv[1]).open('ab') as stream:
        stream.write(b'.')
    time.sleep(0.01)
"""
    code = """
import os, signal, subprocess, sys, time
from pathlib import Path
from bili_recipe_notes import batch_runner
root = Path(sys.argv[1])
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
child = subprocess.Popen([sys.executable, '-u', '-c', sys.argv[2], str(root / 'child-count')])
try:
    while not (root / 'finish').exists():
        with (root / 'parent-count').open('ab') as stream:
            stream.write(b'.')
        time.sleep(0.01)
    current = batch_runner._read_status('controlled', root)
    batch_runner._write_status(batch_runner.BackgroundBatchStatus(
        'controlled', 'done', current.started_at,
        finished_at=batch_runner._now(), pid=os.getpid()), root)
finally:
    child.terminate()
    child.wait(timeout=3)
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(tmp_path), tick],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    status = batch_runner.BackgroundBatchStatus(
        "controlled", "running", batch_runner._now(), pid=process.pid,
        process_start_id=batch_runner._process_start_id(process.pid),
    )
    batch_runner._write_status(status, tmp_path)
    batch_runner._RUNS[status.batch_id] = batch_runner._RunRecord(process, status)
    try:
        _wait_for(lambda: all((tmp_path / name).exists() for name in ("parent-count", "child-count")))
        yield process, status
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGCONT)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if process.stderr:
            process.stderr.close()
        batch_runner._RUNS.pop(status.batch_id, None)


def _counts(root):
    return tuple((root / name).stat().st_size for name in ("parent-count", "child-count"))


def test_pause_and_continue_same_process_and_children_after_ui_restart(
    controlled_worker, monkeypatch, tmp_path
):
    process, original = controlled_worker
    paused = batch_runner.pause_background_batch(original.batch_id, tmp_path)
    assert paused.status == "paused"
    assert paused.pid == process.pid
    assert paused.paused_at
    assert batch_runner._RUNS[original.batch_id].status == paused
    assert batch_runner.pause_background_batch(original.batch_id, tmp_path) == paused
    time.sleep(0.05)  # Give the child time to acknowledge SIGSTOP too.
    counts = _counts(tmp_path)
    time.sleep(0.15)
    assert _counts(tmp_path) == counts

    batch_runner._RUNS.pop(original.batch_id)  # A new UI has no in-memory record.
    assert batch_runner.get_background_batch_status(original.batch_id, tmp_path) == paused

    def unexpected_run(*args, **kwargs):
        raise AssertionError("a paused worker must not be restarted")

    monkeypatch.setattr(batch_runner, "run_batch", unexpected_run)
    assert batch_runner.start_background_batch(
        BatchJobOptions(urls=[], batch_id=original.batch_id), project_root=tmp_path
    ) == paused
    resumed = batch_runner.resume_background_batch(original.batch_id, tmp_path)
    assert resumed.status == "running"
    assert resumed.pid == process.pid
    assert resumed.paused_at is None
    assert resumed.paused_seconds >= 0.15
    assert batch_runner.resume_background_batch(original.batch_id, tmp_path) == resumed
    _wait_for(lambda: all(a > b for a, b in zip(_counts(tmp_path), counts)))

    # A very fast completion after SIGCONT must not be overwritten by running.
    batch_runner.pause_background_batch(original.batch_id, tmp_path)
    (tmp_path / "finish").touch()
    resumed = batch_runner.resume_background_batch(original.batch_id, tmp_path)
    process.wait(timeout=5)
    final = batch_runner.get_background_batch_status(original.batch_id, tmp_path)
    assert final.status == "done"
    assert final.paused_seconds == resumed.paused_seconds


def test_failed_pause_status_write_keeps_worker_running(controlled_worker, monkeypatch, tmp_path):
    _, original = controlled_worker
    counts = _counts(tmp_path)

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(batch_runner, "_write_status", fail_write)
        with pytest.raises(OSError, match="disk full"):
            batch_runner.pause_background_batch(original.batch_id, tmp_path)
    assert batch_runner.get_background_batch_status(original.batch_id, tmp_path).status == "running"
    _wait_for(lambda: all(a > b for a, b in zip(_counts(tmp_path), counts)))


def test_failed_resume_status_write_keeps_worker_paused(controlled_worker, monkeypatch, tmp_path):
    _, original = controlled_worker
    batch_runner.pause_background_batch(original.batch_id, tmp_path)
    time.sleep(0.05)
    counts = _counts(tmp_path)

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(batch_runner, "_write_status", fail_write)
        with pytest.raises(OSError, match="disk full"):
            batch_runner.resume_background_batch(original.batch_id, tmp_path)
    assert batch_runner.get_background_batch_status(original.batch_id, tmp_path).status == "paused"
    time.sleep(0.1)
    assert _counts(tmp_path) == counts


def test_failed_resume_signal_restores_paused_status(controlled_worker, monkeypatch, tmp_path):
    _, original = controlled_worker
    paused = batch_runner.pause_background_batch(original.batch_id, tmp_path)

    def fail_signal(*args, **kwargs):
        raise PermissionError("cannot signal worker")

    with monkeypatch.context() as patch:
        patch.setattr(os, "killpg", fail_signal)
        with pytest.raises(PermissionError, match="cannot signal worker"):
            batch_runner.resume_background_batch(original.batch_id, tmp_path)
    assert batch_runner.get_background_batch_status(original.batch_id, tmp_path) == paused


def test_pause_refuses_ui_process_and_shared_process_group(monkeypatch, tmp_path):
    current = batch_runner.BackgroundBatchStatus("unsafe", "running", batch_runner._now(), pid=os.getpid())
    batch_runner._write_status(current, tmp_path)
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        pytest.skip("process group pause requires Linux or macOS")

    def unexpected_signal(*args, **kwargs):
        raise AssertionError("unsafe process group must never be signalled")

    monkeypatch.setattr(os, "killpg", unexpected_signal)
    with pytest.raises(RuntimeError, match="separate worker"):
        batch_runner.pause_background_batch(current.batch_id, tmp_path)
    monkeypatch.setattr(batch_runner, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(batch_runner, "_legacy_worker_matches", lambda status, root: True)
    monkeypatch.setattr(os, "getpgid", lambda pid: os.getpgrp())
    monkeypatch.setattr(os, "getsid", lambda pid: pid)
    batch_runner._write_status(replace(current, pid=os.getpid() + 100000), tmp_path)
    with pytest.raises(RuntimeError, match="isolated process group"):
        batch_runner.pause_background_batch(current.batch_id, tmp_path)


def test_dead_paused_worker_is_failed(controlled_worker, tmp_path):
    process, original = controlled_worker
    batch_runner.pause_background_batch(original.batch_id, tmp_path)
    os.killpg(process.pid, signal.SIGCONT)
    process.terminate()
    # Observe a zombie before Popen reaps it: kill(pid, 0) alone is insufficient.
    _wait_for(lambda: not batch_runner._pid_is_alive(process.pid))
    status = batch_runner.get_background_batch_status(original.batch_id, tmp_path)
    assert status.status == "failed"
    assert status.paused_at is None
    assert "exited unexpectedly" in status.error
    process.wait(timeout=5)
    with pytest.raises(RuntimeError, match="paused background batch"):
        batch_runner.resume_background_batch(original.batch_id, tmp_path)


def test_pause_on_windows_is_explicitly_unsupported(monkeypatch, tmp_path):
    status = batch_runner.BackgroundBatchStatus("windows", "running", batch_runner._now(), pid=os.getpid())
    batch_runner._write_status(status, tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="Linux and macOS"):
        batch_runner.pause_background_batch(status.batch_id, tmp_path)


def test_reused_pid_is_failed_without_signalling(controlled_worker, monkeypatch, tmp_path):
    process, original = controlled_worker
    stale = replace(original, status="paused", process_start_id="another-boot:123")
    batch_runner._write_status(stale, tmp_path)
    batch_runner._RUNS.pop(original.batch_id)

    def unexpected_signal(*args, **kwargs):
        raise AssertionError("a reused PID must never be signalled")

    with monkeypatch.context() as patch:
        patch.setattr(os, "killpg", unexpected_signal)
        status = batch_runner.get_background_batch_status(original.batch_id, tmp_path)
        assert status.status == "failed"
        with pytest.raises(RuntimeError, match="paused background batch"):
            batch_runner.resume_background_batch(original.batch_id, tmp_path)
    assert process.poll() is None


def test_legacy_status_cannot_control_an_unrelated_session(controlled_worker, tmp_path):
    process, original = controlled_worker
    batch_runner._write_status(replace(original, process_start_id=None), tmp_path)
    # This fixture is a separate session, but its command is not batch_worker.
    assert not batch_runner._legacy_worker_matches(original, tmp_path)
    assert batch_runner.get_background_batch_status(original.batch_id, tmp_path).status == "failed"
    assert process.poll() is None


def test_status_recovers_ui_crash_between_signal_and_status_write(controlled_worker, tmp_path):
    process, original = controlled_worker
    os.killpg(process.pid, signal.SIGSTOP)
    _wait_for(lambda: batch_runner._process_state(process.pid) in {"T", "t"})
    assert batch_runner._read_status(original.batch_id, tmp_path).status == "running"
    recovered = batch_runner.get_background_batch_status(original.batch_id, tmp_path)
    assert recovered.status == "paused"
    resumed = batch_runner.resume_background_batch(original.batch_id, tmp_path)
    assert resumed.status == "running"
    assert resumed.pid == process.pid


def test_pause_does_not_replace_completion_racing_with_signal(controlled_worker, monkeypatch, tmp_path):
    process, original = controlled_worker
    actual_signal = os.killpg

    def signal_after_completion(group, signum):
        if signum == signal.SIGSTOP:
            batch_runner._write_status(replace(original, status="done", finished_at=batch_runner._now()), tmp_path)
        actual_signal(group, signum)

    with monkeypatch.context() as patch:
        patch.setattr(os, "killpg", signal_after_completion)
        result = batch_runner.pause_background_batch(original.batch_id, tmp_path)
    assert result.status == "done"
    assert batch_runner.get_background_batch_status(original.batch_id, tmp_path).status == "done"
    _wait_for(lambda: batch_runner._process_state(process.pid) not in {"T", "t"})
