from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from bili_recipe_notes import admin_tasks as tasks, admin_creator_worker as creator
from bili_recipe_notes.admin_common import AdminContext, require_admin_request
from bili_recipe_notes.batch_queue import create_batch_state, load_batch_state, save_batch_state
from bili_recipe_notes.config import UIConfig, save_config
from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.review_policy import set_ai_review_visibility
from bili_recipe_notes.storage import atomic_write_json
from test_dish_info_runner import recipe, original_bytes


@pytest.fixture
def setup(tmp_path, monkeypatch, authorize_client):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "recipes"
    first = recipe(root, "up/a")
    second = recipe(root, "up/b")
    config = UIConfig(out_dir=str(root), llm_provider="local", local_llm_command="unused fake model", cookies="private-cookie.txt")
    save_config(config, tmp_path)
    store = MobileSyncStore(tmp_path, out_dir=root)
    store.index_recipes()
    ctx = AdminContext(store, SimpleNamespace())
    app = FastAPI()
    app.include_router(tasks.create_router(ctx), prefix="/api/admin/v1", dependencies=[Depends(require_admin_request)])
    ids = {Path(row["output_folder"]).name: row["id"] for row in ctx.rows()}
    monkeypatch.setattr(tasks.generation, "start_background_batch", lambda *args, **kwargs: pytest.fail("unexpected real generation launch"))
    monkeypatch.setattr(tasks.reviews, "_launch", lambda *args: pytest.fail("unexpected real review launch"))
    monkeypatch.setattr(tasks.frames, "_launch", lambda *args: pytest.fail("unexpected real frame launch"))
    monkeypatch.setattr(tasks.introductions, "_launch", lambda *args: pytest.fail("unexpected real introduction launch"))
    return SimpleNamespace(client=authorize_client(TestClient(app), tmp_path), root=root, ctx=ctx, ids=ids, first=first, second=second, project=tmp_path)


def test_video_links_are_deduplicated_and_multi_part_identity_is_retained():
    assert tasks.canonical_video_urls([
        "# a creator list\nBV13p411d7oQ", "https://www.bilibili.com/video/BV13p411d7oQ/?spm_id=test&p=1",
        "http://m.bilibili.com/video/BV13p411d7oQ?p=2", "https://b23.tv/AbCd1?from=share",
    ]) == ["https://www.bilibili.com/video/BV13p411d7oQ", "https://www.bilibili.com/video/BV13p411d7oQ?p=2", "https://b23.tv/AbCd1"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://example.com/video/BV13p411d7oQ", "https://www.bilibili.com.evil/video/BV13p411d7oQ", "https://space.bilibili.com/42", "https://user@www.bilibili.com/video/BV13p411d7oQ", "https://www.bilibili.com/video/BV13p411d7oQ?p=no"])
def test_invalid_video_links_do_not_create_any_job(setup, url):
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": [url], "create_only": True})
    assert response.status_code == 400
    assert not list((setup.project / "data/state/batches").glob("*.json"))


def test_generation_can_be_created_without_running_then_resumed_and_upgraded(setup, monkeypatch):
    original = original_bytes(setup.root)
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"] * 2,
                                  "target_stage": "raw", "create_only": True, "creator_name": "王刚"})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["total"] == 1 and value["status"] == "pending" and value["can_resume"]
    assert "cookies" not in value["options"]
    state_path = setup.project / "data/state/batches" / f"{value['id']}.json"
    assert "private-cookie" not in state_path.read_text()
    calls = []
    monkeypatch.setattr(tasks.generation, "start_background_batch", lambda options, **kwargs: calls.append(asdict(options)))
    resumed = setup.client.post(f"/api/admin/v1/tasks/generation/{value['id']}/resume", json={"use_current_config": True})
    assert resumed.status_code == 200
    assert calls[-1]["target_stage"] == "raw" and calls[-1]["creator_name"] == "王刚"
    assert calls[-1]["cookies"] == "private-cookie.txt"
    assert calls[-1]["out"] == str(setup.root)
    advanced = setup.client.post(f"/api/admin/v1/tasks/generation/{value['id']}/resume", json={"target_stage": "recipe"})
    assert advanced.status_code == 200
    assert calls[-1]["target_stage"] == "recipe"
    assert advanced.json()["target_stage"] == "recipe"
    assert original_bytes(setup.root) == original


