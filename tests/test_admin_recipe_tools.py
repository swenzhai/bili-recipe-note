from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from bili_recipe_notes import admin_recipe_tools as tools
from bili_recipe_notes.admin_common import AdminContext, require_admin_request
from bili_recipe_notes.config import UIConfig, save_config
from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.storage import atomic_write_json, atomic_write_text


@pytest.fixture
def setup(tmp_path, monkeypatch, authorize_client):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "recipes"
    folder = root / "up/eggs"
    folder.mkdir(parents=True)
    recipe = {
        "title": "炒鸡蛋", "source_url": "https://www.bilibili.com/video/BV13p411d7oQ", "video_title": "完整做菜视频",
        "ingredients": [{"name": "鸡蛋", "amount": "2个"}], "seasonings": [], "tools": [],
        "steps": [{"title": "翻炒", "start_time": 2.0, "action": "中火炒熟"}], "summary_tips": [], "uncertain_points": [],
        "taste_rating": 5, "creator_name": "厨师", "cover_image_status": "unavailable",
    }
    atomic_write_json(folder / "recipe.json", recipe)
    atomic_write_json(folder / "source.json", {"title": "完整视频", "uploader": "厨师"})
    atomic_write_json(folder / "transcript.json", [{"start": 0.0, "end": 10.0, "text": "准备两个鸡蛋，中火炒熟。"}])
    atomic_write_json(folder / "recipe.ai-review.json", {"visibility_override": "visible", "manual_review": {"keep": True}})
    atomic_write_text(folder / "note.md", "原来的笔记\n")
    save_config(UIConfig(out_dir=str(root), llm_provider="local", local_llm_command="never run a model"), tmp_path)
    store = MobileSyncStore(tmp_path, out_dir=root)
    store.index_recipes()
    ctx = AdminContext(store, SimpleNamespace())
    recipe_id = ctx.rows()[0]["id"]
    app = FastAPI()
    app.include_router(tools.create_router(ctx), prefix="/api/admin/v1", dependencies=[Depends(require_admin_request)])
    calls = []
    def enqueue(context, kind, arguments):
        calls.append((kind, arguments))
        return {"id": "synthetic", "status": "completed", "result": tools.run_tool_operation(context, arguments)}
    monkeypatch.setattr(tools, "enqueue_settings_operation", enqueue)
    def never(*args, **kwargs): pytest.fail("unexpected model or download")
    monkeypatch.setattr(tools.optimizer, "optimize_existing_note", never)
    monkeypatch.setattr(tools.content_analysis, "analyze_video_content", never)
    monkeypatch.setattr(tools.pipeline, "recapture_step_screenshot", never)
    return SimpleNamespace(ctx=ctx, folder=folder, root=root, id=recipe_id, client=authorize_client(TestClient(app), tmp_path), calls=calls)


def run(setup, action, **arguments):
    payload = {"revision": tools.tools_revision(setup.folder), "confirmed": True, **arguments}
    return setup.client.post(f"/api/admin/v1/recipes/{setup.id}/tools/{action}", json=payload)


def test_explicit_confirmation_and_stale_revision_reject_before_task_starts(setup):
    baseline = tools.tools_revision(setup.folder)
    response = run(setup, "rewrite", confirmed=False)
    assert response.status_code == 422
    atomic_write_text(setup.folder / "note.md", "另一个编辑页修改的笔记")
    response = run(setup, "rewrite", revision=baseline)
    assert response.status_code == 409
    assert setup.calls == []
    assert run(setup, "unknown").status_code == 404


def test_note_rewrite_uses_private_copy_and_backs_up_original(setup, monkeypatch):
    original_recipe = (setup.folder / "recipe.json").read_bytes()
    original_review = (setup.folder / "recipe.ai-review.json").read_bytes()
    calls = []
    def rewrite(folder, **options):
        calls.append((folder, options))
        atomic_write_text(folder / "note.md", "整理好的笔记")
        atomic_write_json(folder / "quality.json", {"score": 90})
        return SimpleNamespace(stage_errors=[])
    monkeypatch.setattr(tools.pipeline, "regenerate_note_from_recipe", rewrite)
    response = run(setup, "rewrite")
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert calls[0][0] != setup.folder and not calls[0][0].exists()
    assert calls[0][1]["llm_provider"] == "local"
    assert (setup.root / result["backup"] / "note.md").read_text() == "原来的笔记\n"
    assert (setup.folder / "note.md").read_text() == "整理好的笔记"
    assert (setup.folder / "recipe.json").read_bytes() == original_recipe
    assert (setup.folder / "recipe.ai-review.json").read_bytes() == original_review


