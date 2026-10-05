from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bili_recipe_notes import cli, frame_selection_runner as runner, frame_selection_worker, ui_launcher
from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.review_policy import set_ai_review_visibility
from test_dish_info_runner import original_bytes, recipe


@pytest.fixture(autouse=True)
def isolated_workspace(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(runner, "_PROCESSES", {})
    monkeypatch.setattr(runner, "load_frame_candidate", lambda *args: None)
    monkeypatch.setattr(runner, "generate_frame_candidate", lambda *args, **kwargs: pytest.fail("unexpected model call"))
    preflight = runner._preflight_options
    monkeypatch.setattr(runner, "_preflight_options", lambda options: options)
    return preflight


def prepare(monkeypatch, root, **options):
    def launch(out_dir, state):
        token = f"test-{len(state.get('runs', [])) + 1}"
        state.update(status="paused", run_token=token, pid=None, process_start_id=None, stop_requested=False,
                     pause_reason=None, error=None, consecutive_failures=0)
        state.setdefault("runs", []).append({"run_token": token, "started_at": runner._now(), "options": dict(state["options"])})
        return runner._write(out_dir, state)
    monkeypatch.setattr(runner, "_launch", launch)
    return runner.start_frame_selection_batch(root, **options)


def run(root, state):
    return runner.run_frame_selection_batch(root, state["batch_id"], state["run_token"])


def confirmed(folder):
    value = json.loads((folder / "recipe.json").read_text())
    value["cover_image_status"] = "uploaded"
    (folder / "recipe.json").write_text(json.dumps(value))


def test_selection_keeps_visibility_publication_and_manual_decisions(monkeypatch, tmp_path):
    root = tmp_path / "outputs"
    for name in ("up/a", "up/b", "up/c", "up/d", "up/e"):
        recipe(root, name)
    store = MobileSyncStore(tmp_path, out_dir=root)
    store.index_recipes()
    unlisted = json.loads((root / "up/b/sync-meta.json").read_text())["recipe_id"]
    store.set_recipe_publications({unlisted: False})
    set_ai_review_visibility(root / "up/c", "hidden")
    confirmed(root / "up/d")
    originals = original_bytes(root)
    state = prepare(monkeypatch, root, limit=1, folders=["up/b", "up/c", "up/d", "up/a", "up/e"])
    assert [item["folder"] for item in state["items"]] == ["up/a"]
    assert original_bytes(root) == originals
    forced = runner.start_frame_selection_batch(root, only_pending=False, published_only=False, skip_confirmed=False,
                                                folders=["up/b", "up/c", "up/d"])
    assert [item["folder"] for item in forced["items"]] == ["up/b", "up/d"]
    assert original_bytes(root) == originals


@pytest.mark.parametrize("status", ["ok", "not_found"])
def test_valid_candidate_and_not_found_skipped_but_force_can_regenerate(monkeypatch, tmp_path, status):
    recipe(tmp_path)
    monkeypatch.setattr(runner, "load_frame_candidate", lambda *args: {"status": status, "options": runner._normalize_options({})})
    state = prepare(monkeypatch, tmp_path)
    assert state["total"] == 0 and state["status"] == "completed"
    forced = runner.start_frame_selection_batch(tmp_path, only_pending=False)
    assert forced["total"] == 1


@pytest.mark.parametrize("updates,expected", [
    ({"requirement": "只选装盘后的近景"}, 1), ({"coarse_limit": 48}, 1), ({"fine_limit": 12}, 1),
    ({"model": "/another/model"}, 1), ({"python_executable": "/another/python"}, 1),
    ({"cookies": "/another/cookies", "keep_video": True}, 0),
])
def test_pending_selection_accounts_for_changed_model_and_sampling_options(monkeypatch, tmp_path, updates, expected):
    recipe(tmp_path)
    previous = runner._normalize_options({})
    monkeypatch.setattr(runner, "load_frame_candidate", lambda *args: {"status": "not_found", "options": previous})
    state = prepare(monkeypatch, tmp_path, **updates)
    assert state["total"] == expected


def test_candidate_without_recorded_options_is_eligible_for_refresh(monkeypatch, tmp_path):
    recipe(tmp_path)
    monkeypatch.setattr(runner, "load_frame_candidate", lambda *args: {"status": "ok"})
    assert prepare(monkeypatch, tmp_path)["total"] == 1


def test_invalid_environment_is_detected_before_creating_any_task(monkeypatch, tmp_path, isolated_workspace):
    from bili_recipe_notes import frame_selection
    from bili_recipe_notes.frame_selection_service import FrameSelectionConfigurationError

    recipe(tmp_path)
    tool = tmp_path / "tool"
    tool.mkdir()
    (tool / "select_frame.py").write_text("raise AssertionError('must never start')")
    monkeypatch.setattr(frame_selection, "TOOL_ROOT", tool)
    monkeypatch.setattr(runner, "_preflight_options", isolated_workspace)
    with pytest.raises(FrameSelectionConfigurationError):
        runner.start_frame_selection_batch(tmp_path, python_executable=sys.executable, model=tmp_path / "missing model")
    assert not list(runner._tasks(tmp_path).glob("frame-*.json"))


def test_runtime_rechecks_hidden_manual_and_new_valid_candidate(monkeypatch, tmp_path):
    for name in ("a", "b", "c"):
        recipe(tmp_path, name)
    state = prepare(monkeypatch, tmp_path)
    set_ai_review_visibility(tmp_path / "a", "hidden")
    confirmed(tmp_path / "b")
    monkeypatch.setattr(runner, "load_frame_candidate", lambda _, folder: {"status": "not_found", "options": state["options"]} if folder.name == "c" else None)
    final = run(tmp_path, state)
    assert final["status"] == "completed" and final["skipped"] == 3
    assert [item["skip_reason"] for item in final["items"]] == ["not_visible_or_published", "manually_confirmed", "valid_candidate"]


def test_progress_history_result_counts_and_original_bytes(monkeypatch, tmp_path):
    for name in ("a", "b"):
        recipe(tmp_path, name)
    originals = original_bytes(tmp_path)
    state = prepare(monkeypatch, tmp_path, threads=2, coarse_limit=3)
    def generate(folder, *, out_dir, options, progress):
        progress({"phase": "loading_model", "completed_frames": 0, "total_frames": 3})
        progress({"phase": "coarse", "completed_frames": 1, "total_frames": 3, "current_timestamp": 1.25})
        persisted = runner._read(out_dir, state["batch_id"])
        assert persisted["current_progress"]["current_timestamp"] == 1.25
        assert persisted["current_folder"] == folder.name
        return {"status": "ok" if folder.name == "a" else "not_found", "timestamp": 1.25 if folder.name == "a" else None,
                "reason": "成品画面" if folder.name == "a" else "无符合要求的候选"}
    monkeypatch.setattr(runner, "generate_frame_candidate", generate)
    final = run(tmp_path, state)
    assert final["status"] == "completed"
    assert (final["total"], final["completed"], final["not_found"], final["failed"], final["pending"]) == (2, 2, 1, 0, 0)
    assert final["current_progress"] is None
    assert [item["status"] for item in final["items"]] == ["completed", "not_found"]
    history = final["items"][0]["attempts"][0]
    assert [event["phase"] for event in history["progress"]] == ["loading_model", "coarse"]
    assert history["options"]["threads"] == 2 and history["finished_at"]
    assert original_bytes(tmp_path) == originals


def test_transient_retry_bound_and_options_are_recorded(monkeypatch, tmp_path):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path, threads=2)
    calls, delays = [], []
    def generate(folder, **kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise TimeoutError("network timed out")
        return {"status": "ok"}
    monkeypatch.setattr(runner, "generate_frame_candidate", generate)
    monkeypatch.setattr(runner.time, "sleep", delays.append)
    final = run(tmp_path, state)
    history = final["items"][0]["attempts"]
    assert final["status"] == "completed" and delays == [1, 2]
    assert [attempt["status"] for attempt in history] == ["failed", "failed", "completed"]
    assert history[0]["error_kind"] == "transient" and history[1]["retry_delay_seconds"] == 2
    assert history[-1]["options"]["threads"] == 2


def test_configuration_failure_pauses_then_current_options_fix_it(monkeypatch, tmp_path):
    recipe(tmp_path, "a")
    recipe(tmp_path, "b")
    state = prepare(monkeypatch, tmp_path, python_executable="missing-python")
    calls = []
    def generate(folder, **kwargs):
        calls.append(folder.name)
        if kwargs["options"]["python_executable"] == "missing-python":
            raise FileNotFoundError("找不到可执行的选图 Python：missing-python")
        return {"status": "ok"}
    monkeypatch.setattr(runner, "generate_frame_candidate", generate)
    paused = run(tmp_path, state)
    assert paused["status"] == "paused" and paused["pause_reason"] == "configuration" and len(calls) == 1
    resumed = runner.resume_frame_selection_batch(tmp_path, state["batch_id"], retry_failed=True, python_executable="fixed-python")
    final = run(tmp_path, resumed)
    assert final["completed"] == 2 and calls == ["a", "a", "b"]
    assert final["items"][0]["attempts"][0]["options"]["python_executable"] == "missing-python"
    assert final["items"][0]["attempts"][1]["options"]["python_executable"] == "fixed-python"


def test_failure_continues_and_manual_pause_finishes_current_item(monkeypatch, tmp_path):
    for name in ("a", "b", "c"):
        recipe(tmp_path, name)
    state = prepare(monkeypatch, tmp_path)
    calls = []
    def generate(folder, **kwargs):
        calls.append(folder.name)
        if folder.name == "a":
            raise ValueError("invalid result")
        if folder.name == "b":
            runner.stop_frame_selection_batch(tmp_path, state["batch_id"])
            assert runner._read(tmp_path, state["batch_id"])["items"][1]["status"] == "running"
        return {"status": "not_found"}
    monkeypatch.setattr(runner, "generate_frame_candidate", generate)
    paused = run(tmp_path, state)
    assert paused["status"] == "paused" and paused["pause_reason"] == "requested"
    assert (paused["failed"], paused["completed"], paused["not_found"], paused["pending"]) == (1, 1, 1, 1)
    final = run(tmp_path, runner.resume_frame_selection_batch(tmp_path, state["batch_id"]))
    assert final["status"] == "completed_with_errors" and calls == ["a", "b", "c"]
    monkeypatch.setattr(runner, "generate_frame_candidate", lambda folder, **k: calls.append(folder.name) or {"status": "ok"})
    final = run(tmp_path, runner.resume_frame_selection_batch(tmp_path, state["batch_id"], retry_failed=True))
    assert final["status"] == "completed" and calls == ["a", "b", "c", "a"]
    assert final["completed"] == 3 and final["not_found"] == 2


def test_pause_during_transient_failure_does_not_start_another_attempt(monkeypatch, tmp_path):
    recipe(tmp_path, "a")
    recipe(tmp_path, "b")
    state = prepare(monkeypatch, tmp_path)
    def generate(*args, **kwargs):
        runner.stop_frame_selection_batch(tmp_path, state["batch_id"])
        raise TimeoutError("timeout")
    monkeypatch.setattr(runner, "generate_frame_candidate", generate)
    monkeypatch.setattr(runner.time, "sleep", lambda _: pytest.fail("must pause promptly"))
    final = run(tmp_path, state)
    assert final["status"] == "paused" and final["pending"] == 1
    assert len(final["items"][0]["attempts"]) == 1


def test_pause_during_retry_delay_does_not_restart_expensive_model(monkeypatch, tmp_path):
    recipe(tmp_path, "a")
    recipe(tmp_path, "b")
    state = prepare(monkeypatch, tmp_path)
    calls = []
    def generate(folder, **kwargs):
        calls.append(folder.name)
        raise TimeoutError("timed out")
    monkeypatch.setattr(runner, "generate_frame_candidate", generate)
    monkeypatch.setattr(runner.time, "sleep", lambda _: runner.stop_frame_selection_batch(tmp_path, state["batch_id"]))
    final = run(tmp_path, state)
    assert final["status"] == "paused" and calls == ["a"]
    assert len(final["items"][0]["attempts"]) == 1


def test_three_attempt_limit_and_five_consecutive_failures_pause(monkeypatch, tmp_path):
    for index in range(6):
        recipe(tmp_path, str(index))
    state = prepare(monkeypatch, tmp_path)
    def generate(*args, **kwargs):
        raise TimeoutError("connection timed out")
    monkeypatch.setattr(runner, "generate_frame_candidate", generate)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    final = run(tmp_path, state)
    assert final["status"] == "paused" and final["pause_reason"] == "consecutive_failures"
    assert (final["failed"], final["pending"]) == (5, 1)
    assert all(len(item["attempts"]) == 3 for item in final["items"][:5])


def test_crashed_worker_resumes_running_item_without_repeating_complete(monkeypatch, tmp_path):
    recipe(tmp_path, "a")
    recipe(tmp_path, "b")
    state = prepare(monkeypatch, tmp_path)
    state.update(status="running", pid=999999999, process_start_id="gone")
    state["items"][0]["status"] = "not_found"
    state["items"][1].update(status="running", attempts=[{"status": "running", "run_token": state["run_token"]}])
    runner._write(tmp_path, state)
    found = runner.list_frame_selection_batches(tmp_path)[0]
    assert found["status"] == "interrupted" and not found["active"]
    resumed = runner.resume_frame_selection_batch(tmp_path, state["batch_id"])
    assert resumed["run_token"] != state["run_token"]
    assert resumed["items"][1]["attempts"][0]["status"] == "interrupted"
    calls = []
    monkeypatch.setattr(runner, "generate_frame_candidate", lambda folder, **k: calls.append(folder.name) or {"status": "ok"})
    final = run(tmp_path, resumed)
    assert final["completed"] == 2 and final["not_found"] == 1 and calls == ["b"]


def test_stale_worker_cannot_write_progress_or_failure_to_new_task(monkeypatch, tmp_path):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path)
    resumed = runner.resume_frame_selection_batch(tmp_path, state["batch_id"])
    path = runner._path(tmp_path, state["batch_id"])
    before = path.read_bytes()
    assert frame_selection_worker.main([str(tmp_path), state["batch_id"], state["run_token"]]) == 1
    assert path.read_bytes() == before
    def generate(folder, **kwargs):
        runner._update(tmp_path, state["batch_id"], lambda current: current.update(run_token="new-owner"))
        kwargs["progress"]({"phase": "coarse", "completed_frames": 1})
        pytest.fail("stale progress must terminate selector")
    monkeypatch.setattr(runner, "generate_frame_candidate", generate)
    assert frame_selection_worker.main([str(tmp_path), state["batch_id"], resumed["run_token"]]) == 1
    final = runner._read(tmp_path, state["batch_id"])
    assert final["run_token"] == "new-owner" and final["status"] == "running"
    assert final["items"][0]["attempts"][0]["progress"] == []


