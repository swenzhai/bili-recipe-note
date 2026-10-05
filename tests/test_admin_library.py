from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from bili_recipe_notes.mobile_api import create_app
from bili_recipe_notes.review_policy import set_ai_review_visibility

HEADERS = {"X-Admin-Request": "1"}
ROOT = "/api/admin/v1"


def make_recipe(root: Path, name: str = "番茄炒蛋", bvid: str = "BV13p411d7oQ") -> Path:
    folder = root / "测试厨房" / name
    (folder / "images").mkdir(parents=True)
    recipe = {
        "title": name, "source_url": f"https://www.bilibili.com/video/{bvid}", "creator_name": "测试厨房",
        "category": "家常菜", "cuisine": "家常", "ingredients": [{"name": "鸡蛋", "amount": "2个"}],
        "seasonings": [], "tools": ["炒锅"], "summary_tips": [], "uncertain_points": [],
        "steps": [{"title": "翻炒", "start_time": 0, "action": "中火炒熟", "screenshot_path": "images/step.jpg"}],
    }
    (folder / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    (folder / "transcript.json").write_text('[{"start":0,"text":"准备两个鸡蛋，中火炒熟。"}]', encoding="utf-8")
    (folder / "source.json").write_text('{"title":"赞助节目中的完整做菜教程"}', encoding="utf-8")
    (folder / "note.md").write_text("# 菜谱原始笔记\n保留字节", encoding="utf-8")
    (folder / "recipe.review.json").write_bytes(b'{"original":true}')
    Image.new("RGB", (800, 600), "orange").save(folder / "images" / "step.jpg")
    return folder


@pytest.fixture
def library(tmp_path):
    recipes = tmp_path / "data" / "recipes"
    folder = make_recipe(recipes)
    app = create_app(tmp_path, out_dir=recipes, start_background_indexer=False)
    with TestClient(app) as client:
        client.headers.update(HEADERS)
        recipe_id = client.get(ROOT + "/recipes").json()["items"][0]["id"]
        yield app, client, folder, recipe_id


def test_browser_requests_require_same_origin_and_cannot_fall_through_to_spa(library):
    _, client, _, recipe_id = library
    assert client.get(ROOT + "/overview", headers={"X-Admin-Request": ""}).status_code == 403
    assert client.post(ROOT + "/recipes/publication", headers={"Origin": "https://untrusted.example"}, json={"ids": [recipe_id], "published": False}).status_code == 403
    assert client.get(ROOT + "/recipes", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get(ROOT + "/overview", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.get(ROOT + "/recipes/missing").status_code == 404
    assert client.get(ROOT + "/not-a-route").status_code == 404


def test_browse_is_readonly_uses_index_and_renders_safe_media(library, monkeypatch):
    app, client, folder, recipe_id = library
    before = {path.relative_to(folder): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    monkeypatch.setattr(app.state.store, "index_recipes", lambda: pytest.fail("Browsing must not rescan the library"))
    response = client.get(ROOT + "/recipes?q=鸡蛋&page_size=1")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    detail = client.get(ROOT + f"/recipes/{recipe_id}").json()
    assert detail["recipe"]["title"] == "番茄炒蛋"
    assert detail["transcript"][0]["text"] == "准备两个鸡蛋，中火炒熟。"
    assert detail["step_images"][0]["index"] == 0
    assert client.get(detail["cover_url"], headers={"X-Admin-Request": ""}).status_code == 200
    assert client.get(ROOT + f"/recipes/{recipe_id}/media/step-999").status_code == 404
    assert client.get(ROOT + "/overview").json()["counts"]["total"] == 1
    assert {path.relative_to(folder): path.read_bytes() for path in folder.rglob("*") if path.is_file()} == before


def test_hidden_on_first_scan_can_be_found_and_restored_without_publishing(tmp_path):
    root = tmp_path / "data" / "recipes"
    folder = make_recipe(root)
    set_ai_review_visibility(folder, "hidden")
    app = create_app(tmp_path, out_dir=root, start_background_indexer=False)
    with TestClient(app) as client:
        client.headers.update(HEADERS)
        assert not client.get(ROOT + "/recipes").json()["items"]
        hidden = client.get(ROOT + "/recipes?visibility=hidden").json()["items"]
        assert len(hidden) == 1
        recipe_id = hidden[0]["id"]
        detail = client.get(ROOT + f"/recipes/{recipe_id}").json()
        restored = client.post(ROOT + f"/recipes/{recipe_id}/review", json={"action": "visibility", "visibility": "visible", "content_fingerprint": detail["review"]["content_fingerprint"]})
        assert restored.status_code == 200
        assert client.get(ROOT + "/recipes").json()["items"][0]["hidden"] is False
        assert client.post(ROOT + "/recipes/publication", json={"ids": [recipe_id], "published": False}).status_code == 200
        for visibility in ("hidden", "visible"):
            assert client.post(ROOT + f"/recipes/{recipe_id}/review", json={"action": "visibility", "visibility": visibility, "content_fingerprint": detail["review"]["content_fingerprint"]}).status_code == 200
        assert client.get(ROOT + "/recipes").json()["items"][0]["published"] is False


def test_manual_review_requires_evidence_and_preserves_originals(library):
    _, client, folder, recipe_id = library
    originals = {path.relative_to(folder): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    detail = client.get(ROOT + f"/recipes/{recipe_id}").json()
    endpoint = ROOT + f"/recipes/{recipe_id}/review"
    payload = {"content_fingerprint": detail["review"]["content_fingerprint"]}
    assert client.post(endpoint, json={**payload, "action": "classification", "classification": "recipe"}).status_code == 200
    assert client.post(endpoint, json={**payload, "action": "complete"}).status_code == 422
    assert client.post(endpoint, json={**payload, "action": "item", "item_id": "ingredients:0", "verdict": "supported"}).status_code == 422
    assert client.post(endpoint, json={**payload, "action": "item", "item_id": "ingredients:0", "verdict": "supported", "evidence": [{"text": "伪造引用"}]}).status_code == 422
    for item_id in ("ingredients:0", "steps:0"):
        response = client.post(endpoint, json={**payload, "action": "item", "item_id": item_id, "verdict": "supported", "evidence": [{"text": "准备两个鸡蛋，中火炒熟。", "start": 0}]})
        assert response.status_code == 200, response.text
    assert client.post(endpoint, json={**payload, "action": "complete"}).json()["review"]["manual_completed"]
    assert client.get(ROOT + f"/recipes/{recipe_id}/review-history").json()["items"]
    for relative, content in originals.items():
        assert (folder / relative).read_bytes() == content
    (folder / "transcript.json").write_text('[{"text":"已经变更"}]')
    assert client.post(endpoint, json={**payload, "action": "complete"}).status_code == 409


def test_edit_conflicts_backups_and_atomic_note_revision(library):
    _, client, folder, recipe_id = library
    detail = client.get(ROOT + f"/recipes/{recipe_id}").json()
    original = (folder / "recipe.json").read_bytes()
    response = client.put(ROOT + f"/recipes/{recipe_id}", json={"revision": detail["revision"], "recipe": {"title": "滑炒鸡蛋"}, "regenerate_note": True})
    assert response.status_code == 200, response.text
    assert response.json()["recipe"]["title"] == "滑炒鸡蛋"
    assert (folder / "recipe.json.bak").read_bytes() == original
    assert "滑炒鸡蛋" in (folder / "note.md").read_text()
    assert client.put(ROOT + f"/recipes/{recipe_id}", json={"revision": detail["revision"], "recipe": {"title": "旧页面覆盖"}}).status_code == 409
    latest = response.json()["revision"]
    (folder / "note.md").write_text("用户已手工修改笔记")
    assert client.put(ROOT + f"/recipes/{recipe_id}", json={"revision": latest, "recipe": {}, "note": "过期正文"}).status_code == 409


def test_upload_crop_confirm_and_stale_candidates(library):
    _, client, folder, recipe_id = library
    original = (folder / "recipe.json").read_bytes()
    image = io.BytesIO()
    Image.new("RGB", (1000, 800), "red").save(image, format="PNG")
    response = client.post(ROOT + f"/recipes/{recipe_id}/cover-upload", json={"content": base64.b64encode(image.getvalue()).decode()})
    assert response.status_code == 200, response.text
    assert (folder / "recipe.json").read_bytes() == original
    candidate = response.json()
    assert client.get(candidate["url"]).status_code == 200
    detail = client.get(ROOT + f"/recipes/{recipe_id}").json()
    saved = client.post(ROOT + f"/recipes/{recipe_id}/cover", json={"revision": detail["revision"], "source": "candidate-" + candidate["id"], "crop": {"left": 100, "top": 100, "width": 800, "height": 600}})
    assert saved.status_code == 200, saved.text
    recipe = saved.json()["recipe"]
    assert recipe["cover_image_status"] == "manual_crop"
    assert recipe["cover_crop_box"]["left"] == 100
    assert Image.open(folder / "images" / "cover.jpg").size == (800, 600)
    (folder / "transcript.json").write_text('[{"text":"全新来源内容"}]')
    assert not client.get(ROOT + f"/recipes/{recipe_id}/cover-candidates").json()["items"]
    assert client.post(ROOT + f"/recipes/{recipe_id}/cover", json={"revision": saved.json()["revision"], "source": "candidate-" + candidate["id"]}).status_code == 409


def test_media_symlink_cannot_read_outside_library(library, tmp_path):
    _, client, folder, recipe_id = library
    outside = tmp_path / "private.txt"
    outside.write_text("not an image")
    (folder / "images" / "step.jpg").unlink()
    (folder / "images" / "step.jpg").symlink_to(outside)
    response = client.get(ROOT + f"/recipes/{recipe_id}/media/step-0")
    assert response.status_code == 400
    assert "not an image" not in response.text


def test_unavailable_cover_does_not_fall_back_to_steps_or_remain_pending(library):
    _, client, _, recipe_id = library
    detail = client.get(ROOT + f"/recipes/{recipe_id}").json()
    response = client.post(ROOT + f"/recipes/{recipe_id}/cover", json={"revision": detail["revision"], "source": "unavailable"})
    assert response.status_code == 200, response.text
    assert response.json()["cover_url"] is None
    assert client.get(ROOT + "/overview").json()["counts"]["cover_pending"] == 0
    assert client.get(ROOT + "/recipes?cover_status=confirmed").json()["total"] == 1
    assert client.get(ROOT + "/recipes?cover_status=pending").json()["total"] == 0
    assert client.get(ROOT + f"/recipes/{recipe_id}/media/cover").status_code == 404


def test_video_capture_rejects_source_changes_during_download(library, monkeypatch):
    from bili_recipe_notes import admin_library
    from bili_recipe_notes.screenshot import ScreenshotCandidate
    app, client, folder, recipe_id = library
    def capture(*args, **kwargs):
        (folder / "source.json").write_text('{"title":"已经切换到另一部视频"}')
        return ScreenshotCandidate(timestamp=2, score=1, content=(folder / "images" / "step.jpg").read_bytes())
    monkeypatch.setattr(admin_library, "capture_recipe_cover_candidate", capture)
    response = client.post(ROOT + f"/recipes/{recipe_id}/cover-candidates", json={"timestamp": 2})
    assert response.status_code == 409
    assert not list(app.state.store.out_dir.glob(".admin/cover-candidates/**/*.jpg"))


def test_media_lookup_does_not_materialize_whole_catalog(library, monkeypatch):
    app, client, _, recipe_id = library
    monkeypatch.setattr(app.state.store, "list_management_recipes", lambda: pytest.fail("A single thumbnail should not load the full catalog"))
    assert client.get(ROOT + f"/recipes/{recipe_id}/media/step-0").status_code == 200


@pytest.mark.parametrize("operation", ["edit", "cover", "review"])
def test_saved_changes_survive_index_failure_without_false_save_error(library, monkeypatch, operation):
    app, client, folder, recipe_id = library
    detail = client.get(ROOT + f"/recipes/{recipe_id}").json()
    def unavailable():
        raise OSError("temporary index failure")
    monkeypatch.setattr(app.state.store, "index_recipes", unavailable)
    if operation == "edit":
        response = client.put(ROOT + f"/recipes/{recipe_id}", json={"revision": detail["revision"], "recipe": {"title": "已保存的菜名"}})
        assert json.loads((folder / "recipe.json").read_text())["title"] == "已保存的菜名"
    elif operation == "cover":
        response = client.post(ROOT + f"/recipes/{recipe_id}/cover", json={"revision": detail["revision"], "source": "unavailable"})
        assert json.loads((folder / "recipe.json").read_text())["cover_image_status"] == "no_suitable"
    else:
        response = client.post(ROOT + f"/recipes/{recipe_id}/review", json={"action": "visibility", "visibility": "hidden", "content_fingerprint": detail["review"]["content_fingerprint"]})
        assert json.loads((folder / "recipe.ai-review.json").read_text())["visibility_override"] == "hidden"
    assert response.status_code == 200, response.text
    assert "已保存" in response.json()["sync_warning"]


def test_publication_batch_reads_catalog_once(library, monkeypatch):
    app, client, _, recipe_id = library
    original = app.state.store.list_management_recipes
    calls = []
    def catalog():
        calls.append(True)
        return original()
    monkeypatch.setattr(app.state.store, "list_management_recipes", catalog)
    response = client.post(ROOT + "/recipes/publication", json={"ids": [recipe_id, recipe_id], "published": False})
    assert response.status_code == 200
    assert len(calls) == 1


def test_archive_rejects_unseen_edits(library, monkeypatch):
    from bili_recipe_notes import admin_library
    _, client, folder, recipe_id = library
    version = client.get(ROOT + f"/recipes/{recipe_id}").json()["revision"]
    (folder / "note.md").write_text("另一位用户修改的笔记")
    monkeypatch.setattr(admin_library, "archive_recipe_to_obsidian", lambda *args, **kwargs: pytest.fail("stale draft must not archive"))
    assert client.post(ROOT + f"/recipes/{recipe_id}/archive", json={"revision": version}).status_code == 409


def test_ai_candidate_replacement_requires_refresh_even_with_same_pixels(library, monkeypatch):
    from bili_recipe_notes import admin_library
    app, client, folder, recipe_id = library
    directory = app.state.store.out_dir / ".frame-selection" / "synthetic"
    directory.mkdir(parents=True)
    image = directory / "one.png"
    Image.new("RGB", (800, 600), "red").save(image)
    current = {"status": "ok", "image_path": str(image), "run_id": "one", "timestamp": 2, "reason": "fake"}
    monkeypatch.setattr(admin_library, "load_frame_candidate", lambda *args, **kwargs: dict(current))
    detail = client.get(ROOT + f"/recipes/{recipe_id}").json()
    original = (folder / "recipe.json").read_bytes()
    current["run_id"] = "two"
    response = client.post(ROOT + f"/recipes/{recipe_id}/cover", json={"revision": detail["revision"], "source": "ai", "candidate_revision": detail["frame_candidate"]["candidate_revision"]})
    assert response.status_code == 409
    assert (folder / "recipe.json").read_bytes() == original
