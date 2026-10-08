from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from bili_recipe_notes import admin_menu, admin_settings
from bili_recipe_notes.admin_common import AdminContext, require_admin_request
from bili_recipe_notes.config import UIConfig, save_config
from bili_recipe_notes.data_paths import state_dir
from bili_recipe_notes.menu_image import MenuImageFile, MenuImageResult
from bili_recipe_notes.mobile_sync import MobileSyncStore


@pytest.fixture
def setup(tmp_path, monkeypatch, authorize_client):
    monkeypatch.delenv("BILI_RECIPE_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "data" / "recipes"
    originals = {}
    for name in ("青菜", "鸡蛋", "隐藏内容"):
        folder = out / name
        folder.mkdir(parents=True)
        payload = {"title": name, "uploader": "合成作者", "source_url": f"https://example.com/{name}",
                   "ingredients": [], "seasonings": [], "steps": [], "summary_tips": [], "uncertain_points": []}
        path = folder / "recipe.json"
        path.write_text(json.dumps(payload, ensure_ascii=False))
        originals[path] = path.read_bytes()
    store = MobileSyncStore(tmp_path, out_dir=out)
    save_config(UIConfig(out_dir=str(out)), tmp_path)
    store.index_recipes()
    ctx = AdminContext(store, SimpleNamespace(snapshot=lambda: {"status": "ok"}))
    original_rows = ctx.rows
    ctx.rows = lambda: [{**row, "hidden": row["title"] == "隐藏内容"} for row in original_rows()]
    rows = {row["title"]: row for row in ctx.rows()}
    app = FastAPI()
    app.include_router(admin_settings.create_router(ctx), prefix="/api/admin/v1", dependencies=[Depends(require_admin_request)])
    app.include_router(admin_menu.create_router(ctx), prefix="/api/admin/v1", dependencies=[Depends(require_admin_request)])
    client = authorize_client(TestClient(app), tmp_path)
    yield SimpleNamespace(client=client, ctx=ctx, root=tmp_path, rows=rows, originals=originals)
    for path, worker in list(admin_settings._RUNNING.items()):
        if path.startswith(str(tmp_path)):
            worker.join(timeout=5)
            admin_settings._RUNNING.pop(path, None)
    assert all(path.read_bytes() == content for path, content in originals.items())


def call(setup, method, path, **kwargs):
    return setup.client.request(method, f"/api/admin/v1{path}", **kwargs)


def finish(setup, operation):
    path = state_dir(setup.root) / "admin-operations" / f"{operation['id']}.json"
    admin_settings._RUNNING[str(path)].join(timeout=5)
    return admin_settings.get_settings_operation(setup.ctx, operation["id"])


def test_recommendation_publishes_recipe_and_rejects_hidden_content(setup):
    visible = setup.rows["青菜"]["id"]
    hidden = setup.rows["隐藏内容"]["id"]
    setup.ctx.store.set_recipe_publications({visible: False})
    response = call(setup, "POST", "/menu/recommendations", json={"recipe_ids": [visible], "recommended": True})
    assert response.json()["updated"] == 1
    result = call(setup, "GET", "/menu").json()
    assert result["total"] == 2
    assert result["recommended"] == 1
    changed = next(item for item in result["items"] if item["id"] == visible)
    assert changed["published"] is True and changed["recommended"] is True
    assert call(setup, "POST", "/menu/recommendations", json={"recipe_ids": [hidden], "recommended": True}).status_code == 409
    assert call(setup, "POST", "/menu/recommendations", json={"recipe_ids": [visible], "recommended": "true"}).status_code == 422
    assert call(setup, "GET", "/menu?q=鸡蛋").json()["total"] == 1


@pytest.mark.parametrize("image_format,count", [("share", 1), ("print", 2)])
def test_menu_images_run_in_background_with_controlled_downloads(setup, monkeypatch, image_format, count):
    seen = []
    image = io.BytesIO()
    Image.new("RGB", (12, 12), "green").save(image, format="JPEG")

    def generate(recipes, resolver, options):
        assert {item["title"] for item in recipes} == {"青菜", "鸡蛋"}
        assert options.image_format == image_format
        seen.append(options)
        return MenuImageResult(tuple(MenuImageFile(f"page-{index}.jpg", image.getvalue(), "image/jpeg", 12, 12) for index in range(count)), 2, 1)

    monkeypatch.setattr(admin_menu, "generate_menu_images", generate)
    created = call(setup, "POST", "/menu/images", json={"image_format": image_format}).json()
    result = finish(setup, created)
    assert result["status"] == "completed", result
    assert seen and len(result["result"]["files"]) == count
    assert result["result"]["recipe_count"] == 2
    primary = setup.client.get(result["result"]["url"])
    assert primary.status_code == 200
    assert primary.content[:2] == (b"PK" if image_format == "print" else b"\xff\xd8")
    for item in result["result"]["files"]:
        assert setup.client.get(item["url"]).content == image.getvalue()
    hidden_id = setup.rows["隐藏内容"]["id"]
    assert call(setup, "POST", "/menu/images", json={"recipe_ids": [hidden_id]}).status_code == 422


def test_menu_worker_rechecks_visibility_after_job_was_created(setup, monkeypatch):
    monkeypatch.setattr(admin_menu, "generate_menu_images", lambda *_: pytest.fail("must not render hidden/unpublished dishes"))
    recipe_id = setup.rows["青菜"]["id"]
    setup.ctx.store.set_recipe_publications({recipe_id: False})
    with pytest.raises(ValueError, match="下架或隐藏"):
        admin_menu._render_menu(setup.ctx, {"recipe_ids": [recipe_id]})


def test_pairing_issues_real_one_time_credential_but_link_does_not(setup):
    before = setup.ctx.store.list_devices()
    result = call(setup, "POST", "/menu/link", json={"base_url": "http://192.168.1.2:8765"})
    assert result.status_code == 200
    assert result.json()["qr"].startswith("data:image/png;base64,")
    assert "pairing_token" not in result.text
    pairing = call(setup, "POST", "/menu/pairing", json={"base_url": "http://192.168.1.2:8765"}).json()
    payload = json.loads(pairing["payload"])
    assert payload["base_url"] == "http://192.168.1.2:8765"
    assert setup.ctx.store.list_devices() == before
    device = setup.ctx.store.pair_device(payload["pairing_token"], "合成旧客户端")
    assert device["device_id"]
    assert call(setup, "POST", "/menu/pairing", json={"base_url": "javascript:alert(1)"}).status_code == 422
    assert call(setup, "POST", "/menu/pairing", json={"base_url": "http://user:pass@localhost"}).status_code == 422


def practice_payload(setup):
    return {"id": str(uuid.uuid4()), "recipe_id": setup.rows["青菜"]["id"], "cooked_on": "2026-10-05", "outcome": "success", "rating": 4, "notes": "初次尝试"}


def test_practice_edit_and_soft_delete_produce_sync_changes(setup):
    payload = practice_payload(setup)
    setup.ctx.store.admin_save_practice_log(payload)
    initial = setup.ctx.store.current_revision()
    listed = call(setup, "GET", "/menu/practice").json()
    assert listed["items"][0]["recipe_title"] == "青菜"
    changed = call(setup, "PUT", f"/menu/practice/{payload['id']}", json={"notes": "改成小火后成功", "rating": 5})
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    assert setup.ctx.store.current_revision() > initial
    assert call(setup, "PUT", f"/menu/practice/{payload['id']}", json={"id": "another"}).status_code == 422
    assert call(setup, "PUT", f"/menu/practice/{payload['id']}", json={"rating": True}).status_code == 422
    deleted = call(setup, "DELETE", f"/menu/practice/{payload['id']}")
    assert deleted.json()["deleted_at"]
    assert call(setup, "GET", "/menu/practice").json()["total"] == 0
    assert setup.ctx.store.list_practice_logs(include_deleted=True)[0]["notes"] == "改成小火后成功"
    assert call(setup, "GET", "/menu/media/" + "a" * 64).status_code == 404


@pytest.mark.parametrize("resolution", ["server", "incoming", "merged"])
def test_sync_conflicts_can_keep_server_incoming_or_merge(setup, resolution):
    payload = practice_payload(setup)
    setup.ctx.store.admin_save_practice_log(payload)
    setup.ctx.store.admin_update_practice(payload["id"], {"notes": "服务器新版"})
    device = setup.ctx.store.join_device("合成离线手机")
    incoming = {**payload, "notes": "离线心得"}
    setup.ctx.store.sync(device["device_id"], 0, [{"op_id": str(uuid.uuid4()), "entity_type": "practice_log", "entity_id": payload["id"], "action": "upsert", "base_version": 1, "payload": incoming}])
    conflicts = call(setup, "GET", "/menu/conflicts").json()["items"]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["recipe_title"] == "青菜"
    assert conflict["server"]["notes"] == "服务器新版"
    assert conflict["incoming"]["notes"] == "离线心得"
    result = call(setup, "POST", f"/menu/conflicts/{conflict['id']}/resolve", json={"resolution": resolution, "updates": {"notes": "合并后的心得"}})
    assert result.status_code == 200, result.text
    expected = {"server": "服务器新版", "incoming": "离线心得", "merged": "合并后的心得"}[resolution]
    assert setup.ctx.store.list_practice_logs()[0]["notes"] == expected
    assert call(setup, "GET", "/menu/conflicts").json()["items"] == []
    assert call(setup, "POST", f"/menu/conflicts/{conflict['id']}/resolve", json={"resolution": resolution}).status_code == 404