def test_edit_during_model_run_is_not_overwritten(setup, monkeypatch):
    def optimize(folder, options):
        atomic_write_text(folder / "note.md", "AI 作业结果")
        atomic_write_json(folder / "quality.json", {"score": 95})
        atomic_write_text(setup.folder / "note.md", "用户在另一个页面保存的修改")
        return SimpleNamespace(quality_before=SimpleNamespace(score=60), quality_after=SimpleNamespace(score=95))
    monkeypatch.setattr(tools.optimizer, "optimize_existing_note", optimize)
    response = run(setup, "optimize")
    assert response.status_code == 409
    assert (setup.folder / "note.md").read_text() == "用户在另一个页面保存的修改"
    assert not (setup.folder / "quality.json").exists()


def test_failed_service_leaves_all_originals_unchanged(setup, monkeypatch):
    original = {file.name: file.read_bytes() for file in setup.folder.iterdir() if file.is_file()}
    def optimize(folder, options):
        atomic_write_text(folder / "note.md", "部分结果")
        raise RuntimeError("模型失败")
    monkeypatch.setattr(tools.optimizer, "optimize_existing_note", optimize)
    with pytest.raises(RuntimeError, match="模型失败"):
        run(setup, "optimize")
    assert original == {name: (setup.folder / name).read_bytes() for name in original}


def test_transcript_reextract_validates_then_updates_only_explicit_outputs(setup):
    old_ai = (setup.folder / "recipe.ai-review.json").read_bytes()
    old_source = (setup.folder / "source.json").read_bytes()
    assert run(setup, "reextract", transcript=[{"start": 10, "end": 2, "text": "不合法"}]).status_code == 422
    assert setup.calls == []
    response = run(setup, "reextract", transcript=[{"start": 0, "end": 10, "text": "准备三个鸡蛋，中火炒熟。"}])
    assert response.status_code == 200, response.text
    assert "三个鸡蛋" in (setup.folder / "transcript.json").read_text()
    assert (setup.folder / "recipe.ai-review.json").read_bytes() == old_ai
    assert (setup.folder / "source.json").read_bytes() == old_source
    assert (setup.root / response.json()["result"]["backup"] / "transcript.json").is_file()


def test_step_upload_clear_and_candidates_keep_images_inside_library(setup, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), "red").save(buffer, format="PNG")
    content = buffer.getvalue()
    original_review = (setup.folder / "recipe.ai-review.json").read_bytes()
    response = run(setup, "upload-image", content=base64.b64encode(content).decode())
    assert response.status_code == 200, response.text
    recipe = json.loads((setup.folder / "recipe.json").read_text())
    assert recipe["steps"][0]["screenshot_path"] == "images/step_01.jpg"
    assert (setup.folder / "images/step_01.jpg").is_file()
    response = run(setup, "clear-image")
    assert response.status_code == 200
    assert json.loads((setup.folder / "recipe.json").read_text())["steps"][0]["screenshot_status"] == "none"
    # Original image bytes remain available as an unreferenced original and in
    # backups; the client sees the recipe's explicit no-image decision.
    monkeypatch.setattr(tools.pipeline, "suggest_step_screenshots", lambda *args, **kwargs: [tools.ScreenshotCandidate(timestamp=3.5, score=0.8, content=content)])
    response = run(setup, "candidates", confirmed=False)
    candidate = response.json()["result"]["candidates"][0]
    assert setup.client.get(candidate["url"]).status_code == 200
    assert run(setup, "choose-image", candidate=candidate["id"]).status_code == 200
    assert json.loads((setup.folder / "recipe.json").read_text())["steps"][0]["screenshot_time"] == 3.5
    assert run(setup, "choose-image", candidate=candidate["id"]).status_code == 409
    assert (setup.folder / "recipe.ai-review.json").read_bytes() == original_review


def test_legacy_review_application_preserves_ai_sidecar_and_current_metadata(setup):
    original_ai = (setup.folder / "recipe.ai-review.json").read_bytes()
    assert run(setup, "create-review").status_code == 200
    detail = setup.client.get(f"/api/admin/v1/recipes/{setup.id}/tools").json()
    assert detail["legacy_current"]
    assert run(setup, "review-item", item_id="ingredients:0", decision="edited", value={"name": "鸡蛋", "amount": "3个"}, comment="人工核对").status_code == 200
    assert run(setup, "review-item", item_id="steps:0", decision="accepted").status_code == 200
    response = run(setup, "apply-review")
    assert response.status_code == 200, response.text
    result = json.loads((setup.folder / "recipe.json").read_text())
    assert result["ingredients"][0]["amount"] == "3个"
    assert result["taste_rating"] == 5 and result["cover_image_status"] == "unavailable" and result["creator_name"] == "厨师"
    assert (setup.folder / "recipe.ai-review.json").read_bytes() == original_ai
    assert (setup.root / response.json()["result"]["backup"] / "recipe.review.json").is_file()


