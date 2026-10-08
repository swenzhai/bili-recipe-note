from __future__ import annotations

import json
import os
import shlex
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from bili_recipe_notes import ai_review_runner as runner
from bili_recipe_notes import ai_review_worker, cli, ui_launcher
from test_ai_review import answer, write_recipe


def _prepare_without_process(monkeypatch, root, **options):
    def launch(out_dir, state):
        state.update(status="paused", run_token="testing", pid=None, process_start_id=None,
                     run_processed=0, stop_requested=False, pause_reason=None)
        return runner._write(out_dir, state)
    monkeypatch.setattr(runner, "_launch", launch)
    return runner.start_ai_review_batch(root, provider="local", **options)


@pytest.fixture
def launch_without_worker(monkeypatch):
    """Exercise durable launch bookkeeping without starting any AI process."""
    processes = []

    class Worker:
        pid = os.getpid()

        def __init__(self, *args, **kwargs):
            processes.append(self)

        def poll(self):
            return None

    monkeypatch.setattr(runner.subprocess, "Popen", Worker)
    yield processes
    for batch_id, process in list(runner._PROCESSES.items()):
        if process in processes:
            runner._PROCESSES.pop(batch_id)
    runner._RETIRED_PROCESSES[:] = [process for process in runner._RETIRED_PROCESSES
                                   if process not in processes]