def test_same_token_second_worker_cannot_duplicate_active_task(monkeypatch, tmp_path):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path)
    state.update(status="running", pid=os.getpid(), process_start_id=runner._identity(os.getpid()))
    runner._write(tmp_path, state)
    path = runner._path(tmp_path, state["batch_id"])
    before = path.read_bytes()
    assert run(tmp_path, state)["status"] == "running"
    assert path.read_bytes() == before


@pytest.mark.parametrize("folder", ["../outside", ".private/recipe", ".", "/outside"])
def test_path_escape_rejected_before_launch(monkeypatch, tmp_path, folder):
    root = tmp_path / "outputs"
    recipe(root)
    recipe(tmp_path, "outside")
    prepare(monkeypatch, root)
    with pytest.raises(ValueError, match="输出目录内"):
        runner.start_frame_selection_batch(root, folders=[folder])


def test_symlink_directories_and_invalid_ids_are_rejected(monkeypatch, tmp_path):
    root = tmp_path / "outputs"
    recipe(root)
    outside = recipe(tmp_path, "outside")
    state = prepare(monkeypatch, root)
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        runner.start_frame_selection_batch(root, folders=[root / "linked"])
    for batch_id in (None, 123, "../../outside"):
        with pytest.raises(ValueError, match="任务 ID"):
            runner.stop_frame_selection_batch(root, batch_id)
    path = runner._path(root, state["batch_id"])
    path.unlink()
    path.symlink_to(outside / "recipe.json")
    with pytest.raises(ValueError, match="符号链接"):
        runner.stop_frame_selection_batch(root, state["batch_id"])
    other = tmp_path / "other"
    other.mkdir()
    (other / ".frame-selection").mkdir()
    (other / ".frame-selection/tasks").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="符号链接"):
        runner.list_frame_selection_batches(other)


