from __future__ import annotations

import base64
import io
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from bili_recipe_notes import admin_settings as admin
from bili_recipe_notes import knowledge_base as kb
from bili_recipe_notes.admin_common import require_admin_request
from bili_recipe_notes.config import UIConfig, load_config, save_config
from bili_recipe_notes.data_paths import state_dir
from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.storage import atomic_write_json


@pytest.fixture
def setup(tmp_path, monkeypatch, authorize_client):
    monkeypatch.delenv("BILI_RECIPE_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "recipes"
    out.mkdir()
    store = MobileSyncStore(tmp_path, out_dir=out)
    save_config(UIConfig(out_dir=str(out)), tmp_path)
    folders = {}

    def folder(recipe_id):
        if recipe_id not in folders:
            raise HTTPException(404, "菜谱不存在")
        return folders[recipe_id]

    def config():
        value = load_config(tmp_path)
        value.out_dir = str(out)
        return value

    ctx = SimpleNamespace(store=store, indexer=SimpleNamespace(snapshot=lambda: {"status": "ok"}), config=config, folder=folder)
    app = FastAPI()
    app.include_router(admin.create_router(ctx), prefix="/api/admin/v1", dependencies=[Depends(require_admin_request)])
    client = authorize_client(TestClient(app), tmp_path)
    yield SimpleNamespace(client=client, ctx=ctx, root=tmp_path, out=out, folders=folders)
    for path, worker in list(admin._RUNNING.items()):
        if path.startswith(str(tmp_path)):
            worker.join(timeout=5)
            admin._RUNNING.pop(path, None)


def request(setup, method, path, **kwargs):
    return setup.client.request(method, f"/api/admin/v1{path}", **kwargs)


def finish(setup, operation):
    worker = admin._RUNNING[str(state_dir(setup.root) / "admin-operations" / f"{operation['id']}.json")]
    worker.join(timeout=5)
    assert not worker.is_alive()
    return request(setup, "GET", f"/settings/operations/{operation['id']}").json()


def test_configuration_validates_and_new_directory_requires_restart(setup):
    result = request(setup, "GET", "/settings").json()
    assert result["active_out_dir"] == str(setup.out)
    assert result["restart_required"] is False
    updated = request(setup, "PUT", "/settings", json={"config": {"restaurant_name": "测试厨房", "out_dir": str(setup.root / "next-library")}})
    assert updated.status_code == 200
    assert updated.json()["restart_required"] is True
    assert updated.json()["config"]["restaurant_name"] == "测试厨房"
    assert setup.ctx.config().out_dir == str(setup.out)
    for invalid in ({"enable_screenshot": "yes"}, {"max_recipe_steps": True}, {"max_recipe_steps": 20},
                    {"max_step_images": 0}, {"unknown": 1}, {"llm_provider": "invalid"},
                    {"restaurant_logo_path": "/etc/passwd"}, {"config": []}):
        response = request(setup, "PUT", "/settings", json=invalid)
        assert response.status_code == 422, response.text
    assert load_config(setup.root).restaurant_name == "测试厨房"


def test_devices_are_revoked_and_self_join_is_controlled(setup):
    setup.ctx.store.join_device("测试平板")
    devices = request(setup, "GET", "/settings/devices").json()
    assert len(devices["devices"]) == 1
    device = devices["devices"][0]
    assert "access_token" not in device
    response = request(setup, "PUT", "/settings/devices/self-join", json={"enabled": False})
    assert response.json()["self_join_enabled"] is False
    assert request(setup, "PUT", "/settings/devices/self-join", json={"enabled": "false"}).status_code == 422
    response = request(setup, "DELETE", f"/settings/devices/{device['id']}")
    assert response.json()["devices"][0]["revoked_at"]
    assert request(setup, "DELETE", "/settings/devices/missing").status_code == 404


def test_cookie_actions_never_return_cookie_content(setup, monkeypatch):
    imported = state_dir(setup.root) / "cookies" / "bilibili-edge.txt"
    imported.parent.mkdir(parents=True)
    imported.write_text("private-cookie-secret")
    monkeypatch.setattr(admin.downloader, "import_edge_cookies", lambda root: imported)
    monkeypatch.setattr(admin.downloader, "validate_bilibili_cookie_file", lambda path: Path(path) == imported)
    monkeypatch.setattr(admin.downloader, "remove_imported_cookies", lambda root: imported.unlink())
    assert request(setup, "POST", "/settings/cookies/import").json() == {"configured": True}
    assert "private-cookie-secret" not in request(setup, "GET", "/settings").text
    assert request(setup, "POST", "/settings/cookies/validate").json() == {"valid": True}
    assert request(setup, "POST", "/settings/cookies/clear").json() == {"configured": False}
    assert not imported.exists()
    assert load_config(setup.root).cookies is None


def test_logo_upload_is_validated_and_download_path_is_controlled(setup):
    source = io.BytesIO()
    Image.new("RGB", (20, 10), "green").save(source, format="PNG")
    result = request(setup, "POST", "/settings/logo", json={"content": base64.b64encode(source.getvalue()).decode()})
    assert result.status_code == 200
    assert result.json()["branding"]["has_logo"] is True
    assert Path(load_config(setup.root).restaurant_logo_path).is_file()
    assert request(setup, "POST", "/settings/logo", json={"content": "%%%"}).status_code == 422
    assert request(setup, "POST", "/settings/logo", json={"content": base64.b64encode(b"not an image").decode()}).status_code == 422
    assert request(setup, "POST", "/settings/logo", json={"content": "x" * (7 * 1024 * 1024)}).status_code == 413
    assert request(setup, "DELETE", "/settings/logo").json()["branding"]["has_logo"] is False
    assert request(setup, "GET", "/downloads/not-a-token").status_code == 404


def test_environment_is_explicit_and_uses_service(setup, monkeypatch):
    seen = []
    monkeypatch.setattr(admin.environment, "run_environment_checks", lambda: seen.append(True) or [admin.environment.EnvironmentCheck("ffmpeg", True, "fake")])
    request(setup, "GET", "/settings")
    assert seen == []
    response = request(setup, "POST", "/settings/environment")
    assert response.json()["checks"][0]["name"] == "ffmpeg"
    assert seen == [True]


def add_entry(setup, title="焯水去腥", content="按来源描述冷水下锅并撇去浮沫。"):
    response = request(setup, "POST", "/knowledge", json={"title": title, "category": "技巧", "content": content, "tags": ["焯水"], "evidence": "来源原文"})
    assert response.status_code == 200, response.text
    return request(setup, "GET", "/knowledge").json()["items"][0]


def test_knowledge_crud_review_practice_and_backup_preserve_originals(setup):
    recipe = setup.out / "原始菜谱"
    recipe.mkdir()
    original = b'{"title":"keep-original"}'
    (recipe / "recipe.json").write_bytes(original)
    entry = add_entry(setup)
    assert request(setup, "GET", "/knowledge?query=除腥").json()["total"] == 1
    updated = request(setup, "PUT", f"/knowledge/{entry['id']}", json={"review_status": "approved", "content": "核对后的来源内容"})
    assert updated.status_code == 200
    assert updated.json()["review_status"] == "approved"
    assert request(setup, "PUT", f"/knowledge/{entry['id']}", json={"review_count": 900}).status_code == 422
    reviewed = request(setup, "POST", f"/knowledge/{entry['id']}/review", json={"mastery": "已掌握"})
    assert reviewed.json()["review_count"] == 1
    assert request(setup, "GET", "/knowledge?due=true").json()["total"] == 0
    practiced = request(setup, "POST", f"/knowledge/{entry['id']}/practice", json={"dish": "炖肉", "outcome": "成功", "notes": "口感更清爽"})
    assert practiced.json()["practice_records"][0]["dish"] == "炖肉"
    assert request(setup, "DELETE", f"/knowledge/{entry['id']}").status_code == 200
    assert request(setup, "GET", "/knowledge").json()["total"] == 0
    backups = request(setup, "GET", "/knowledge/backups").json()["items"]
    saved = next(item for item in backups if "knowledge-delete" in item["id"])
    assert request(setup, "POST", "/knowledge/backups/restore", json={"id": "../config.json"}).status_code == 404
    assert request(setup, "POST", "/knowledge/backups/restore", json={"id": saved["id"]}).json()["restored"]
    assert request(setup, "GET", "/knowledge").json()["total"] == 1
    assert (recipe / "recipe.json").read_bytes() == original


def test_knowledge_export_and_duplicate_merge(setup):
    one = kb.CookingKnowledgeEntry(id="one", title="焯水去腥", category="技巧", content="冷水下锅焯水，捞出后冲净浮沫。")
    two = kb.CookingKnowledgeEntry(id="two", title="肉类焯水去腥", category="技巧", content="冷水下锅焯水，捞出后冲净浮沫。", source_url="https://example.com/source")
    kb.save_knowledge_entries([one, two], setup.root)
    groups = request(setup, "GET", "/knowledge/duplicates")
    assert groups.status_code == 200
    assert len(groups.json()["groups"]) == 1
    merged = request(setup, "POST", "/knowledge/merge", json={"primary_id": "one", "duplicate_ids": ["two"]})
    assert merged.status_code == 200
    assert merged.json()["source_refs"][0]["url"] == "https://example.com/source"
    for kind in ("markdown", "csv", "anki"):
        result = request(setup, "POST", "/knowledge/export", json={"kind": kind}).json()
        downloaded = setup.client.get(result["url"])
        assert downloaded.status_code == 200
        assert "焯水" in downloaded.content.decode("utf-8-sig")
    assert request(setup, "POST", "/knowledge/export", json={"kind": "../../config"}).status_code == 422


def test_extraction_records_failure_continues_and_retries_only_failed(setup, monkeypatch):
    for recipe_id in ("one", "two", "three"):
        folder = setup.out / recipe_id
        folder.mkdir()
        (folder / "recipe.json").write_text('{"title":"original"}')
        setup.folders[recipe_id] = folder
    calls = []
    failing = {"two"}

    def extract(folders, options, root, skip_existing):
        folder = folders[0]
        calls.append(folder.name)
        if folder.name in failing:
            raise RuntimeError("fake model unavailable")
        return kb.KnowledgeBatchExtractionResult(kb.knowledge_base_path(root), [kb.KnowledgeBatchExtractionItem(folder, "done", added_count=1)], added_count=1)

    monkeypatch.setattr(kb, "extract_knowledge_from_folders", extract)
    original = {key: (folder / "recipe.json").read_bytes() for key, folder in setup.folders.items()}
    created = request(setup, "POST", "/knowledge/extract", json={"recipe_ids": list(setup.folders)}).json()
    done = finish(setup, created)
    assert done["status"] == "completed_with_errors"
    assert done["processed"] == 3
    assert calls == ["one", "two", "three"]
    failing.clear()
    resumed = request(setup, "POST", f"/settings/operations/{done['id']}/resume").json()
    assert finish(setup, resumed)["status"] == "completed"
    assert calls == ["one", "two", "three", "two"]
    assert {key: (folder / "recipe.json").read_bytes() for key, folder in setup.folders.items()} == original
    assert request(setup, "POST", "/knowledge/extract", json={"recipe_ids": ["missing"]}).status_code == 404
    assert admin.list_settings_operations(setup.ctx)[0]["status"] == "completed"


def test_interrupted_operations_can_resume_and_cannot_cross_libraries(setup, monkeypatch):
    operation_id = "a" * 32
    path = state_dir(setup.root) / "admin-operations" / f"{operation_id}.json"
    atomic_write_json(path, {"id": operation_id, "kind": "knowledge_extract", "out_dir": str(setup.out), "status": "running", "items": [], "arguments": {}, "created_at": "2026-01-01", "processed": 0, "total": 0})
    assert admin.get_settings_operation(setup.ctx, operation_id)["status"] == "interrupted"
    assert finish(setup, admin.resume_settings_operation(setup.ctx, operation_id))["status"] == "completed"
    raw = admin.get_settings_operation(setup.ctx, operation_id)
    raw.update(status="interrupted", out_dir=str(setup.root / "different"))
    atomic_write_json(path, raw)
    assert request(setup, "POST", f"/settings/operations/{operation_id}/resume").status_code == 409


def test_deployment_export_download_and_handoff_import_use_controlled_files(setup, monkeypatch):
    seen = []

    def export(out_dir, destination, project_root):
        assert out_dir == setup.out
        seen.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"synthetic-export")
        return SimpleNamespace(path=destination)

    monkeypatch.setattr(admin, "export_deployment_bundle", export)
    created = request(setup, "POST", "/settings/export").json()
    done = finish(setup, created)
    assert done["status"] == "completed", done
    assert setup.client.get(done["result"]["url"]).content == b"synthetic-export"
    assert request(setup, "GET", "/downloads/" + "b" * 32).status_code == 404
    assert not seen[0].exists()

    def import_package(path, out, project_root):
        assert path.is_relative_to(setup.root / "data" / "sources" / "admin-imports")
        assert out == setup.out
        raise ValueError("synthetic archive rejected")

    monkeypatch.setattr(admin, "import_handoff_bundle", import_package)
    imported = request(setup, "POST", "/settings/handoff/import", content=b"PKfake", headers={"content-type": "application/zip"}).json()
    result = finish(setup, imported)
    assert result["status"] == "failed"
    assert result["error"] == "synthetic archive rejected"
    assert request(setup, "POST", "/settings/handoff/import", content=b"bad").status_code == 422


