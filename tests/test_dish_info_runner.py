from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bili_recipe_notes import cli, dish_info, dish_info_runner as runner, dish_info_worker, ui_launcher
from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.review_policy import set_ai_review_visibility
from test_dish_info import answer


@pytest.fixture(autouse=True)
def isolated_workspace(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(runner, "_PROCESSES", {})


def recipe(root: Path, name: str = "one") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for filename, value in {
        "recipe.json": {"title": "炒鸡蛋", "source_url": f"https://example.com/{name}",
                        "ingredients": [{"name": "鸡蛋", "amount": "2个"}], "seasonings": [],
                        "steps": [{"action": "中火炒熟"}]},
        "transcript.json": [{"start": 0, "text": "准备两个鸡蛋，中火炒熟。"}],
        "source.json": {"title": "完整菜谱与购买提示"},
        "job.json": {"status": "completed"},
        "quality.json": {"status": "original"},
        "recipe.review.json": {"manual": "keep"},
    }.items():
        (folder / filename).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    (folder / "note.md").write_bytes(b"Original note\r\n")
    return folder


def prepare(monkeypatch, root, **options):
    def launch(out_dir, state):
        token = f"testing-{len(state.get('runs', [])) + 1}"
        state.update(status="paused", run_token=token, pid=None, process_start_id=None,
                     stop_requested=False, pause_reason=None, error=None, consecutive_failures=0)
        state.setdefault("runs", []).append({"run_token": token, "started_at": runner._now(),
                                             "llm_options": dict(state["options"])})
        return runner._write(out_dir, state)
    monkeypatch.setattr(runner, "_launch", launch)
    return runner.start_dish_info_batch(root, provider="local", **options)


def run(root, state):
    return runner.run_dish_info_batch(root, state["batch_id"], state["run_token"])


def original_bytes(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*")
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(root).parts)}


def test_selection_honors_visibility_publication_scope_and_limit(monkeypatch, tmp_path):
    root = tmp_path / "outputs"
    for name in ("up/a", "up/b", "up/c", "up/d"):
        recipe(root, name)
    store = MobileSyncStore(tmp_path, out_dir=root)
    store.index_recipes()
    unlisted_id = json.loads((root / "up/b/sync-meta.json").read_text())["recipe_id"]
    store.set_recipe_publications({unlisted_id: False})
    set_ai_review_visibility(root / "up/c", "hidden")
    originals = original_bytes(root)
    state = prepare(monkeypatch, root, folders=[root / "up/b", "up/c", "up/a", "up/d"], limit=1)
    assert [item["folder"] for item in state["items"]] == ["up/a"]
    assert original_bytes(root) == originals


@pytest.mark.parametrize("search_status,web_search,expected", [
    ("not_requested", False, 0), ("not_requested", True, 1),
    ("completed", True, 0), ("no_results", True, 0), ("failed", True, 1),
])
def test_valid_sidecars_are_skipped_consistently(monkeypatch, tmp_path, search_status, web_search, expected):
    recipe(tmp_path)
    monkeypatch.setattr(runner, "load_dish_info", lambda _: {"search_status": search_status})
    state = prepare(monkeypatch, tmp_path, web_search=web_search)
    assert state["total"] == expected


def test_runtime_rechecks_visibility_and_new_valid_intro(monkeypatch, tmp_path):
    first = recipe(tmp_path, "a")
    second = recipe(tmp_path, "b")
    state = prepare(monkeypatch, tmp_path)
    set_ai_review_visibility(first, "hidden")
    monkeypatch.setattr(runner, "load_dish_info", lambda folder: {"search_status": "no_results"} if folder == second else None)
    monkeypatch.setattr(runner, "generate_dish_info", lambda *a, **k: pytest.fail("ineligible item reached model"))
    final = run(tmp_path, state)
    assert final["status"] == "completed" and final["skipped"] == 2
    assert [item["skip_reason"] for item in final["items"]] == ["not_visible_or_published", "valid_info"]