def test_generation_rejects_path_override_and_invalid_parameters(setup):
    for options in ({"out": "/tmp/other"}, {"source_database_path": "/tmp/db"}, {"max_step_images": 20}, {"keep_media": "false"}):
        response = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"], "options": options})
        assert response.status_code == 400


def test_generation_reads_latest_worker_stage_and_original_config(setup, monkeypatch):
    state = create_batch_state(["https://www.bilibili.com/video/BV13p411d7oQ"],
                               {"out": str(setup.root), "target_stage": "raw", "llm_provider": "codex"}, project_root=setup.project)
    state.items[0].status = "raw_ready"
    state.items[0].stages["raw"].status = "done"
    save_batch_state(state, setup.project)
    request = tasks.generation.background_request_path(state.batch_id, setup.project)
    atomic_write_json(request, {"options": {"batch_id": state.batch_id, "out": str(setup.root), "target_stage": "recipe", "llm_provider": "local", "local_llm_command": "original model"}})
    response = setup.client.get(f"/api/admin/v1/tasks/generation/{state.batch_id}")
    assert response.json()["target_stage"] == "recipe"
    assert response.json()["processed"] == 0 and response.json()["can_resume"]
    calls = []
    monkeypatch.setattr(tasks.generation, "start_background_batch", lambda options, **kwargs: calls.append(options))
    setup.client.post(f"/api/admin/v1/tasks/generation/{state.batch_id}/resume", json={})
    assert calls[-1].local_llm_command == "original model"


def test_generation_other_library_not_listed_or_controllable(setup):
    state = create_batch_state(["BV13p411d7oQ"], {"out": str(setup.project / "other")}, project_root=setup.project)
    assert setup.client.get("/api/admin/v1/tasks").json()["items"] == []
    assert setup.client.post(f"/api/admin/v1/tasks/generation/{state.batch_id}/resume", json={}).status_code == 404


@pytest.mark.parametrize("status", ["running", "paused"])
def test_generation_blocks_overlapping_active_urls_but_allows_create_only(setup, monkeypatch, status):
    other = create_batch_state(["http://m.bilibili.com/video/BV13p411d7oQ/?spm=legacy&p=1"],
                               {"out": str(setup.root)}, project_root=setup.project)
    runtime = tasks.generation.BackgroundBatchStatus(other.batch_id, status, "2026-10-05T00:00:00+00:00", pid=123)
    monkeypatch.setattr(tasks.generation, "get_background_batch_status", lambda batch_id, *args: runtime if batch_id == other.batch_id else None)
    blocked = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"]})
    assert blocked.status_code == 409 and other.batch_id in blocked.text
    assert len(list((setup.project / "data/state/batches").glob("*.json"))) == 1
    queued = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"], "create_only": True})
    assert queued.status_code == 200
    pending_id = queued.json()["id"]
    assert setup.client.post(f"/api/admin/v1/tasks/generation/{pending_id}/resume", json={}).status_code == 409
    state = load_batch_state(pending_id, setup.project)
    state.items[0].status = "failed"
    state.items[0].stages["raw"].status = "failed"
    save_batch_state(state, setup.project)
    before = (setup.project / f"data/state/batches/{pending_id}.json").read_bytes()
    assert setup.client.post(f"/api/admin/v1/tasks/generation/{pending_id}/retry", json={}).status_code == 409
    assert (setup.project / f"data/state/batches/{pending_id}.json").read_bytes() == before


