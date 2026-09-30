from __future__ import annotations

import json
import os
import shlex
import sys
import time

import pytest

from bili_recipe_notes import ai_review_runner as runner
from bili_recipe_notes import ai_review_worker, cli, ui_launcher
from test_ai_review import answer, write_recipe


def _prepare_without_process(monkeypatch, root, **options):
    def launch(out_dir, state):
        state.update(status="paused", run_token="testing", pid=None, process_start_id=None)
        return runner._write(out_dir, state)
    monkeypatch.setattr(runner, "_launch", launch)
    return runner.start_ai_review_batch(root, provider="local", **options)


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
    assert paused["failed"] == 1
    assert paused["completed"] == 1
    assert calls == ["a", "b"]
    # Simulate loss of all UI memory. State is loaded from disk only.
    runner._PROCESSES.clear()
    restored = runner.get_ai_review_batch(tmp_path)
    assert restored["items"] == paused["items"]
    resumed = runner.resume_ai_review_batch(tmp_path, batch["batch_id"])
    # The fake launch resets only the stop request; production does this in _launch.
    runner._update(tmp_path, batch["batch_id"], lambda state: state.update(stop_requested=False))
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
