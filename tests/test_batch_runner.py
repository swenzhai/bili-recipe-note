from __future__ import annotations

import time

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