def test_generation_overlap_excludes_self_other_library_and_completed_jobs(setup, monkeypatch):
    outside = create_batch_state(["BV13p411d7oQ"], {"out": str(setup.project / "other")}, project_root=setup.project)
    old = create_batch_state(["BV13p411d7oQ"], {"out": str(setup.root)}, project_root=setup.project)
    mine = create_batch_state(["BV13p411d7oQ"], {"out": str(setup.root)}, project_root=setup.project)
    runtimes = {outside.batch_id: "running", old.batch_id: "done", mine.batch_id: "paused"}
    monkeypatch.setattr(tasks.generation, "get_background_batch_status", lambda batch_id, *args:
                        tasks.generation.BackgroundBatchStatus(batch_id, runtimes[batch_id], "2026-10-05T00:00:00+00:00", pid=123))
    calls = []
    monkeypatch.setattr(tasks.generation, "resume_background_batch", lambda *args: calls.append(args))
    assert setup.client.post(f"/api/admin/v1/tasks/generation/{mine.batch_id}/resume", json={}).status_code == 200
    assert calls[0][0] == mine.batch_id


def test_generation_native_lock_is_held_through_launch_across_processes(setup, monkeypatch):
    import os
    import subprocess
    import sys
    source = """from pathlib import Path
from types import SimpleNamespace
import sys
from fastapi import HTTPException
from bili_recipe_notes.admin_tasks import _generation_control
ctx = SimpleNamespace(store=SimpleNamespace(project_root=Path(sys.argv[1])))
try:
    with _generation_control(ctx):
        print('acquired')
except HTTPException as exc:
    print(exc.status_code)
"""
    environment = {**os.environ, "PYTHONPATH": str(Path(tasks.__file__).resolve().parent.parent)}
    def acquire_in_child():
        return subprocess.run([sys.executable, "-c", source, str(setup.project)], env=environment,
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    during = []
    monkeypatch.setattr(tasks.generation, "start_background_batch", lambda *args, **kwargs: during.append(acquire_in_child()))
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"]})
    assert response.status_code == 200, response.text
    assert during == ["409"]
    assert acquire_in_child() == "acquired"


def test_generation_respects_disabled_llm_setting(setup, monkeypatch):
    config = setup.ctx.config()
    config.enable_llm_summary = False
    save_config(config, setup.project)
    calls = []
    monkeypatch.setattr(tasks.generation, "start_background_batch", lambda options, **kwargs: calls.append(options))
    assert setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"]}).status_code == 200
    assert calls[-1].no_llm_summary is True
    config.enable_llm_summary, config.llm_provider = True, "none"
    save_config(config, setup.project)
    assert tasks._generation_options(setup.ctx).no_llm_summary is True


def completed_generation(setup):
    from test_admin_library import make_recipe
    folder = make_recipe(setup.root, name="已完成菜谱")
    atomic_write_json(folder / "job.json", {"status": "done"})
    state = create_batch_state(["https://www.bilibili.com/video/BV13p411d7oQ"],
                               {"out": str(setup.root), "target_stage": "recipe"}, project_root=setup.project)
    item = state.items[0]
    item.status, item.output_folder = "done", str(folder)
    for stage in item.stages.values():
        stage.status = "done"
    save_batch_state(state, setup.project)
    return state, folder


def test_same_video_request_opens_completed_result_without_new_job(setup):
    state, _ = completed_generation(setup)
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"]})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == state.batch_id
    assert response.json()["reused"] and response.json()["status"] == "completed"
    assert len(list((setup.project / "data/state/batches").glob("*.json"))) == 1


def test_same_video_failure_reuses_task_and_deliberate_regeneration_is_separate(setup, monkeypatch):
    state = create_batch_state(["BV13p411d7oQ"], {"out": str(setup.root)}, project_root=setup.project)
    state.items[0].status = "failed"
    state.items[0].stages["raw"].status = "failed"
    save_batch_state(state, setup.project)
    calls = []
    monkeypatch.setattr(tasks.generation, "start_background_batch", lambda options, **kwargs: calls.append(options))
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"]})
    assert response.status_code == 200, response.text
    assert response.json()["reused"] and calls[-1].batch_id == state.batch_id
    separate = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"], "options": {"skip_existing": False}})
    assert separate.status_code == 200, separate.text
    assert not separate.json()["reused"] and calls[-1].batch_id != state.batch_id


def test_reused_success_with_missing_output_reopens_only_missing_work(setup, monkeypatch):
    state, folder = completed_generation(setup)
    (folder / "transcript.json").unlink()
    calls = []
    monkeypatch.setattr(tasks.generation, "start_background_batch", lambda options, **kwargs: calls.append(options))
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "generation", "urls": ["BV13p411d7oQ"]})
    assert response.status_code == 200, response.text
    assert response.json()["reused"] and calls[-1].batch_id == state.batch_id
    item = load_batch_state(state.batch_id, setup.project).items[0]
    assert item.stages["raw"].status == item.stages["recipe"].status == "pending"