def test_generation_preserves_originals_and_archives_in_root_history(monkeypatch, tmp_path):
    folder = recipe(tmp_path)
    originals = original_bytes(tmp_path)
    monkeypatch.setattr(dish_info, "complete_markdown_prompt", lambda *a, **k: json.dumps(answer()))
    state = prepare(monkeypatch, tmp_path, only_pending=False)
    assert run(tmp_path, state)["completed"] == 1
    previous = (folder / dish_info.DISH_INFO_FILE_NAME).read_bytes()
    monkeypatch.setattr(dish_info, "complete_markdown_prompt", lambda *a, **k: json.dumps(answer(summary="新的简介")))
    state = runner.start_dish_info_batch(tmp_path, provider="local", only_pending=False)
    assert run(tmp_path, state)["completed"] == 1
    history = list((tmp_path / ".dish-info/history").rglob("*.json"))
    assert len(history) == 1 and history[0].read_bytes() == previous
    assert not (folder / ".dish-info").exists()
    assert {name: (tmp_path / name).read_bytes() for name in originals} == originals


def test_transient_failure_retries_and_records_attempt_options(monkeypatch, tmp_path):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path, local_llm_command="synthetic")
    attempts = []
    delays = []
    def generate(folder, **options):
        attempts.append(options)
        if len(attempts) < 3:
            raise TimeoutError("timed out")
        return {"search_status": "not_requested"}
    monkeypatch.setattr(runner, "generate_dish_info", generate)
    monkeypatch.setattr(runner.time, "sleep", delays.append)
    final = run(tmp_path, state)
    assert final["status"] == "completed"
    history = final["items"][0]["attempts"]
    assert [attempt["status"] for attempt in history] == ["failed", "failed", "completed"]
    assert history[0]["error_kind"] == "transient"
    assert history[0]["llm_options"]["local_llm_command"] == "synthetic"
    assert [attempt["retry_delay_seconds"] for attempt in history[:-1]] == delays == [1, 2]
    assert all(attempt["run_token"] == state["run_token"] and attempt["finished_at"] for attempt in history)
    assert attempts[-1]["history_dir"] == tmp_path / ".dish-info/history"


def test_configuration_error_pauses_and_resume_uses_updated_settings(monkeypatch, tmp_path):
    recipe(tmp_path, "a")
    recipe(tmp_path, "b")
    state = prepare(monkeypatch, tmp_path, local_llm_command="broken", codex_profile="old")
    calls = []
    def generate(folder, **options):
        calls.append((folder.name, options))
        if options["local_llm_command"] == "broken":
            raise RuntimeError("local: No such file or directory: broken")
        return {}
    monkeypatch.setattr(runner, "generate_dish_info", generate)
    paused = run(tmp_path, state)
    assert paused["status"] == "paused" and paused["pause_reason"] == "configuration"
    assert len(calls) == 1 and paused["pending"] == 1
    resumed = runner.resume_dish_info_batch(tmp_path, state["batch_id"], retry_failed=True,
                                             local_llm_command="fixed", codex_profile=None)
    final = run(tmp_path, resumed)
    assert final["status"] == "completed" and final["completed"] == 2
    assert [name for name, _ in calls] == ["a", "a", "b"]
    assert final["items"][0]["attempts"][0]["llm_options"]["local_llm_command"] == "broken"
    assert final["items"][0]["attempts"][1]["llm_options"]["local_llm_command"] == "fixed"
    assert calls[-1][1]["provider"] == "local" and calls[-1][1]["codex_profile"] is None
    assert final["runs"][-1]["status"] == "completed"