def test_legacy_review_cannot_apply_to_changed_core_content(setup):
    assert run(setup, "create-review").status_code == 200
    recipe = json.loads((setup.folder / "recipe.json").read_text())
    recipe["ingredients"][0]["amount"] = "8个"
    atomic_write_json(setup.folder / "recipe.json", recipe)
    assert run(setup, "review-item", item_id="ingredients:0", decision="accepted").status_code == 409
    assert run(setup, "apply-review").status_code == 409
    assert json.loads((setup.folder / "recipe.json").read_text())["ingredients"][0]["amount"] == "8个"


def test_repair_does_not_follow_absolute_or_parent_image_paths(setup):
    outside = setup.root.parent / "outside.jpg"
    outside.write_bytes(b"untouched")
    recipe = json.loads((setup.folder / "recipe.json").read_text())
    recipe["steps"][0]["screenshot_path"] = str(outside)
    atomic_write_json(setup.folder / "recipe.json", recipe)
    response = setup.client.get(f"/api/admin/v1/recipes/{setup.id}/tools")
    assert response.status_code == 400
    assert outside.read_bytes() == b"untouched"


def test_analysis_is_separate_and_metadata_uses_portable_path(setup, monkeypatch):
    recipe = (setup.folder / "recipe.json").read_bytes()
    def analyze(folder, request, options):
        atomic_write_text(folder / "extra_analysis.md", "# 技巧\n根据字幕保持中火")
        atomic_write_json(folder / "extra_analysis.json", {"analysis_path": str(folder / "extra_analysis.md"), "request": request})
        return SimpleNamespace(markdown="# 技巧\n根据字幕保持中火")
    monkeypatch.setattr(tools.content_analysis, "analyze_video_content", analyze)
    response = run(setup, "analysis", request="分析火候")
    assert response.status_code == 200
    assert (setup.folder / "recipe.json").read_bytes() == recipe
    assert json.loads((setup.folder / "extra_analysis.json").read_text())["analysis_path"] == "extra_analysis.md"


@pytest.mark.parametrize("kind,suffix", [("markdown", ".md"), ("obsidian", ".md"), ("pdf", ".pdf"), ("docx", ".docx"), ("zip", ".zip")])
def test_single_recipe_exports_reuse_services_and_never_change_original_files(setup, kind, suffix):
    import zipfile
    images = setup.folder / "images"
    images.mkdir()
    Image.new("RGB", (320, 240), "orange").save(images / "step.jpg")
    atomic_write_text(setup.folder / "note.md", "# 炒鸡蛋\n\n准备两个鸡蛋，中火炒熟。\n\n![步骤](images/step.jpg)\n")
    original = {path.relative_to(setup.folder): path.read_bytes() for path in setup.folder.rglob("*") if path.is_file()}
    response = run(setup, "export", format=kind, confirmed=False)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["name"].startswith("炒鸡蛋") and result["name"].endswith(suffix)
    assert result["url"] == f"/api/admin/v1/downloads/{result['id']}"
    output = setup.ctx.store.project_root / "data/exports/admin-downloads" / (result["id"] + suffix)
    data = output.read_bytes()
    if kind == "markdown":
        assert data == original[Path("note.md")]
    elif kind == "obsidian":
        assert data.startswith(b"---\n") and original[Path("note.md")] in data
    elif kind == "pdf":
        assert data.startswith(b"%PDF-")
    elif kind == "docx":
        with zipfile.ZipFile(output) as archive:
            assert "word/document.xml" in archive.namelist()
    elif kind == "zip":
        with zipfile.ZipFile(output) as archive:
            assert archive.read("images/step.jpg") == original[Path("images/step.jpg")]
            assert archive.read("recipe.json") == original[Path("recipe.json")]
            assert archive.read("recipe.ai-review.json") == original[Path("recipe.ai-review.json")]
            assert not any(name.startswith("exports/") for name in archive.namelist())
    assert original == {path.relative_to(setup.folder): path.read_bytes() for path in setup.folder.rglob("*") if path.is_file()}
    assert not list((setup.root / ".admin-tools").glob("work-*"))


@pytest.mark.parametrize("kind,missing", [("pdf", "reportlab.platypus"), ("docx", "docx")])
def test_document_export_dependency_failure_is_explained_and_keeps_existing_fallback(setup, monkeypatch, kind, missing):
    import sys
    monkeypatch.setitem(sys.modules, missing, None)
    response = run(setup, "export", format=kind, confirmed=False)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["url"]
    assert "基础文字版" in result["warnings"][0] and "不包含图片" in result["warnings"][0]


def test_export_does_not_follow_unreferenced_image_symlinks(setup):
    images = setup.folder / "images"
    images.mkdir()
    external = setup.root.parent / "private.jpg"
    external.write_bytes(b"private data")
    (images / "linked.jpg").symlink_to(external)
    response = run(setup, "export", format="zip", confirmed=False)
    assert response.status_code == 400
    assert external.read_bytes() == b"private data"
    assert not (setup.ctx.store.project_root / "data/exports/admin-downloads").exists()