@pytest.fixture
def task_clock(monkeypatch):
    from bili_recipe_notes import admin_tasks
    current = [datetime(2026, 10, 6, tzinfo=timezone.utc)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(admin_tasks, "datetime", Clock)
    monkeypatch.setattr(runner, "_now", lambda: current[0].isoformat())
    return current


def test_paused_duration_stays_fixed_and_resume_counts_only_new_run(monkeypatch, tmp_path, launch_without_worker, task_clock):
    from bili_recipe_notes.admin_tasks import _duration
    for name in ("a", "b", "c"):
        write_recipe(tmp_path, name)

    def review(folder, **options):
        task_clock[0] += timedelta(seconds=10)
        return answer()

    monkeypatch.setattr(runner, "review_recipe", review)
    batch = runner.start_ai_review_batch(tmp_path, provider="local", run_limit=1)
    paused = runner.run_ai_review_batch(tmp_path, batch["batch_id"], batch["run_token"])
    first_run = dict(paused["runs"][0])
    assert first_run["finished_at"] == paused["finished_at"]
    assert first_run["status"] == "paused" and first_run["run_processed"] == 1
    assert _duration(paused) == 10
    task_clock[0] += timedelta(hours=1)
    assert _duration(runner.get_ai_review_batch(tmp_path, batch["batch_id"])) == 10
    resumed = runner.resume_ai_review_batch(tmp_path, batch["batch_id"])
    assert resumed["runs"][0] == first_run and not resumed["runs"][1].get("finished_at")
    task_clock[0] += timedelta(seconds=7)
    assert _duration(resumed) == 17
    paused_again = runner.run_ai_review_batch(tmp_path, batch["batch_id"], resumed["run_token"])
    assert _duration(paused_again) == 27
    task_clock[0] += timedelta(hours=1)
    assert _duration(runner.get_ai_review_batch(tmp_path, batch["batch_id"])) == 27
    last = runner.resume_ai_review_batch(tmp_path, batch["batch_id"], run_limit=0)
    final = runner.run_ai_review_batch(tmp_path, batch["batch_id"], last["run_token"])
    assert final["status"] == "completed" and _duration(final) == 37
    assert all(run["finished_at"] and run["run_processed"] == 1 for run in final["runs"])


@pytest.mark.parametrize("reason,error", [
    ("configuration", RuntimeError("invalid api key")),
    ("consecutive_failures", ValueError("invalid synthetic review")),
])
def test_error_pause_closes_run_duration(monkeypatch, tmp_path, launch_without_worker, task_clock, reason, error):
    from bili_recipe_notes.admin_tasks import _duration
    for name in ("a", "b"):
        write_recipe(tmp_path, name)

    def review(folder, **options):
        task_clock[0] += timedelta(seconds=5)
        raise error

    monkeypatch.setattr(runner, "review_recipe", review)
    batch = runner.start_ai_review_batch(tmp_path, provider="local", consecutive_failure_limit=1)
    paused = runner.run_ai_review_batch(tmp_path, batch["batch_id"], batch["run_token"])
    assert paused["pause_reason"] == reason
    assert paused["runs"][0]["status"] == "paused" and paused["runs"][0]["run_processed"] == 1
    task_clock[0] += timedelta(hours=1)
    assert _duration(runner.get_ai_review_batch(tmp_path, batch["batch_id"])) == 5


@pytest.mark.parametrize("interruption", ["dead_process", "worker_exception", "launch_failure"])
def test_interruption_closes_run_duration(monkeypatch, tmp_path, launch_without_worker, task_clock, interruption):
    from bili_recipe_notes.admin_tasks import _duration
    write_recipe(tmp_path)
    if interruption == "launch_failure":
        def fail_launch(*args, **kwargs):
            task_clock[0] += timedelta(seconds=4)
            raise OSError("synthetic launch failure")
        monkeypatch.setattr(runner.subprocess, "Popen", fail_launch)
        with pytest.raises(OSError, match="synthetic launch failure"):
            runner.start_ai_review_batch(tmp_path, provider="local")
        interrupted = runner.get_ai_review_batch(tmp_path)
    else:
        batch = runner.start_ai_review_batch(tmp_path, provider="local")
        task_clock[0] += timedelta(seconds=4)
        if interruption == "dead_process":
            monkeypatch.setattr(runner, "_alive", lambda state: False)
            interrupted = runner.get_ai_review_batch(tmp_path, batch["batch_id"])
        else:
            def fail_worker(*args, **kwargs):
                raise RuntimeError("synthetic worker failure")
            monkeypatch.setattr(ai_review_worker, "run_ai_review_batch", fail_worker)
            assert ai_review_worker.main([str(tmp_path), batch["batch_id"], batch["run_token"]]) == 1
            interrupted = runner.get_ai_review_batch(tmp_path, batch["batch_id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["runs"][0]["finished_at"] == interrupted["finished_at"]
    assert interrupted["runs"][0]["run_processed"] == 0
    task_clock[0] += timedelta(hours=1)
    assert _duration(runner.get_ai_review_batch(tmp_path, interrupted["batch_id"])) == 4


@pytest.mark.parametrize("end_field", ["finished_at", "updated_at"])
def test_resume_closes_legacy_runs_using_recorded_boundaries(monkeypatch, tmp_path, launch_without_worker, task_clock, end_field):
    from bili_recipe_notes.admin_tasks import _duration
    write_recipe(tmp_path)
    batch = runner.start_ai_review_batch(tmp_path, provider="local", create_only=True)
    start = task_clock[0]
    first_end = (start + timedelta(seconds=5)).isoformat()
    second_start = (start + timedelta(minutes=10)).isoformat()
    second_end = (start + timedelta(minutes=10, seconds=7)).isoformat()
    batch.update(status="paused", run_token="second", run_processed=1,
                 runs=[{"run_token": "first", "started_at": start.isoformat()},
                       {"run_token": "second", "started_at": second_start}])
    batch["items"][0]["attempts"] = [{"run_token": "first", "status": "failed", "finished_at": first_end}]
    batch[end_field] = second_end
    # Write a legacy state directly: the runner's writer now closes current runs.
    runner.atomic_write_json(runner._state_path(tmp_path, batch["batch_id"]), batch, backup=False)
    task_clock[0] = start + timedelta(hours=2)
    resumed = runner.resume_ai_review_batch(tmp_path, batch["batch_id"])
    assert resumed["runs"][0]["finished_at"] == first_end
    assert resumed["runs"][1]["finished_at"] == second_end
    assert resumed["runs"][1]["run_processed"] == 1
    assert len(resumed["runs"]) == 3 and not resumed["runs"][2].get("finished_at")
    assert _duration(resumed) == 12
    task_clock[0] += timedelta(seconds=3)
    assert _duration(resumed) == 15


def test_full_queue_runs_in_limited_rounds_and_finishes_at_exact_limit(monkeypatch, tmp_path, launch_without_worker):
    for name in ("a", "b", "c", "d", "e"):
        write_recipe(tmp_path, name)
    calls = []
    monkeypatch.setattr(runner, "review_recipe", lambda folder, **options: calls.append(folder.name) or answer())
    batch = runner.start_ai_review_batch(tmp_path, provider="local", run_limit=2)
    assert batch["total"] == 5 and batch["pending"] == 5
    assert batch["run_processed"] == 0 and batch["run_limit"] == 2
    first = runner.run_ai_review_batch(tmp_path, batch["batch_id"], batch["run_token"])
    assert first["status"] == "paused" and first["pause_reason"] == "batch_limit"
    assert first["run_processed"] == 2 and first["completed"] == 2 and first["pending"] == 3
    assert runner.get_ai_review_batch(tmp_path, batch["batch_id"])["run_processed"] == 2
    second_run = runner.resume_ai_review_batch(tmp_path, batch["batch_id"])
    assert second_run["run_limit"] == 2 and second_run["run_processed"] == 0
    assert second_run["pause_reason"] is None
    second = runner.run_ai_review_batch(tmp_path, batch["batch_id"], second_run["run_token"])
    assert second["status"] == "paused" and second["pause_reason"] == "batch_limit"
    assert second["run_processed"] == 2 and second["completed"] == 4 and second["pending"] == 1
    last_run = runner.resume_ai_review_batch(tmp_path, batch["batch_id"], run_limit=1)
    final = runner.run_ai_review_batch(tmp_path, batch["batch_id"], last_run["run_token"])
    assert final["status"] == "completed" and final["pause_reason"] is None
    assert final["completed"] == 5 and final["run_processed"] == 1 and final["pending"] == 0
    assert calls == ["a", "b", "c", "d", "e"]
    assert [run["run_limit"] for run in final["runs"]] == [2, 2, 1]


def test_resume_zero_removes_run_limit_without_changing_queue_scope(monkeypatch, tmp_path, launch_without_worker):
    for name in ("a", "b", "c", "d"):
        write_recipe(tmp_path, name)
    monkeypatch.setattr(runner, "review_recipe", lambda folder, **options: answer())
    batch = runner.start_ai_review_batch(tmp_path, provider="local", limit=3, run_limit=1)
    assert [item["folder"] for item in batch["items"]] == ["a", "b", "c"]
    runner.run_ai_review_batch(tmp_path, batch["batch_id"], batch["run_token"])
    resumed = runner.resume_ai_review_batch(tmp_path, batch["batch_id"], run_limit=0)
    final = runner.run_ai_review_batch(tmp_path, batch["batch_id"], resumed["run_token"])
    assert final["status"] == "completed" and final["completed"] == 3
    assert final["run_limit"] == 0 and final["run_processed"] == 2
    assert [run["run_limit"] for run in final["runs"]] == [1, 0]


@pytest.mark.parametrize("run_limit", [0, 1])
def test_requested_pause_takes_priority_over_run_limit(monkeypatch, tmp_path, launch_without_worker, run_limit):
    for name in ("a", "b"):
        write_recipe(tmp_path, name)
    batch = runner.start_ai_review_batch(tmp_path, provider="local", run_limit=run_limit)

    def review(folder, **options):
        runner.request_stop_ai_review_batch(tmp_path, batch["batch_id"])
        return answer()

    monkeypatch.setattr(runner, "review_recipe", review)
    paused = runner.run_ai_review_batch(tmp_path, batch["batch_id"], batch["run_token"])
    assert paused["status"] == "paused" and paused["pause_reason"] == "requested"
    assert paused["run_processed"] == 1 and paused["completed"] == 1 and paused["pending"] == 1


def test_failures_skips_and_completed_items_count_once_despite_retries(monkeypatch, tmp_path, launch_without_worker):
    for name in ("a", "b", "c", "d", "e"):
        write_recipe(tmp_path, name)
    batch = runner.start_ai_review_batch(tmp_path, provider="local", run_limit=3, retry_base_seconds=0)
    monkeypatch.setattr(runner, "load_ai_review", lambda folder: answer() if folder.name == "c" else None)
    calls = []

    def review(folder, **options):
        calls.append(folder.name)
        if folder.name == "a":
            raise ValueError("invalid synthetic review")
        if folder.name == "b" and calls.count("b") == 1:
            raise TimeoutError("synthetic timeout")
        return answer()

    monkeypatch.setattr(runner, "review_recipe", review)
    paused = runner.run_ai_review_batch(tmp_path, batch["batch_id"], batch["run_token"])
    assert calls == ["a", "b", "b"]
    assert [item["status"] for item in paused["items"]] == ["failed", "completed", "skipped", "pending", "pending"]
    assert len(paused["items"][1]["attempts"]) == 2
    assert paused["status"] == "paused" and paused["pause_reason"] == "batch_limit"
    assert paused["run_processed"] == 3 and paused["pending"] == 2
    resumed = runner.resume_ai_review_batch(tmp_path, batch["batch_id"], run_limit=0)
    final = runner.run_ai_review_batch(tmp_path, batch["batch_id"], resumed["run_token"])
    assert final["status"] == "completed_with_errors" and final["pause_reason"] is None
    assert final["run_processed"] == 2 and final["pending"] == 0


def test_create_only_saves_pending_without_launch_or_review(monkeypatch, tmp_path, launch_without_worker):
    for name in ("a", "b", "c"):
        write_recipe(tmp_path, name)
    originals = {file: file.read_bytes() for file in tmp_path.glob("*/*") if file.is_file()}
    monkeypatch.setattr(runner, "review_recipe", lambda *args, **kwargs: pytest.fail("must not review when creating"))
    batch = runner.start_ai_review_batch(tmp_path, provider="local", create_only=True, run_limit=2)
    assert batch["status"] == "pending" and not batch["active"]
    assert batch["total"] == 3 and batch["pending"] == 3
    assert batch["run_processed"] == 0 and batch["run_limit"] == 2
    assert batch["pid"] is None and not batch.get("runs")
    assert not launch_without_worker
    assert not list((tmp_path / ".ai-review").glob("*.log"))
    assert runner.get_ai_review_batch(tmp_path, batch["batch_id"])["status"] == "pending"
    assert all(file.read_bytes() == content for file, content in originals.items())
    monkeypatch.setattr(runner, "review_recipe", lambda folder, **options: answer())
    resumed = runner.resume_ai_review_batch(tmp_path, batch["batch_id"])
    assert len(launch_without_worker) == 1
    paused = runner.run_ai_review_batch(tmp_path, batch["batch_id"], resumed["run_token"])
    assert paused["status"] == "paused" and paused["completed"] == 2 and paused["pending"] == 1


def test_create_only_can_queue_during_active_run_while_normal_start_deduplicates(tmp_path, launch_without_worker):
    write_recipe(tmp_path)
    active = runner.start_ai_review_batch(tmp_path, provider="local")
    assert active["active"]
    queued = runner.start_ai_review_batch(tmp_path, provider="local", create_only=True, run_limit=1)
    assert queued["batch_id"] != active["batch_id"]
    assert queued["status"] == "pending" and not queued["active"]
    assert queued["pending"] == 1 and len(launch_without_worker) == 1
    duplicate = runner.start_ai_review_batch(tmp_path, provider="local")
    assert duplicate["batch_id"] == active["batch_id"] and len(launch_without_worker) == 1
    with pytest.raises(RuntimeError, match="已有 AI 审核任务运行"):
        runner.resume_ai_review_batch(tmp_path, queued["batch_id"])
    assert runner.get_ai_review_batch(tmp_path, queued["batch_id"])["status"] == "pending"


def test_old_pending_state_resumes_without_run_limit(monkeypatch, tmp_path, launch_without_worker):
    write_recipe(tmp_path)
    batch = runner.start_ai_review_batch(tmp_path, provider="local", create_only=True)
    batch.pop("run_limit")
    batch.pop("run_processed")
    runner._write(tmp_path, batch)
    monkeypatch.setattr(runner, "review_recipe", lambda folder, **options: answer())
    resumed = runner.resume_ai_review_batch(tmp_path, batch["batch_id"])
    assert resumed["run_limit"] == 0 and resumed["run_processed"] == 0
    final = runner.run_ai_review_batch(tmp_path, batch["batch_id"], resumed["run_token"])
    assert final["status"] == "completed" and final["run_processed"] == 1


@pytest.mark.parametrize("invalid", [-1, True, 1.5, "2"])
def test_invalid_run_limit_is_rejected_at_start_and_resume(tmp_path, invalid):
    with pytest.raises(ValueError, match="本轮审核数量"):
        runner.start_ai_review_batch(tmp_path, run_limit=invalid)
    with pytest.raises(ValueError, match="本轮审核数量"):
        runner.resume_ai_review_batch(tmp_path, "missing", run_limit=invalid)


def test_invalid_create_only_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="仅创建任务"):
        runner.start_ai_review_batch(tmp_path, create_only="false")


def test_failures_do_not_interrupt_and_pause_resume_skips_completed(monkeypatch, tmp_path):
    for name in ("a", "b", "c"):
        write_recipe(tmp_path, name)
    batch = _prepare_without_process(monkeypatch, tmp_path, only_pending=False)
    calls = []
    def review(folder, **options):
        calls.append(folder.name)
        if folder.name == "a":
            raise RuntimeError("temporary provider error")
        if folder.name == "b":
            runner.request_stop_ai_review_batch(tmp_path, batch["batch_id"])
        return answer()
    monkeypatch.setattr(runner, "review_recipe", review)
    paused = runner.run_ai_review_batch(tmp_path, batch["batch_id"], "testing")
    assert paused["status"] == "paused"
    assert paused["pause_reason"] == "requested"
    assert paused["failed"] == 1
    assert paused["completed"] == 1
    assert calls == ["a", "b"]
    # Simulate loss of all UI memory. State is loaded from disk only.
    runner._PROCESSES.clear()
    restored = runner.get_ai_review_batch(tmp_path)
    assert restored["items"] == paused["items"]
    resumed = runner.resume_ai_review_batch(tmp_path, batch["batch_id"])
    final = runner.run_ai_review_batch(tmp_path, resumed["batch_id"], "testing")
    assert calls == ["a", "b", "c"]
    assert final["status"] == "completed_with_errors"
    assert final["completed"] == 2
    monkeypatch.setattr(runner, "review_recipe", lambda folder, **options: calls.append(folder.name) or answer())
    runner.resume_ai_review_batch(tmp_path, batch["batch_id"], retry_failed=True)
    final = runner.run_ai_review_batch(tmp_path, batch["batch_id"], "testing")
    assert calls == ["a", "b", "c", "a"]
    assert final["status"] == "completed"


def test_interrupted_process_can_resume_running_item(monkeypatch, tmp_path):
    write_recipe(tmp_path)
    state = _prepare_without_process(monkeypatch, tmp_path)
    state.update(status="running", pid=999999999, process_start_id="stale")
    state["items"][0]["status"] = "running"
    runner._write(tmp_path, state)
    status = runner.get_ai_review_batch(tmp_path, state["batch_id"])
    assert status["status"] == "interrupted"
    assert not status["active"]
    resumed = runner.resume_ai_review_batch(tmp_path, state["batch_id"])
    assert resumed["items"][0]["status"] == "pending"


def test_scoped_folders_limit_and_path_traversal(monkeypatch, tmp_path):
    out = tmp_path / "outputs"
    out.mkdir()
    a = write_recipe(out, "a")
    write_recipe(out, "b")
    outside = write_recipe(tmp_path, "outside")
    batch = _prepare_without_process(monkeypatch, out, folders=[str(a)], limit=1)
    assert [item["folder"] for item in batch["items"]] == ["a"]
    for malicious in (outside, out / ".." / "outside"):
        with pytest.raises(ValueError, match="菜谱子目录"):
            runner.start_ai_review_batch(out, folders=[malicious])
    with pytest.raises(ValueError, match="任务 ID"):
        runner.get_ai_review_batch(out, "../../outside")
    if hasattr(os, "symlink"):
        (out / "linked").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="符号链接"):
            runner.start_ai_review_batch(out, folders=[out / "linked"])


def _wait(root, batch_id, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = runner.get_ai_review_batch(root, batch_id)
        if predicate(state):
            return state
        time.sleep(0.02)
    raise AssertionError(f"worker timeout; last state: {state}")


def test_real_subprocess_survives_ui_restart_and_rejects_duplicate_start(tmp_path):
    root = tmp_path / "outputs"
    root.mkdir()
    write_recipe(root, "a")
    write_recipe(root, "b")
    fake = tmp_path / "fake provider.py"
    fake.write_text(
        "import json, sys, time\n"
        "prompt = sys.stdin.read()\n"
        "assert 'untrusted_material' in prompt\n"
        "time.sleep(0.15)\n"
        f"print({json.dumps(json.dumps(answer(), ensure_ascii=False))})\n", encoding="utf-8",
    )
    command = shlex.join([sys.executable, str(fake)])
    originals = {str(file.relative_to(root)): file.read_bytes() for file in root.glob("*/*")}
    first = runner.start_ai_review_batch(root, provider="local", local_llm_command=command, only_pending=False)
    second = runner.start_ai_review_batch(root, provider="local", local_llm_command=command)
    assert first["batch_id"] == second["batch_id"]
    assert first["pid"] != os.getpid()
    processes = dict(runner._PROCESSES)
    runner._PROCESSES.clear()
    try:
        final = _wait(root, first["batch_id"], lambda state: state["status"] in {"completed", "completed_with_errors", "interrupted"})
        assert final["status"] == "completed", final
        assert final["completed"] == 2
        assert not final["active"]
        assert originals == {name: (root / name).read_bytes() for name in originals}
    finally:
        for process in processes.values():
            process.wait(timeout=10)


@pytest.mark.parametrize("entrypoint", [cli.main, ui_launcher.main])
def test_frozen_entrypoint_dispatches_ai_worker(monkeypatch, entrypoint):
    seen = []
    monkeypatch.setattr(sys, "argv", ["app", "--ai-review-worker", "/out", "audit-test", "token"])
    monkeypatch.setattr(ai_review_worker, "main", lambda args: seen.append(args) or 0)
    assert entrypoint() == 0
    assert seen == [["/out", "audit-test", "token"]]


def test_windows_restart_uses_query_only_identity_without_signalling(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(runner, "_windows_process_identity", lambda pid: "windows:123:456")
    monkeypatch.setattr(runner, "_pid_is_alive", lambda pid: pytest.fail("Windows must not use os.kill"))
    runner._PROCESSES.clear()
    state = {"batch_id": "test", "pid": 100, "process_start_id": "windows:123:456"}
    assert runner._alive(state)
    state["process_start_id"] = "windows:other:process"
    assert not runner._alive(state)


def test_finished_worker_handles_are_reaped():
    process = __import__("subprocess").Popen([sys.executable, "-c", "pass"])
    runner._PROCESSES["reap-test"] = process
    deadline = time.monotonic() + 5
    while "reap-test" in runner._PROCESSES and time.monotonic() < deadline:
        runner._reap_finished_processes()
        time.sleep(0.01)
    assert "reap-test" not in runner._PROCESSES
    assert process.returncode == 0


def test_transient_retry_records_attempts_and_configuration(monkeypatch, tmp_path):
    write_recipe(tmp_path)
    state = _prepare_without_process(monkeypatch, tmp_path, retry_base_seconds=0, local_llm_command="synthetic-command")
    calls = []
    def review(folder, **options):
        calls.append(options)
        if len(calls) == 1:
            raise TimeoutError("request timed out")
        return answer()
    monkeypatch.setattr(runner, "review_recipe", review)
    result = runner.run_ai_review_batch(tmp_path, state["batch_id"], "testing")
    assert result["status"] == "completed"
    attempts = result["items"][0]["attempts"]
    assert [attempt["status"] for attempt in attempts] == ["failed", "completed"]
    assert attempts[0]["error_kind"] == "transient"
    assert attempts[0]["llm_options"]["local_llm_command"] == "synthetic-command"
    assert attempts[0]["retry_delay_seconds"] == 0


def test_configuration_error_pauses_immediately_and_current_settings_fix_retry(monkeypatch, tmp_path):
    write_recipe(tmp_path, "a")
    write_recipe(tmp_path, "b")
    state = _prepare_without_process(monkeypatch, tmp_path, local_llm_command="missing-command", retry_base_seconds=0)
    calls = []
    def review(folder, **options):
        calls.append(options)
        if options["local_llm_command"] == "missing-command":
            raise RuntimeError("local: No such file or directory: missing-command")
        return answer()
    monkeypatch.setattr(runner, "review_recipe", review)
    paused = runner.run_ai_review_batch(tmp_path, state["batch_id"], "testing")
    assert paused["status"] == "paused" and paused["pause_reason"] == "configuration"
    assert len(calls) == 1
    assert paused["items"][1]["status"] == "pending"
    runner.resume_ai_review_batch(tmp_path, state["batch_id"], retry_failed=True,
                                 provider="local", local_llm_command="fixed-command")
    result = runner.run_ai_review_batch(tmp_path, state["batch_id"], "testing")
    assert result["status"] == "completed"
    attempts = result["items"][0]["attempts"]
    assert [attempt["llm_options"]["local_llm_command"] for attempt in attempts] == ["missing-command", "fixed-command"]
    assert attempts[0]["error"] and attempts[1]["status"] == "completed"


def test_bounded_transient_retries_and_consecutive_failures_auto_pause(monkeypatch, tmp_path):
    for name in ("a", "b", "c"):
        write_recipe(tmp_path, name)
    state = _prepare_without_process(monkeypatch, tmp_path, max_attempts=2, consecutive_failure_limit=2, retry_base_seconds=0)
    calls = []
    def review(folder, **options):
        calls.append(folder.name)
        raise TimeoutError("connection timeout")
    monkeypatch.setattr(runner, "review_recipe", review)
    result = runner.run_ai_review_batch(tmp_path, state["batch_id"], "testing")
    assert calls == ["a", "a", "b", "b"]
    assert result["status"] == "paused" and result["pause_reason"] == "consecutive_failures"
    assert result["failed"] == 2 and result["pending"] == 1
    assert all(len(item["attempts"]) == 2 for item in result["items"][:2])


def test_retry_new_configuration_retains_review_and_latest_manual_decisions(monkeypatch, tmp_path):
    from bili_recipe_notes import ai_review, review_policy as policy
    folder = write_recipe(tmp_path)
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", lambda *args, **kwargs: json.dumps(answer()))
    ai_review.review_recipe(folder, provider="local", local_llm_command="original")
    policy.set_ai_review_visibility(folder, "hidden")
    policy.set_ai_review_classification(folder, "recipe")
    policy.set_ai_review_item_decision(folder, "steps:0", "supported", comment="人工确认炒熟")
    original = (folder / policy.AI_REVIEW_FILE_NAME).read_bytes()
    state = _prepare_without_process(monkeypatch, tmp_path, only_pending=False, local_llm_command="missing-command")
    def bad_provider(*args, **kwargs):
        raise RuntimeError("No such file or directory: missing-command")
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", bad_provider)
    assert runner.run_ai_review_batch(tmp_path, state["batch_id"], "testing")["status"] == "paused"
    assert (folder / policy.AI_REVIEW_FILE_NAME).read_bytes() == original
    policy.set_ai_review_visibility(folder, "visible")
    def fixed_provider(*args, **kwargs):
        assert kwargs["local_llm_command"] == "fixed-command"
        return json.dumps(answer())
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", fixed_provider)
    runner.resume_ai_review_batch(tmp_path, state["batch_id"], retry_failed=True, local_llm_command="fixed-command")
    finished = runner.run_ai_review_batch(tmp_path, state["batch_id"], "testing")
    assert finished["status"] == "completed"
    record = policy.load_review_record(folder)
    assert record["visibility_override"] == "visible" and record["classification_override"] == "recipe"
    assert record["manual_review"]["items"]["steps:0"]["verdict"] == "supported"
    assert len(finished["items"][0]["attempts"]) == 2
    assert policy.list_review_history(folder)[0]["review"]["visibility_override"] == "visible"