def test_failure_continuation_manual_pause_resume_and_retry(monkeypatch, tmp_path):
    for name in ("a", "b", "c"):
        recipe(tmp_path, name)
    state = prepare(monkeypatch, tmp_path)
    calls = []
    def generate(folder, **options):
        calls.append(folder.name)
        if folder.name == "a":
            raise ValueError("invalid model output")
        if folder.name == "b":
            runner.stop_dish_info_batch(tmp_path, state["batch_id"])
        return {}
    monkeypatch.setattr(runner, "generate_dish_info", generate)
    paused = run(tmp_path, state)
    assert paused["status"] == "paused" and paused["pause_reason"] == "requested"
    assert (paused["completed"], paused["failed"], paused["pending"]) == (1, 1, 1)
    resumed = runner.resume_dish_info_batch(tmp_path, state["batch_id"])
    final = run(tmp_path, resumed)
    assert final["status"] == "completed_with_errors" and calls == ["a", "b", "c"]
    monkeypatch.setattr(runner, "generate_dish_info", lambda folder, **k: calls.append(folder.name) or {})
    retried = runner.resume_dish_info_batch(tmp_path, state["batch_id"], retry_failed=True)
    final = run(tmp_path, retried)
    assert final["status"] == "completed" and calls == ["a", "b", "c", "a"]


def test_retry_bound_and_consecutive_failure_pause(monkeypatch, tmp_path):
    for index in range(6):
        recipe(tmp_path, str(index))
    state = prepare(monkeypatch, tmp_path)
    def failed(*args, **kwargs):
        raise TimeoutError("network timed out")
    monkeypatch.setattr(runner, "generate_dish_info", failed)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    final = run(tmp_path, state)
    assert final["status"] == "paused" and final["pause_reason"] == "consecutive_failures"
    assert (final["failed"], final["pending"]) == (5, 1)
    assert all(len(item["attempts"]) == 3 for item in final["items"][:5])


def test_interrupted_item_and_attempt_resume_without_repeating_completed(monkeypatch, tmp_path):
    recipe(tmp_path, "a")
    recipe(tmp_path, "b")
    state = prepare(monkeypatch, tmp_path)
    state.update(status="running", pid=999999999, process_start_id="gone")
    state["items"][0]["status"] = "completed"
    state["items"][1].update(status="running", attempts=[{"run_token": state["run_token"], "status": "running"}])
    runner._write(tmp_path, state)
    interrupted = runner.list_dish_info_batches(tmp_path)[0]
    assert interrupted["status"] == "interrupted" and not interrupted["active"]
    resumed = runner.resume_dish_info_batch(tmp_path, state["batch_id"])
    assert resumed["run_token"] != state["run_token"]
    assert resumed["items"][1]["attempts"][0]["status"] == "interrupted"
    calls = []
    monkeypatch.setattr(runner, "generate_dish_info", lambda folder, **k: calls.append(folder.name) or {})
    assert run(tmp_path, resumed)["completed"] == 2
    assert calls == ["b"]


def test_stale_worker_cannot_rewrite_new_run_even_on_exception(monkeypatch, tmp_path):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path)
    resumed = runner.resume_dish_info_batch(tmp_path, state["batch_id"])
    path = runner._path(tmp_path, state["batch_id"])
    previous = path.read_bytes()
    assert dish_info_worker.main([str(tmp_path), state["batch_id"], state["run_token"]]) == 1
    assert path.read_bytes() == previous
    def changed_token(folder, **options):
        runner._update(tmp_path, state["batch_id"], lambda current: current.update(run_token="new-owner"))
        return {}
    monkeypatch.setattr(runner, "generate_dish_info", changed_token)
    assert dish_info_worker.main([str(tmp_path), resumed["batch_id"], resumed["run_token"]]) == 1
    latest = runner._read(tmp_path, state["batch_id"])
    assert latest["run_token"] == "new-owner" and latest["status"] == "running"
    assert latest["items"][0]["attempts"][0]["status"] == "running"