def test_manual_consolidation_removes_duplicate_failures_but_get_does_not(setup):
    successful, _ = completed_generation(setup)
    failed = create_batch_state(["BV13p411d7oQ"], {"out": str(setup.root)}, project_root=setup.project)
    failed.items[0].status = "failed"
    failed.items[0].error = "old failure"
    save_batch_state(failed, setup.project)
    assert len(setup.client.get("/api/admin/v1/tasks").json()["items"]) == 2
    response = setup.client.post("/api/admin/v1/tasks/consolidate")
    assert response.status_code == 200, response.text
    assert response.json()["removed_tasks"] == 1 and response.json()["recovered_items"] == 1
    items = setup.client.get("/api/admin/v1/tasks").json()["items"]
    assert len(items) == 1 and items[0]["completed"] == 1 and not items[0]["failed"]
def test_generation_immediate_pause_and_continue_keep_worker_identity(setup, monkeypatch):
    state = create_batch_state(["BV13p411d7oQ"], {"out": str(setup.root)}, project_root=setup.project)
    runtime = tasks.generation.BackgroundBatchStatus(state.batch_id, "running", "2026-10-05T00:00:00+00:00", pid=123)
    calls = []
    monkeypatch.setattr(tasks.generation, "get_background_batch_status", lambda *args: runtime)
    monkeypatch.setattr(tasks.generation, "pause_background_batch", lambda *args: calls.append("pause"))
    assert setup.client.post(f"/api/admin/v1/tasks/generation/{state.batch_id}/pause", json={}).status_code == 200
    runtime = tasks.generation.BackgroundBatchStatus(state.batch_id, "paused", "2026-10-05T00:00:00+00:00", pid=123)
    monkeypatch.setattr(tasks.generation, "resume_background_batch", lambda *args: calls.append("resume"))
    assert setup.client.post(f"/api/admin/v1/tasks/generation/{state.batch_id}/resume", json={}).status_code == 200
    assert calls == ["pause", "resume"]


def test_real_frame_selection_scope_respects_visibility_and_manual_covers(setup, monkeypatch):
    set_ai_review_visibility(setup.second, "hidden")
    original = original_bytes(setup.root)
    monkeypatch.setattr(tasks.frames, "_preflight_options", lambda options: options)
    def fake_launch(root, state):
        state.update(status="paused", run_token="fake", stop_requested=False)
        return tasks.frames._write(root, state)
    monkeypatch.setattr(tasks.frames, "_launch", fake_launch)
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "frames", "recipe_ids": [setup.ids["b"], setup.ids["a"]], "limit": 0})
    assert response.status_code == 200, response.text
    value = response.json()
    assert [item["folder"] for item in value["items"]] == ["up/a"]
    assert "cookies" not in value["options"]
    assert original_bytes(setup.root) == original
    assert setup.client.get(f"/api/admin/v1/tasks/frames/{value['id']}").status_code == 200
    assert setup.client.post("/api/admin/v1/tasks", json={"kind": "frames", "recipe_ids": ["../../outside"]}).status_code == 404


