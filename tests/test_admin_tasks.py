from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from bili_recipe_notes import admin_tasks as tasks, admin_creator_worker as creator
from bili_recipe_notes.admin_common import AdminContext
from bili_recipe_notes.batch_queue import create_batch_state, load_batch_state, save_batch_state
from bili_recipe_notes.config import UIConfig, save_config
from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.review_policy import set_ai_review_visibility
from bili_recipe_notes.storage import atomic_write_json
from test_dish_info_runner import recipe, original_bytes


@pytest.fixture
def setup(tmp_path, monkeypatch):
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
    app.include_router(tasks.create_router(ctx), prefix="/api/admin/v1")
    ids = {Path(row["output_folder"]).name: row["id"] for row in ctx.rows()}
    monkeypatch.setattr(tasks.generation, "start_background_batch", lambda *args, **kwargs: pytest.fail("unexpected real generation launch"))
    monkeypatch.setattr(tasks.reviews, "_launch", lambda *args: pytest.fail("unexpected real review launch"))
    monkeypatch.setattr(tasks.frames, "_launch", lambda *args: pytest.fail("unexpected real frame launch"))
    monkeypatch.setattr(tasks.introductions, "_launch", lambda *args: pytest.fail("unexpected real introduction launch"))
    return SimpleNamespace(client=TestClient(app), root=root, ctx=ctx, ids=ids, first=first, second=second, project=tmp_path)


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
    assert result["processed"] == 2 and result["completed"] == 2 and result["not_found"] == 1
    assert result["progress"] == pytest.approx(2 / 3)
    assert result["current_progress"]["phase"] == "fine"
    response = setup.client.post("/api/admin/v1/tasks", json={"kind": "frames"})
    assert response.json()["already_running"] is True
    assert response.json()["id"] == state["batch_id"]


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
