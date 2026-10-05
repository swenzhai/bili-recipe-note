from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from bili_recipe_notes import frame_selection_runner, frame_selection_service, ui
from bili_recipe_notes.mobile_sync import MobileSyncStore


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BILI_RECIPE_DATA_DIR", raising=False)


def _recipe(root: Path) -> Path:
    folder = root / "测试作者" / "番茄炒蛋--test"
    folder.mkdir(parents=True)
    recipe = {
        "title": "番茄炒蛋", "source_url": "https://www.bilibili.com/video/BV13p411d7oQ",
        "video_title": "番茄炒蛋", "uploader": "测试作者", "servings": None,
        "total_time": None, "difficulty": None, "ingredients": [], "seasonings": [],
        "tools": [], "prep_items": [], "shopping_list": [], "steps": [],
        "summary_tips": [], "uncertain_points": [],
    }
    (folder / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    (folder / "note.md").write_text("# 原始菜谱\n", encoding="utf-8")
    (folder / "quality.json").write_text('{"score": 0, "issues": [], "summary": "test"}', encoding="utf-8")
    # Existing quick review indexes a stable mobile identity on first visit.
    MobileSyncStore(Path.cwd(), out_dir=root).index_recipes()
    return folder


def _open(root: Path, page="快速审核") -> AppTest:
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(root))
    at.selectbox(key="main_page").set_value(page)
    at.run()
    assert not at.exception
    return at


def _candidate(root: Path) -> dict:
    path = root / ".frame-selection" / "runs" / "test" / "best_frame.png"
    path.parent.mkdir(parents=True)
    image = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 80, 30)).save(image, format="PNG")
    path.write_bytes(image.getvalue())
    return {
        "status": "ok", "image_path": str(path), "timestamp": 126.0,
        "source_url": "https://www.bilibili.com/video/BV13p411d7oQ",
        "dimensions": [640, 480], "reason": "盛盘完成，主体清晰",
    }


def _snapshot(folder: Path) -> dict:
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


@pytest.mark.parametrize("page,key", [("快速审核", "quick_review_ai_frames"), ("本餐点菜", "meal_ai_frames")])
def test_main_entry_opens_batch_page_without_starting_model(tmp_path, monkeypatch, page, key):
    root = tmp_path / "recipes"
    folder = _recipe(root)
    before = _snapshot(folder)
    monkeypatch.setattr(frame_selection_runner, "start_frame_selection_batch", lambda *_a, **_kw: pytest.fail("导航不应启动模型"))
    at = _open(root, page)
    at.button(key=key).click().run()
    assert not at.exception
    assert at.selectbox(key="main_page").value == "AI 菜品选图"
    assert _snapshot(folder) == before


def test_current_recipe_start_is_explicit_and_scoped(tmp_path, monkeypatch):
    root = tmp_path / "recipes"
    folder = _recipe(root)
    calls = []

    def start(out_dir, **options):
        calls.append((out_dir, options))
        return {"batch_id": "frame-0123456789abcdef"}

    monkeypatch.setattr(frame_selection_runner, "start_frame_selection_batch", start)
    at = _open(root)
    selected = at.selectbox(key="quick_review_select").value
    at.button(key=f"mobile_cover_ai_start_{selected}").click().run()
    assert not at.exception
    assert len(calls) == 1
    assert Path(calls[0][0]) == root
    assert calls[0][1]["folders"] == [folder]
    assert calls[0][1]["limit"] == 1
    assert all(calls[0][1][key] is False for key in ("only_pending", "published_only", "skip_confirmed"))
    assert at.selectbox(key="main_page").value == "AI 菜品选图"
    assert at.session_state["_frame_selection_scope_folders"] == [str(folder)]


def test_current_recipe_start_reports_existing_batch_instead_of_claiming_queued(tmp_path, monkeypatch):
    root = tmp_path / "recipes"
    _recipe(root)
    monkeypatch.setattr(frame_selection_runner, "start_frame_selection_batch", lambda *_a, **_kw: {
        "batch_id": "frame-0123456789abcdef", "already_running": True,
    })
    at = _open(root)
    selected = at.selectbox(key="quick_review_select").value
    at.button(key=f"mobile_cover_ai_start_{selected}").click().run()
    assert not at.exception
    assert at.selectbox(key="main_page").value == "AI 菜品选图"
    assert any("本次点击未创建新任务" in warning.value for warning in at.warning)
    assert "_frame_selection_scope_folders" not in at.session_state


@pytest.mark.parametrize("stale", [False, True])
def test_candidate_requires_crop_confirmation_and_revalidates_source(tmp_path, monkeypatch, stale):
    import streamlit_cropper

    root = tmp_path / "recipes"
    folder = _recipe(root)
    candidate = _candidate(root)
    current = [candidate]
    monkeypatch.setattr(frame_selection_service, "load_frame_candidate", lambda *_a, **_kw: current[0])
    monkeypatch.setattr(streamlit_cropper, "st_cropper", lambda *_a, **_kw: {"left": 0, "top": 0, "width": 640, "height": 480})
    before = _snapshot(folder)
    at = _open(root)
    selected = at.selectbox(key="quick_review_select").value
    assert _snapshot(folder) == before
    at.button(key=f"mobile_cover_ai_crop_{selected}").click().run()
    assert not at.exception
    crop = at.session_state[f"mobile_cover_crop_{selected}"]
    assert crop["timestamp"] == 126.0
    assert crop["source_kind"] == "video_frame"
    assert crop["source_url"] == candidate["source_url"]
    assert crop["content"] == Path(candidate["image_path"]).read_bytes()
    assert _snapshot(folder) == before
    if stale:
        current[0] = None
    at.button(key=f"mobile_cover_crop_confirm_{selected}_{crop['token']}").click().run()
    assert not at.exception
    if stale:
        assert any("来源已变化" in error.value for error in at.error)
        assert _snapshot(folder) == before
    else:
        result = json.loads((folder / "recipe.json").read_text(encoding="utf-8"))
        assert result["cover_image_status"] == "manual_video"
        assert result["cover_image_time"] == 126.0
        assert result["cover_source_url"] == candidate["source_url"]
        assert result["cover_source_kind"] == "video_frame"
        assert (folder / result["cover_image_path"]).is_file()
        assert (folder / "note.md").read_bytes() == before["note.md"]


def test_no_match_preserves_manual_cover_choices(tmp_path, monkeypatch):
    root = tmp_path / "recipes"
    folder = _recipe(root)
    before = _snapshot(folder)
    monkeypatch.setattr(frame_selection_service, "load_frame_candidate", lambda *_a, **_kw: {"status": "not_found"})
    at = _open(root)
    assert any("本次采样没有找到" in message.value for message in at.info)
    assert any(button.label == "从原视频生成更多候选" for button in at.button)
    assert not any(button.label == "裁剪 AI 候选图" for button in at.button)
    assert _snapshot(folder) == before