def test_frame_no_match_counts_once_and_runtime_progress_is_exposed(setup, monkeypatch):
    state = {"batch_id": "frame-1234567890abcdef", "status": "running", "active": True, "current_folder": "up/b",
             "current_progress": {"phase": "fine", "current": 2, "total": 8}, "completed": 2, "not_found": 1,
             "items": [{"folder": "a", "status": "completed"}, {"folder": "b", "status": "not_found"}, {"folder": "c", "status": "running"}]}
    monkeypatch.setattr(tasks.frames, "list_frame_selection_batches", lambda *args: [state])
    result = setup.client.get("/api/admin/v1/tasks").json()["items"][0]
    assert result["processed"] == 2 and result["completed"] == 1 and result["not_found"] == 1
    assert result["progress"] == pytest.approx(2 / 3)
    assert result["current_progress"]["phase"] == "fine"
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "frames"})
    assert response.json()["already_running"] is True
    assert response.json()["id"] == state["batch_id"]


AI_TASK_RUNNERS = [
    ("frames", tasks.frames, "run_frame_selection_batch"),
    ("review", tasks.reviews, "run_ai_review_batch"),
    ("dish-info", tasks.introductions, "run_dish_info_batch"),
]


def test_frame_needs_review_and_no_match_retry_have_separate_counts_and_action(setup, monkeypatch):
    state = {"batch_id": "frame-1234567890abcdef", "status": "paused", "active": False,
             "items": [{"folder": "a", "status": "completed"}, {"folder": "b", "status": "needs_review"},
                       {"folder": "c", "status": "not_found"}, {"folder": "d", "status": "pending"}]}
    monkeypatch.setattr(tasks.frames, "list_frame_selection_batches", lambda *args: [state])
    calls = []
    monkeypatch.setattr(tasks.frames, "resume_frame_selection_batch", lambda *args, **kwargs: calls.append(kwargs) or state)
    url = f"/api/admin/v1/tasks/frames/{state['batch_id']}"
    summary = setup.client.get(url).json()
    assert (summary["completed"], summary["needs_review"], summary["not_found"], summary["processed"], summary["pending"]) == (1, 1, 1, 3, 1)
    assert summary["can_retry_not_found"] and not summary["can_retry"]
    response = setup.client.post(url + "/retry-not-found", json={"run_limit": 2})
    assert response.status_code == 200
    assert calls == [{"retry_failed": False, "retry_not_found": True, "run_limit": 2, "cookies": "private-cookie.txt"}]
    state.update(status="running", active=True)
    assert setup.client.post(url + "/retry-not-found", json={}).status_code == 409
    assert len(calls) == 1
    state.update(status="completed", active=False)
    state["items"][2]["status"] = "needs_review"
    assert not setup.client.get(url).json()["can_retry_not_found"]
    assert setup.client.post(url + "/retry-not-found", json={}).status_code == 409
    state.update(status="paused", run_scope="not_found", run_targets=["c"])
    state["items"][2]["status"] = "not_found"
    state["items"][3]["status"] = "completed"
    summary = setup.client.get(url).json()
    assert summary["can_retry_not_found"] and not summary["can_resume"]


def test_no_match_retry_rejects_other_task_kinds(setup, monkeypatch):
    state = {"batch_id": "intro-1234567890abcdef", "status": "paused", "active": False,
             "items": [{"folder": "a", "status": "not_found"}]}
    monkeypatch.setattr(tasks.introductions, "list_dish_info_batches", lambda *args: [state])
    response = setup.client.post(f"/api/admin/v1/tasks/dish-info/{state['batch_id']}/retry-not-found", json={})
    assert response.status_code == 400 and "仅 AI 选图" in response.json()["detail"]