def test_document_upload_uses_safe_name_and_migration_remains_preview(setup, monkeypatch):
    seen = []

    def extract(path, options, root):
        seen.append(path)
        assert path.parent == setup.root / "data" / "sources" / "admin-imports"
        assert path.read_text() == "test source"
        return kb.KnowledgeExtractionResult(kb.knowledge_base_path(root), [], 0, 0)

    monkeypatch.setattr(kb, "extract_knowledge_from_document", extract)
    created = request(setup, "POST", "/knowledge/documents", json={"name": "../../source.md", "content": base64.b64encode(b"test source").decode()}).json()
    assert finish(setup, created)["status"] == "completed"
    assert seen and seen[0].name.endswith("-source.md")
    assert request(setup, "POST", "/knowledge/documents", json={"name": "bad.py", "content": "eA=="}).status_code == 422
    legacy = setup.root / "outputs" / "old"
    legacy.mkdir(parents=True)
    (legacy / "recipe.json").write_text('{"title":"old"}')
    before = (legacy / "recipe.json").read_bytes()
    response = request(setup, "GET", "/settings/migration")
    assert response.status_code == 200
    assert response.json()["requires_offline"] is True
    assert "--migrate-data apply" in response.json()["command"]
    assert (legacy / "recipe.json").read_bytes() == before