def test_worker_revalidates_persisted_paths(monkeypatch, tmp_path):
    root = tmp_path / "outputs"
    recipe(root)
    outside = recipe(tmp_path, "outside")
    original = original_bytes(outside)
    state = prepare(monkeypatch, root)
    state["items"][0]["folder"] = "../outside"
    runner._write(root, state)
    final = run(root, state)
    assert final["status"] == "completed_with_errors" and final["items"][0]["error_kind"] == "validation"
    assert original_bytes(outside) == original


@pytest.mark.parametrize("options", [{"unknown": True}, {"threads": 0}, {"coarse_limit": 1}, {"interval": float("nan")},
                                    {"fine_fps": False}, {"device": "other"}, {"keep_video": 1}, {"requirement": ""}])
def test_invalid_resume_config_preserves_persisted_state(monkeypatch, tmp_path, options):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path)
    path = runner._path(tmp_path, state["batch_id"])
    before = path.read_bytes()
    with pytest.raises(ValueError):
        runner.resume_frame_selection_batch(tmp_path, state["batch_id"], **options)
    assert path.read_bytes() == before


def test_relative_model_cookie_and_python_paths_remain_correct_in_child(monkeypatch, tmp_path):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path, model="models/demo", cookies=Path("cookies.txt"), python_executable="bin/python")
    assert {key: state["options"][key] for key in ("model", "cookies", "python_executable")} == {
        "model": str(tmp_path / "models/demo"), "cookies": str(tmp_path / "cookies.txt"), "python_executable": str(tmp_path / "bin/python")}