@pytest.mark.parametrize("kind,module,worker_name", AI_TASK_RUNNERS)
def test_ai_queue_api_preserves_all_items_and_runs_only_next_quota(setup, monkeypatch, kind, module, worker_name):
    original = original_bytes(setup.root)
    calls = []
    launches = []

    def launch(root, state):
        token = f"synthetic-{len(launches)}"
        state.update(status="paused", run_token=token, pid=None, process_start_id=None,
                     stop_requested=False, pause_reason=None, finished_at=None, error=None, run_processed=0)
        state.setdefault("runs", []).append({"run_token": token, "started_at": module._now(), "run_limit": state["run_limit"]})
        launches.append(token)
        return module._write(root, state)

    def process(folder, **options):
        calls.append(folder.name)
        return {"status": "ok", "classification": "recipe", "visibility": "visible"}

    monkeypatch.setattr(module, "_launch", launch)
    if kind == "frames":
        monkeypatch.setattr(module, "_preflight_options", lambda options: options)
        monkeypatch.setattr(module, "generate_frame_candidate", process)
    elif kind == "review":
        monkeypatch.setattr(module, "review_recipe", process)
    else:
        monkeypatch.setattr(module, "generate_dish_info", process)

    response = setup.client.post("/api/admin/v1/tasks", json={
        "kind": kind, "limit": 0, "run_limit": 1, "create_only": True,
    })
    assert response.status_code == 200, response.text
    queued = response.json()
    assert queued["status"] == "pending" and queued["total"] == queued["pending"] == 2
    assert queued["run_limit"] == 1 and queued["run_processed"] == 0 and queued["can_resume"]
    assert launches == calls == []
    task_url = f"/api/admin/v1/tasks/{kind}/{queued['id']}"
    assert setup.client.post(task_url + "/resume", json={}).status_code == 200
    getattr(module, worker_name)(setup.root, queued["id"], launches[-1])
    first = setup.client.get(task_url).json()
    assert first["status"] == "paused" and first["pause_reason"] == "batch_limit"
    assert (first["total"], first["processed"], first["pending"], first["run_processed"]) == (2, 1, 1, 1)
    assert first["can_resume"] and not first["can_pause"]
    assert calls == ["a"]
    listed = next(item for item in setup.client.get("/api/admin/v1/tasks").json()["items"] if item["id"] == queued["id"])
    assert listed["pending"] == 1 and listed["pause_reason"] == "batch_limit"

    resumed = setup.client.post(task_url + "/resume", json={"run_limit": 0})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run_limit"] == resumed.json()["run_processed"] == 0
    getattr(module, worker_name)(setup.root, queued["id"], launches[-1])
    finished = setup.client.get(task_url).json()
    assert finished["status"] == "completed" and finished["pending"] == 0
    assert finished["processed"] == 2 and finished["run_processed"] == 1
    assert calls == ["a", "b"]
    assert original_bytes(setup.root) == original


@pytest.mark.parametrize("kind,module,worker_name", AI_TASK_RUNNERS)
def test_ai_create_only_api_can_queue_while_another_batch_is_running(setup, monkeypatch, kind, module, worker_name):
    listing = {"frames": "list_frame_selection_batches", "review": "list_ai_review_batches",
               "dish-info": "list_dish_info_batches"}[kind]
    active = {"batch_id": "existing", "status": "running", "active": True,
              "items": [{"folder": "up/a", "status": "running"}]}
    monkeypatch.setattr(module, listing, lambda *args: [active])
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": kind, "limit": 0, "run_limit": 1, "create_only": True})
    assert response.status_code == 200, response.text
    assert response.json()["id"] != "existing" and response.json()["status"] == "pending"
    assert response.json()["total"] == 2 and not response.json()["already_running"]
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": kind, "limit": 0, "run_limit": 1})
    assert response.status_code == 200 and response.json()["id"] == "existing"
    assert response.json()["already_running"]


@pytest.mark.parametrize("value", [-1, 1.5, True, "10", 10001])
def test_execution_quota_api_rejects_invalid_values_without_starting_work(setup, value):
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "review", "run_limit": value, "create_only": True})
    assert response.status_code == 422
    response = setup.client.post("/api/admin/v1/tasks/review/nonexistent/resume", json={"run_limit": value})
    assert response.status_code == 422