def test_registered_operations_run_and_retry_with_persisted_arguments(setup):
    calls = []

    def handler(ctx, arguments):
        assert ctx.store.out_dir == setup.out
        calls.append(arguments["recipe_id"])
        if len(calls) == 1:
            raise RuntimeError("first fake failure")
        return {"done": True}

    admin.register_settings_operation("test_tools", handler)
    created = admin.enqueue_settings_operation(setup.ctx, "test_tools", {"recipe_id": "fake"})
    assert finish(setup, created)["status"] == "failed"
    finished = finish(setup, admin.resume_settings_operation(setup.ctx, created["id"]))
    assert finished["result"] == {"done": True}
    assert finished["processed"] == 1
    assert calls == ["fake", "fake"]
    with pytest.raises(HTTPException):
        admin.enqueue_settings_operation(setup.ctx, "not_registered", {})


def test_batch_archive_checks_each_revision_and_preserves_manual_destination(setup, monkeypatch):
    for recipe_id in ("first", "stale", "last"):
        folder = setup.out / recipe_id
        folder.mkdir()
        (folder / "recipe.json").write_text("original")
        setup.folders[recipe_id] = folder
    calls = []

    @dataclass
    class Result:
        note_path: Path

    def check_revision(folder, revision):
        assert revision == "expected"
        if folder.name == "stale":
            raise HTTPException(409, "菜谱已变化")

    def archive(folder, vault, *, conflict):
        assert conflict == "update"
        calls.append(folder.name)
        return Result(Path(vault) / f"{folder.name}.md")

    setup.ctx.check_revision = check_revision
    monkeypatch.setattr(admin, "archive_recipe_to_obsidian", archive)
    created = admin.start_settings_operation(setup.ctx, "recipe_archive", {}, items=[{"recipe_id": key, "revision": "expected"} for key in setup.folders])
    done = finish(setup, created)
    assert done["status"] == "completed_with_errors", done
    assert done["processed"] == 3
    assert calls == ["first", "last"]
    assert done["items"][1]["status"] == "failed"
    assert "菜谱已变化" in done["items"][1]["error"]
    assert all((folder / "recipe.json").read_text() == "original" for folder in setup.folders.values())