def test_launch_failure_and_symlink_log_are_retryable(monkeypatch, tmp_path):
    recipe(tmp_path)
    launch = runner._launch
    state = prepare(monkeypatch, tmp_path)
    outside = tmp_path / "unrelated.log"
    outside.write_bytes(b"keep")
    log = runner._tasks(tmp_path) / (state["batch_id"] + ".log")
    log.symlink_to(outside)
    with pytest.raises(ValueError, match="符号链接"):
        launch(tmp_path, state)
    failed = runner._read(tmp_path, state["batch_id"])
    assert failed["status"] == "failed" and not runner._active(failed)
    assert outside.read_bytes() == b"keep"
    log.unlink()
    def unavailable(*args, **kwargs):
        raise OSError("cannot start worker")
    monkeypatch.setattr(runner.subprocess, "Popen", unavailable)
    with pytest.raises(OSError, match="cannot start"):
        launch(tmp_path, failed)
    assert runner._read(tmp_path, state["batch_id"])["status"] == "failed"
    assert runner.resume_frame_selection_batch(tmp_path, state["batch_id"])["pending"] == 1


def test_windows_pid_check_never_signals_and_unix_zombie_is_not_active(monkeypatch):
    state = {"status": "running", "batch_id": "test", "pid": 100, "process_start_id": "same"}
    monkeypatch.setattr(runner, "_identity", lambda _: "same")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(runner, "_pid_is_alive", lambda _: pytest.fail("do not signal on Windows"))
    assert runner._active(state)
    state["process_start_id"] = "other"
    assert not runner._active(state)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(runner, "_pid_is_alive", lambda _: False)
    state["process_start_id"] = "same"
    assert not runner._active(state)