def test_execution_quota_is_not_silently_ignored_for_generation(setup):
    response = setup.client.post("/api/admin/v1/tasks", json={
        "kind": "generation", "urls": ["BV13p411d7oQ"], "run_limit": 1, "create_only": True,
    })
    assert response.status_code == 400
    assert not list((setup.project / "data/state/batches").glob("*.json"))


def test_limited_ai_eta_covers_current_execution_not_the_entire_queue():
    from datetime import datetime, timedelta, timezone

    started = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat()
    state = {"batch_id": "sample", "active": True, "status": "running", "run_limit": 2, "run_processed": 1,
             "runs": [{"started_at": started}],
             "items": [{"status": "completed"}] + [{"status": "pending"} for _ in range(20)]}
    summary = tasks.summarize("frames", state)
    assert summary["pending"] == 20 and summary["run_processed"] == 1
    assert 99 <= summary["eta_seconds"] <= 102
    state.update(active=False, status="paused", pause_reason="batch_limit")
    assert tasks.summarize("frames", state)["eta_seconds"] is None


@pytest.mark.parametrize("kind,module", [("review", tasks.reviews), ("dish-info", tasks.introductions)])
def test_ai_tasks_use_current_config_and_independent_selection(setup, monkeypatch, kind, module):
    def launch(root, state):
        state.update(status="paused", run_token="fake", stop_requested=False)
        return module._write(root, state)
    monkeypatch.setattr(module, "_launch", launch)
    original = original_bytes(setup.root)
    options = {"web_search": True} if kind == "dish-info" else {}
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": kind, "recipe_ids": [setup.ids["a"]], "options": options})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["total"] == 1 and value["options"]["provider"] == "local"
    assert original_bytes(setup.root) == original
    assert setup.client.post(f"/api/admin/v1/tasks/{kind}/{value['id']}/resume", json={"use_current_config": True}).status_code == 200


def test_ai_pause_is_after_current_and_retry_uses_runner_semantics(setup, monkeypatch):
    state = {"batch_id": "intro-1234567890abcdef", "status": "running", "active": True, "options": {"provider": "local"},
             "items": [{"folder": "up/a", "status": "running"}, {"folder": "up/b", "status": "failed"}]}
    monkeypatch.setattr(tasks.introductions, "list_dish_info_batches", lambda *args: [state])
    monkeypatch.setattr(tasks.introductions, "stop_dish_info_batch", lambda *args: state.update(stop_requested=True) or state)
    response = setup.client.post(f"/api/admin/v1/tasks/dish-info/{state['batch_id']}/pause", json={})
    assert response.json()["status"] == "pausing"
    assert not response.json()["can_resume"] and not response.json()["can_pause"]
    state.update(status="paused", active=False)
    calls = []
    monkeypatch.setattr(tasks.introductions, "resume_dish_info_batch", lambda *args, **kwargs: calls.append(kwargs) or state)
    response = setup.client.post(f"/api/admin/v1/tasks/dish-info/{state['batch_id']}/retry", json={})
    assert response.status_code == 200
    assert calls == [{"retry_failed": True}]


def test_creator_refresh_is_a_persistent_background_job_and_invalid_host_rejected(setup, monkeypatch):
    calls = []
    class Process:
        pid = 123456
        def poll(self): return None
    monkeypatch.setattr(creator.subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs)) or Process())
    monkeypatch.setattr(creator, "_identity", lambda pid: str(pid))
    monkeypatch.setattr(creator, "_pid_is_alive", lambda pid: True)
    response = setup.client.post("/api/admin/v1/creators/refresh", json={"url": "https://space.bilibili.com/123/video?spm=abc"})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["kind"] == "creators" and value["status"] == "queued"
    assert calls[0][0][-2] == value["id"]
    state = json.loads(creator.creator_task_path(setup.project, value["id"]).read_text())
    assert state["options"]["url"] == "https://space.bilibili.com/123/video"
    assert "cookies" not in str(state)
    assert setup.client.post("/api/admin/v1/creators/refresh", json={"url": "https://space.bilibili.com/123"}).json()["already_running"]
    assert len(calls) == 1
    assert setup.client.post("/api/admin/v1/creators/refresh", json={"url": "https://space.bilibili.com.evil/123"}).status_code == 400