def test_second_worker_with_same_token_does_not_take_over(monkeypatch, tmp_path):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path)
    state.update(status="running", pid=os.getpid(), process_start_id=runner._identity(os.getpid()))
    runner._write(tmp_path, state)
    previous = runner._path(tmp_path, state["batch_id"]).read_bytes()
    monkeypatch.setattr(runner, "generate_dish_info", lambda *a, **k: pytest.fail("duplicate worker"))
    assert run(tmp_path, state)["status"] == "running"
    assert runner._path(tmp_path, state["batch_id"]).read_bytes() == previous


@pytest.mark.parametrize("malicious", ["../outside", ".private/recipe", ".", "/outside"])
def test_folder_path_escape_rejected(monkeypatch, tmp_path, malicious):
    root = tmp_path / "output"
    recipe(root, "valid")
    recipe(tmp_path, "outside")
    prepare(monkeypatch, root)
    with pytest.raises(ValueError, match="输出目录内"):
        runner.start_dish_info_batch(root, provider="local", folders=[malicious])


def test_symlink_paths_and_bad_ids_rejected(monkeypatch, tmp_path):
    root = tmp_path / "output"
    recipe(root)
    outside = recipe(tmp_path, "outside")
    state = prepare(monkeypatch, root)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        runner.start_dish_info_batch(root, provider="local", folders=[root / "linked"])
    for batch_id in ("../../outside", None, 17):
        with pytest.raises(ValueError, match="任务 ID"):
            runner.resume_dish_info_batch(root, batch_id)
    path = runner._path(root, state["batch_id"])
    path.unlink()
    path.symlink_to(outside / "recipe.json")
    with pytest.raises(ValueError, match="符号链接"):
        runner.stop_dish_info_batch(root, state["batch_id"])
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    (alternate / ".dish-info").symlink_to(root / ".dish-info", target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        runner.list_dish_info_batches(alternate)


def test_worker_revalidates_persisted_folder_before_generation(monkeypatch, tmp_path):
    root = tmp_path / "output"
    recipe(root)
    outside = recipe(tmp_path, "outside")
    state = prepare(monkeypatch, root)
    state["items"][0]["folder"] = "../outside"
    runner._write(root, state)
    original = original_bytes(outside)
    monkeypatch.setattr(runner, "generate_dish_info", lambda *a, **k: pytest.fail("escaped root"))
    final = run(root, state)
    assert final["status"] == "completed_with_errors"
    assert final["items"][0]["error_kind"] == "validation"
    assert original_bytes(outside) == original


@pytest.mark.parametrize("options", [{"provider": "none"}, {"provider": "local", "unexpected": True}])
def test_invalid_resume_configuration_does_not_rewrite_state(monkeypatch, tmp_path, options):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path)
    path = runner._path(tmp_path, state["batch_id"])
    previous = path.read_bytes()
    with pytest.raises(ValueError, match="LLM"):
        runner.resume_dish_info_batch(tmp_path, state["batch_id"], **options)
    assert path.read_bytes() == previous


def test_launch_failure_and_symlink_log_leave_retryable_task(monkeypatch, tmp_path):
    recipe(tmp_path)
    launch = runner._launch
    state = prepare(monkeypatch, tmp_path)
    outside = tmp_path / "unrelated.log"
    outside.write_bytes(b"keep")
    log = tmp_path / ".dish-info" / (state["batch_id"] + ".log")
    log.symlink_to(outside)
    with pytest.raises(ValueError, match="符号链接"):
        launch(tmp_path, state)
    failed = runner._read(tmp_path, state["batch_id"])
    assert failed["status"] == "failed" and not runner._active(failed)
    assert outside.read_bytes() == b"keep"
    log.unlink()
    def fail(*a, **k):
        raise OSError("cannot start worker")
    monkeypatch.setattr(runner.subprocess, "Popen", fail)
    with pytest.raises(OSError, match="cannot start"):
        launch(tmp_path, failed)
    failed = runner._read(tmp_path, state["batch_id"])
    assert failed["status"] == "failed" and failed["pending"] == 1
    assert runner.resume_dish_info_batch(tmp_path, state["batch_id"])["items"][0]["status"] == "pending"