@pytest.mark.parametrize("entrypoint", [cli.main, ui_launcher.main])
def test_frozen_entrypoints_dispatch_worker(monkeypatch, entrypoint):
    seen = []
    monkeypatch.setattr(sys, "argv", ["app", "--frame-selection-worker", "/out", "frame-test", "token"])
    monkeypatch.setattr(frame_selection_worker, "main", lambda args: seen.append(args) or 0)
    assert entrypoint() == 0 and seen == [["/out", "frame-test", "token"]]


@pytest.mark.parametrize("entrypoint", [cli.main, ui_launcher.main])
def test_frozen_entrypoints_dispatch_supervisor(monkeypatch, entrypoint):
    from bili_recipe_notes import frame_selection_service

    seen = []
    monkeypatch.setattr(sys, "argv", ["app", "--frame-selection-supervisor", "/command.json", "/model.lock"])
    monkeypatch.setattr(frame_selection_service, "supervisor_main", lambda args: seen.append(args) or 0)
    assert entrypoint() == 0 and seen == [["/command.json", "/model.lock"]]


def test_frozen_launch_persists_pid_and_absolute_data_root(monkeypatch, tmp_path):
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
    assert seen["command"] == [sys.executable, "--frame-selection-worker", str(tmp_path), state["batch_id"], launched["run_token"]]
    assert seen["options"]["env"]["BILI_RECIPE_DATA_DIR"] == str(tmp_path / "data")
    assert launched["pid"] == os.getpid() and launched["active"]