def test_creator_worker_preserves_originals_and_tracks_partial_success_and_errors(setup, monkeypatch):
    task_id = "creator-1234567890abcdef"
    path = creator.creator_task_path(setup.project, task_id)
    state = {"batch_id": task_id, "status": "queued", "run_token": "test", "options": {"url": "https://space.bilibili.com/123/video"}, "items": [{"status": "pending"}]}
    atomic_write_json(path, state)
    before = original_bytes(setup.root)
    fake = SimpleNamespace(crawl=SimpleNamespace(videos=["url"], complete=False, warnings=["partial crawl"]))
    monkeypatch.setattr(creator, "crawl_and_archive_creator", lambda *args: fake)
    result = creator.run_creator_task(setup.project, task_id, "test")
    assert result["status"] == "completed" and result["items"][0]["complete"] is False
    assert result["items"][0]["warnings"] == ["partial crawl"]
    assert original_bytes(setup.root) == before
    atomic_write_json(path, state)
    def fail(*args): raise RuntimeError("network unavailable")
    monkeypatch.setattr(creator, "crawl_and_archive_creator", fail)
    result = creator.run_creator_task(setup.project, task_id, "test")
    assert result["status"] == "failed" and "network unavailable" in result["error"]
    assert original_bytes(setup.root) == before


def test_creator_interrupted_worker_is_retryable(setup):
    task_id = "creator-1234567890abcdef"
    atomic_write_json(creator.creator_task_path(setup.project, task_id), {"batch_id": task_id, "status": "running",
        "pid": 999999999, "process_start_id": "old", "options": {"url": "https://space.bilibili.com/123"}, "items": [{"status": "running"}]})
    response = setup.client.get(f"/api/admin/v1/tasks/creators/{task_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "interrupted" and response.json()["can_retry"]


def test_creator_saved_lists_are_readable_and_ignore_symlinks(setup):
    folder = setup.project / "data/creators/123-王刚"
    folder.mkdir(parents=True)
    atomic_write_json(folder / "creator.json", {"uid": "123", "uploader": "王刚", "complete": False, "warnings": ["partial"], "source_url": "https://space.bilibili.com/123"})
    (folder / "video_links.txt").write_text("BV13p411d7oQ\nBV13p411d7oQ\n")
    value = setup.client.get("/api/admin/v1/creators").json()["items"]
    assert len(value) == 1 and value[0]["count"] == 1 and not value[0]["complete"]
    (folder.parent / "linked").symlink_to(folder, target_is_directory=True)
    assert len(setup.client.get("/api/admin/v1/creators").json()["items"]) == 1


def test_settings_operations_are_part_of_task_center(setup, monkeypatch):
    from bili_recipe_notes import admin_settings
    state = {"id": "ops", "kind": "deployment_export", "status": "completed", "total": 1, "processed": 1,
             "items": [], "result": {"url": "/api/admin/v1/settings/downloads/file", "name": "export.zip"}}
    monkeypatch.setattr(admin_settings, "list_settings_operations", lambda ctx: [state])
    monkeypatch.setattr(admin_settings, "get_settings_operation", lambda ctx, task_id: state)
    result = setup.client.get("/api/admin/v1/tasks").json()["items"][0]
    assert result["title"] == "导出数据包" and result["progress"] == 1
    detail = setup.client.get("/api/admin/v1/tasks/settings/ops").json()
    assert detail["result"]["name"] == "export.zip"


def test_creator_worker_entrypoint_dispatch(monkeypatch):
    from bili_recipe_notes import cli, ui_launcher
    import sys
    seen = []
    monkeypatch.setattr(creator, "main", lambda args: seen.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["app", "--admin-creator-worker", "/synthetic", "creator-1234567890abcdef", "token"])
    assert cli.main() == 0
    assert ui_launcher.main() == 0
    assert seen == [["/synthetic", "creator-1234567890abcdef", "token"]] * 2
