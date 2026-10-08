from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from bili_recipe_notes.admin_common import AdminContext, require_admin_request
from bili_recipe_notes.admin_curation import create_router
from bili_recipe_notes.mobile_api import create_app


def recipe_fixture(root: Path, name: str, *, title="番茄炒蛋", ingredient="鸡蛋"):
    folder = root / "测试厨房" / name
    folder.mkdir(parents=True)
    recipe = {"title": title, "category": "家常菜", "creator_name": "测试厨房", "source_url": f"https://www.bilibili.com/video/{name}",
              "ingredients": [{"name": ingredient, "amount": "2个"}], "seasonings": [], "tools": [], "summary_tips": [], "uncertain_points": [],
              "steps": [{"title": "翻炒", "start_time": 1, "action": "翻炒鸡蛋", "evidence": "下锅翻炒"}]}
    (folder / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    (folder / "note.md").write_text("# 原始笔记\n下锅翻炒。", encoding="utf-8")
    (folder / "source.json").write_text(json.dumps({"bvid": name, "duration": 180}), encoding="utf-8")
    (folder / "transcript.json").write_text('[{"start":1,"text":"下锅翻炒"}]', encoding="utf-8")
    return folder


@pytest.fixture
def workspace(tmp_path, authorize_client):
    root = tmp_path / "data" / "recipes"
    folders = [recipe_fixture(root, "BV1aaa"), recipe_fixture(root, "BV1bbb", ingredient="番茄")]
    application = create_app(tmp_path, out_dir=root, start_background_indexer=False)
    application.state.store.index_recipes()
    context = AdminContext(application.state.store, application.state.indexer)
    # Exercise this focused router independently of composition being developed.
    app = FastAPI()
    app.include_router(create_router(context), dependencies=[Depends(require_admin_request)])
    with TestClient(app) as client:
        authorize_client(client, tmp_path)
        yield tmp_path, root, folders, context, client


def originals(folders):
    return {path: path.read_bytes() for folder in folders for path in folder.rglob("*") if path.is_file()}


def test_listing_and_explicit_scan_preserve_all_recipe_bytes_and_manual_decisions(workspace):
    _, root, folders, _, client = workspace
    before = originals(folders)
    assert client.get("/curation").json()["groups"] == []
    assert not (root / "curation-review").exists()
    response = client.post("/curation/scan")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    comparison = client.get("/curation/group", params={"title": "番茄炒蛋"}).json()
    assert len(comparison["items"]) == 2
    assert {item["recipe"]["ingredients"][0]["name"] for item in comparison["items"]} == {"鸡蛋", "番茄"}
    item = comparison["items"][0]
    assert item["recipe_id"]
    payload = {"item_id": item["item_id"], "decision": "keep_variant", "final_title": "番茄炒蛋", "variant_name": "家常版", "review_notes": "不同配比应保留", "revision": comparison["revision"], "content_revision": item["content_revision"]}
    saved = client.post("/curation/decisions", json=payload)
    assert saved.status_code == 200, saved.text
    assert client.post("/curation/decisions", json=payload).status_code == 409
    assert client.post("/curation/scan").status_code == 200
    updated = client.get("/curation/group", params={"title": "番茄炒蛋"}).json()
    retained = next(value for value in updated["items"] if value["item_id"] == item["item_id"])
    assert retained["saved"]["variant_name"] == "家常版"
    assert retained["decision"] == "keep_variant"
    assert originals(folders) == before
    assert client.get("/curation/export?kind=decisions").json()["items"][item["item_id"]]["review_notes"] == "不同配比应保留"
    assert "attachment" in client.get("/curation/export").headers["content-disposition"]


def test_stale_source_blocks_decision_and_only_exposes_indexed_recipe_ids(workspace):
    _, _, folders, _, client = workspace
    client.post("/curation/scan")
    group = client.get("/curation/group", params={"title": "番茄炒蛋"}).json()
    item = group["items"][0]
    folder = next(folder for folder in folders if folder.name == item["item_id"])
    (folder / "transcript.json").write_text('[{"text":"字幕已更正"}]')
    saved = client.post("/curation/decisions", json={"item_id": item["item_id"], "decision": "keep_primary", "revision": group["revision"], "content_revision": item["content_revision"]})
    assert saved.status_code == 409
    assert client.get("/curation/group?title=missing").status_code == 404
    assert "output_folder" not in item
    assert client.post("/curation/decisions", json={"item_id": "../outside", "decision": "keep_primary", "revision": group["revision"], "content_revision": "anything"}).status_code == 404


def test_draft_filters_pagination_and_background_archive_use_saved_revision(workspace, monkeypatch):
    project, _, folders, context, client = workspace
    before = originals(folders)
    response = client.get("/drafts?status=draft&page_size=1").json()
    assert response["total"] == 2
    assert response["counts"]["draft"] == 2
    assert len(response["items"]) == 1
    item = response["items"][0]
    assert client.get("/drafts?status=archived").json()["total"] == 0
    assert client.get("/drafts?q=不存在").json()["total"] == 0
    assert client.get("/drafts?page=0").status_code == 422
    assert originals(folders) == before
    calls = []
    from bili_recipe_notes import admin_settings

    monkeypatch.setattr(admin_settings, "start_settings_operation", lambda ctx, kind, arguments, *, items: calls.append((ctx, kind, arguments, items)) or {"id": "test-background"})
    archived = client.post("/drafts/archive", json={"items": [{"id": item["id"], "revision": item["revision"]}]})
    assert archived.json() == {"id": "test-background"}
    assert calls[0][0] is context
    assert calls[0][1:3] == ("recipe_archive", {})
    assert calls[0][3] == [{"recipe_id": item["id"], "revision": item["revision"]}]
    assert client.post("/drafts/archive", json={"items": [{"id": item["id"], "revision": "old"}]}).status_code == 409
    assert len(calls) == 1


def test_two_primary_versions_are_reported_without_merging(workspace):
    _, _, folders, _, client = workspace
    before = originals(folders)
    client.post("/curation/scan")
    for position in range(2):
        group = client.get("/curation/group", params={"title": "番茄炒蛋"}).json()
        item = group["items"][position]
        assert client.post("/curation/decisions", json={"item_id": item["item_id"], "decision": "keep_primary", "final_title": "番茄炒蛋", "revision": group["revision"], "content_revision": item["content_revision"]}).status_code == 200
    report = client.get("/curation?status=completed").json()
    assert report["completed"] == 2
    assert len(report["conflicts"]) == 1
    assert "2 个主版本" in report["conflicts"][0]
    assert client.get("/curation?status=pending").json()["groups"] == []
    assert originals(folders) == before