def test_worker_handles_bad_arguments_and_marks_matching_run_interrupted(monkeypatch, tmp_path):
    recipe(tmp_path)
    state = prepare(monkeypatch, tmp_path)
    def failure(*args):
        raise RuntimeError("worker failure")
    monkeypatch.setattr(frame_selection_worker, "run_frame_selection_batch", failure)
    assert frame_selection_worker.main([]) == 2
    assert frame_selection_worker.main([str(tmp_path), state["batch_id"], state["run_token"]]) == 1
    final = runner._read(tmp_path, state["batch_id"])
    assert final["status"] == "interrupted" and final["error"] == "worker failure"


def fake_selector(monkeypatch, tmp_path, *, delayed_first=False):
    """Isolate only the optional external selector; launch real app workers."""
    tool = tmp_path / "fake tool"
    tool.mkdir()
    from bili_recipe_notes import frame_selection
    monkeypatch.setattr(frame_selection, "TOOL_ROOT", tool)
    bootstrap = tmp_path / "bootstrap"
    bootstrap.mkdir()
    (bootstrap / "sitecustomize.py").write_text(
        "from pathlib import Path\nfrom bili_recipe_notes import frame_selection\n"
        f"frame_selection.TOOL_ROOT = Path({str(tool)!r})\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(bootstrap))
    model = tmp_path / "fake model"
    model.mkdir()
    log = tmp_path / "selector-events.jsonl"
    (tool / "select_frame.py").write_text(
        "import argparse, json, os, sys, time\n"
        "from pathlib import Path\n"
        "from PIL import Image\n"
        "parser = argparse.ArgumentParser()\n"
        "for key in ('input', 'output', 'requirement', 'model'):\n"
        "    parser.add_argument('--' + key)\n"
        "args, _ = parser.parse_known_args()\n"
        f"events = Path({str(log)!r})\n"
        "first = not events.exists()\n"
        "with events.open('a') as file:\n"
        "    file.write(json.dumps({'event':'start','pid':os.getpid(),'at':time.monotonic()}) + '\\n')\n"
        "print('加载本地模型', file=sys.stderr, flush=True)\n"
        "print('粗选 2 帧', file=sys.stderr, flush=True)\n"
        "print('frame=0 time=0.000s match=True', file=sys.stderr, flush=True)\n"
        f"time.sleep(10 if first and {delayed_first!r} else 0.2)\n"
        "print('frame=1 time=1.250s match=True', file=sys.stderr, flush=True)\n"
        "print('精选 0 帧', file=sys.stderr, flush=True)\n"
        "out = Path(args.output)\n"
        "out.mkdir(parents=True)\n"
        "image = out / 'best_frame.png'\n"
        "Image.new('RGB', (12,8), 'red').save(image)\n"
        "result = {'schema_version':1,'status':'ok','fallback':False,'input':args.input,\n"
        "          'requirement':args.requirement,'image':str(image),'dimensions':[12,8],\n"
        "          'selected':{'frame_index':25,'pts':1250,'time_base':'1/1000',\n"
        "                      'timestamp_seconds':1.25,'relative_timestamp_seconds':1.25,\n"
        "                      'judgment':{'matches_requirement':True,'reason':'synthetic frame'}},\n"
        "          'reason':'synthetic frame',\n"
        "          'metadata':str(out/'result.json')}\n"
        "(out/'result.json').write_text(json.dumps(result))\n"
        "print(json.dumps(result),flush=True)\n"
        "with events.open('a') as file:\n"
        "    file.write(json.dumps({'event':'done','pid':os.getpid(),'at':time.monotonic()}) + '\\n')\n",
        encoding="utf-8")
    return {"python_executable": sys.executable, "model": str(model), "threads": 2,
            "coarse_limit": 2, "fine_limit": 1}, log


def wait_for(root, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = runner.list_frame_selection_batches(root)[0]
        if predicate(state):
            return state
        time.sleep(0.025)
    raise AssertionError(f"worker timed out: {state}")


def with_local_video(root, name):
    folder = recipe(root, name)
    media = folder / "media"
    media.mkdir()
    (media / "cover-video.mp4").write_bytes(b"synthetic selector never decodes this fixture")
    return folder


def test_real_worker_and_fake_selector_survive_ui_restart_and_reject_duplicates(monkeypatch, tmp_path, isolated_workspace):
    from bili_recipe_notes.frame_selection_service import load_frame_candidate

    options, events = fake_selector(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "_preflight_options", isolated_workspace)
    root = tmp_path / "outputs"
    with_local_video(root, "up/a")
    with_local_video(root, "up/b")
    originals = original_bytes(root)
    first = runner.start_frame_selection_batch(root, **options)
    processes = list(runner._PROCESSES.values())
    try:
        second = runner.start_frame_selection_batch(root, **options)
        assert second["batch_id"] == first["batch_id"] and first["pid"] != os.getpid()
        assert second["already_running"] is True
        assert "already_running" not in runner._read(root, first["batch_id"])
        runner._PROCESSES.clear()
        final = wait_for(root, lambda state: state["status"] not in {"queued", "running"})
        assert final["status"] == "completed", final
        assert final["completed"] == 2 and not final["active"]
        candidate = load_frame_candidate(root, root / "up/a")
        assert candidate["status"] == "ok" and candidate["dimensions"] == [12, 8]
        history = final["items"][0]["attempts"][0]["progress"]
        assert any(item["phase"] == "coarse" and item["frames_completed"] == 1 for item in history)
        assert {name: (root / name).read_bytes() for name in originals} == originals
        assert [json.loads(line)["event"] for line in events.read_text().splitlines()] == ["start", "done", "start", "done"]
    finally:
        for process in processes:
            process.wait(timeout=15)


@pytest.mark.skipif(sys.platform == "win32", reason="terminate/EOF process identity test uses POSIX process semantics")
def test_real_interrupted_worker_cleans_model_and_resumes_safely(monkeypatch, tmp_path, isolated_workspace):
    options, events = fake_selector(monkeypatch, tmp_path, delayed_first=True)
    monkeypatch.setattr(runner, "_preflight_options", isolated_workspace)
    root = tmp_path / "outputs"
    with_local_video(root, "one")
    originals = original_bytes(root)
    first = runner.start_frame_selection_batch(root, **options)
    process = runner._PROCESSES[first["batch_id"]]
    resumed_process = None
    try:
        wait_for(root, lambda state: state.get("current_progress", {}).get("phase") == "coarse" if state.get("current_progress") else False)
        old_model = json.loads(events.read_text().splitlines()[0])["pid"]
        process.terminate()
        process.wait(timeout=5)
        interrupted = runner.list_frame_selection_batches(root)[0]
        assert interrupted["status"] == "interrupted"
        resumed = runner.resume_frame_selection_batch(root, first["batch_id"])
        resumed_process = runner._PROCESSES[first["batch_id"]]
        assert resumed["run_token"] != first["run_token"]
        final = wait_for(root, lambda state: state["status"] not in {"queued", "running"})
        assert final["status"] == "completed", final
        assert not runner._pid_is_alive(old_model)
        history = final["items"][0]["attempts"]
        assert [attempt["status"] for attempt in history] == ["interrupted", "completed"]
        assert [json.loads(line)["event"] for line in events.read_text().splitlines()] == ["start", "start", "done"]
        assert {name: (root / name).read_bytes() for name in originals} == originals
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)
        if resumed_process is not None:
            resumed_process.wait(timeout=15)