def test_windows_liveness_never_signals_and_unix_zombie_is_inactive(monkeypatch):
    state = {"status": "running", "pid": 100, "batch_id": "intro-test", "process_start_id": "same"}
    monkeypatch.setattr(runner, "_identity", lambda pid: "same")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(runner, "_pid_is_alive", lambda pid: pytest.fail("must not signal on Windows"))
    assert runner._active(state)
    state["process_start_id"] = "recycled"
    assert not runner._active(state)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(runner, "_pid_is_alive", lambda pid: False)
    state["process_start_id"] = "same"
    assert not runner._active(state)


def test_real_subprocess_survives_ui_restart_and_rejects_duplicate_start(tmp_path):
    root = tmp_path / "outputs"
    recipe(root, "up/a")
    recipe(root, "up/b")
    fake = tmp_path / "fake provider.py"
    fake.write_text(
        "import json, sys, time\n"
        "prompt = sys.stdin.read()\n"
        "assert 'untrusted_material' in prompt\n"
        "time.sleep(0.15)\n"
        f"print({json.dumps(json.dumps(answer(), ensure_ascii=False))})\n", encoding="utf-8",
    )
    command = shlex.join([sys.executable, str(fake)])
    originals = original_bytes(root)
    first = runner.start_dish_info_batch(root, provider="local", local_llm_command=command)
    processes = list(runner._PROCESSES.values())
    try:
        second = runner.start_dish_info_batch(root, provider="local", local_llm_command=command)
        assert first["batch_id"] == second["batch_id"] and first["pid"] != os.getpid()
        runner._PROCESSES.clear()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            final = runner.list_dish_info_batches(root)[0]
            if final["status"] in {"completed", "completed_with_errors", "interrupted", "paused"}:
                break
            time.sleep(0.02)
        assert final["status"] == "completed", final
        assert final["completed"] == 2 and not final["active"]
        assert all(dish_info.load_dish_info(root / name) for name in ("up/a", "up/b"))
        assert {name: (root / name).read_bytes() for name in originals} == originals
        assert final["runs"][0]["llm_options"]["local_llm_command"] == command
    finally:
        for process in processes:
            process.wait(timeout=10)


@pytest.mark.parametrize("entrypoint", [cli.main, ui_launcher.main])
def test_frozen_entrypoint_dispatches_intro_worker(monkeypatch, entrypoint):
    seen = []
    monkeypatch.setattr(sys, "argv", ["app", "--dish-info-worker", "/out", "intro-test", "token"])
    monkeypatch.setattr(dish_info_worker, "main", lambda args: seen.append(args) or 0)
    assert entrypoint() == 0 and seen == [["/out", "intro-test", "token"]]


def test_frozen_launch_sets_absolute_data_root_and_worker_arguments(monkeypatch, tmp_path):
    recipe(tmp_path)
    launch = runner._launch
    state = prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    seen = {}
    def popen(command, **options):
        seen.update(command=command, options=options)
        return SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    launched = launch(tmp_path, state)
    assert seen["command"] == [sys.executable, "--dish-info-worker", str(tmp_path), state["batch_id"], launched["run_token"]]
    assert seen["options"]["env"]["BILI_RECIPE_DATA_DIR"] == str(tmp_path / "data")
    assert Path(seen["options"]["cwd"]).is_absolute()


def test_worker_reports_failure_and_bad_arguments(monkeypatch, tmp_path):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path)
    def failure(*args):
        raise RuntimeError("worker failed")
    monkeypatch.setattr(dish_info_worker, "run_dish_info_batch", failure)
    assert dish_info_worker.main([]) == 2
    assert dish_info_worker.main([str(tmp_path), state["batch_id"], state["run_token"]]) == 1
    assert runner._read(tmp_path, state["batch_id"])["status"] == "interrupted"