def test_native_operation_lock_detects_other_process_and_releases_on_exit(setup):
    operation_id = "c" * 32
    path = state_dir(setup.root) / "admin-operations" / f"{operation_id}.json"
    atomic_write_json(path, {"id": operation_id, "kind": "knowledge_extract", "out_dir": str(setup.out), "status": "running", "items": [], "arguments": {}, "created_at": "2026-01-01", "processed": 0, "total": 0})
    script = "from pathlib import Path; import sys; from bili_recipe_notes.admin_settings import _try_operation_lock; handle = _try_operation_lock(Path(sys.argv[1])); print('locked' if handle else 'failed', flush=True); sys.stdin.read()"
    process = subprocess.Popen([sys.executable, "-u", "-c", script, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=Path(admin.__file__).parent.parent)
    try:
        assert process.stdout.readline().strip() == "locked"
        assert admin.get_settings_operation(setup.ctx, operation_id)["status"] == "running"
        assert admin.resume_settings_operation(setup.ctx, operation_id)["status"] == "running"
        assert str(path) not in admin._RUNNING
    finally:
        process.communicate("", timeout=5)
    assert admin.get_settings_operation(setup.ctx, operation_id)["status"] == "interrupted"
    assert finish(setup, admin.resume_settings_operation(setup.ctx, operation_id))["status"] == "completed"


def test_concurrent_retries_start_only_one_worker(setup):
    started, release = threading.Event(), threading.Event()
    calls = []

    def handler(ctx, arguments):
        calls.append(arguments)
        started.set()
        assert release.wait(timeout=5)
        return {"done": True}

    operation_id = "d" * 32
    admin.register_settings_operation("concurrent_test", handler)
    path = state_dir(setup.root) / "admin-operations" / f"{operation_id}.json"
    atomic_write_json(path, {"id": operation_id, "kind": "concurrent_test", "out_dir": str(setup.out), "status": "failed", "items": [], "arguments": {}, "created_at": "2026-01-01", "processed": 0, "total": 1})
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(admin.resume_settings_operation, setup.ctx, operation_id) for _ in range(8)]
            for future in futures:
                assert future.result(timeout=5)["status"] in {"queued", "running"}
        assert started.wait(timeout=5)
        assert len(calls) == 1
    finally:
        release.set()
    assert finish(setup, {"id": operation_id})["status"] == "completed"
