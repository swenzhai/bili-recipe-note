from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bili_recipe_notes import ai_review_ui
from bili_recipe_notes.history import scan_history
from bili_recipe_notes.review_policy import (
    AI_REVIEW_FILE_NAME, AI_REVIEW_PROMPT_VERSION, AI_REVIEW_RULES_VERSION,
    get_review_state, review_input_fingerprint, review_recipe_hash, set_ai_review_visibility,
)


@pytest.fixture(autouse=True)
def isolate_ui_data(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BILI_RECIPE_DATA_DIR", raising=False)


def _write_recipe(root: Path, name: str, *, hidden: bool = False) -> Path:
    folder = root / name
    folder.mkdir()
    recipe = {
        "title": name, "source_url": f"https://example.com/{name}",
        "ingredients": [], "seasonings": [], "tools": [], "steps": [],
        "summary_tips": [], "uncertain_points": [], "tags": ["原标签"],
    }
    (folder / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    (folder / "transcript.json").write_text('[{"start":0,"text":"今天带大家探店，品尝餐厅菜品"}]', encoding="utf-8")
    (folder / "note.md").write_text(f"# {name}\n原始生成内容\n", encoding="utf-8")
    if hidden:
        (folder / AI_REVIEW_FILE_NAME).write_text(json.dumps({
            "schema_version": 2, "status": "completed", "source_recipe_sha256": review_recipe_hash(folder),
            "input_fingerprint": review_input_fingerprint(folder),
            "rules_version": AI_REVIEW_RULES_VERSION, "prompt_version": AI_REVIEW_PROMPT_VERSION,
            "classification": "non_recipe", "visibility": "hidden", "confidence": 0.99,
            "tags": ["探店"], "reason": "仅探店，没有烹饪教学", "item_checks": [],
            "evidence": [{"text": "今天带大家探店，品尝餐厅菜品", "start": 0}],
        }, ensure_ascii=False), encoding="utf-8")
    return folder


def _open_page(out_dir: Path, **state) -> AppTest:
    at = AppTest.from_string(
        "import streamlit as st\n"
        "from bili_recipe_notes.ai_review_ui import render_ai_review_page\n"
        "from bili_recipe_notes.config import UIConfig\n"
        "render_ai_review_page(st, UIConfig(out_dir=st.session_state['test_out_dir']))\n",
        default_timeout=15,
    )
    at.session_state["test_out_dir"] = str(out_dir)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def test_ai_page_keeps_original_reports_read_only_and_can_restore_hidden(tmp_path: Path) -> None:
    folder = _write_recipe(tmp_path, "探店", hidden=True)
    before = {path: path.read_bytes() for path in folder.iterdir() if path.name != AI_REVIEW_FILE_NAME}
    at = _open_page(tmp_path)
    assert not at.exception
    assert at.metric[3].value == "1"
    assert at.button(key="ai_review_visibility").label == "人工恢复展示"
    at.button(key="ai_review_visibility").click().run()
    assert not at.exception
    assert at.metric[3].value == "0"
    review = json.loads((folder / AI_REVIEW_FILE_NAME).read_text())
    assert review["visibility_override"] == "visible"
    assert review["classification"] == "non_recipe"
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (folder / "quality.json").exists()


def test_batch_entry_passes_only_selected_folders_and_llm_options(tmp_path: Path, monkeypatch) -> None:
    selected = _write_recipe(tmp_path, "鸡蛋")
    _write_recipe(tmp_path, "米饭")
    requests = []

    def start(out_dir, **options):
        requests.append((out_dir, options))
        return {"batch_id": "test-batch"}

    monkeypatch.setattr(ai_review_ui, "start_ai_review_batch", start)
    at = _open_page(tmp_path, _ai_review_scope_folders=[str(selected)])
    assert not at.exception
    assert at.selectbox(key="ai_review_scope").value == "当前批次结果"
    at.number_input(key="ai_review_limit").set_value(10)
    at.button(key="ai_review_start").click().run()
    assert not at.exception
    assert requests[0][1]["folders"] == [str(selected)]
    assert requests[0][1]["only_pending"] is True
    assert requests[0][1]["limit"] == 10
    assert requests[0][1]["provider"] == "opencode"


def test_single_review_uses_background_queue_and_forces_recheck(tmp_path: Path, monkeypatch) -> None:
    folder = _write_recipe(tmp_path, "煎蛋")
    requests = []
    monkeypatch.setattr(ai_review_ui, "start_ai_review_batch", lambda out_dir, **kw: requests.append(kw) or {"batch_id": "single"})
    at = _open_page(tmp_path)
    at.button(key="ai_review_single").click().run()
    assert not at.exception
    assert requests[0]["folders"] == [str(folder)]
    assert requests[0]["only_pending"] is False


def test_ai_page_reports_damaged_recipe_without_crashing(tmp_path: Path) -> None:
    folder = _write_recipe(tmp_path, "损坏")
    (folder / "recipe.json").write_text("{", encoding="utf-8")
    at = _open_page(tmp_path)
    assert not at.exception
    assert at.error
    assert at.button(key="ai_review_single").disabled


def test_ai_page_shows_interrupted_progress_and_resumes(tmp_path: Path, monkeypatch) -> None:
    folder = _write_recipe(tmp_path, "鸡蛋")
    batch = {"batch_id": "paused-test", "status": "interrupted", "active": False, "total": 2,
             "completed": 1, "failed": 1, "items": [{"folder": str(folder), "status": "failed", "error": "LLM timeout"}]}
    monkeypatch.setattr(ai_review_ui, "list_ai_review_batches", lambda *args: [batch])
    monkeypatch.setattr(ai_review_ui, "get_ai_review_batch", lambda *args: batch)
    calls = []
    monkeypatch.setattr(ai_review_ui, "resume_ai_review_batch", lambda *args, **kwargs: calls.append((args, kwargs)))
    at = _open_page(tmp_path)
    assert not at.exception
    assert not at.button(key="ai_review_resume").disabled
    assert any("LLM timeout" in str(table.value) for table in at.dataframe)
    at.button(key="ai_review_retry").click().run()
    assert not at.exception
    assert calls[0][1]["retry_failed"] is True
    assert calls[0][1]["provider"] == "opencode"
    assert calls[0][1]["openai_model"]
    at.radio(key="ai_review_retry_config").set_value("原任务配置")
    at.button(key="ai_review_retry").click().run()
    assert calls[-1][1] == {"retry_failed": True}


def test_running_batch_disables_duplicate_start_and_can_request_pause(tmp_path: Path, monkeypatch) -> None:
    folder = _write_recipe(tmp_path, "鸡蛋")
    batch = {"batch_id": "running-test", "status": "running", "active": True, "total": 1,
             "completed": 0, "failed": 0, "current_folder": folder.name, "items": []}
    monkeypatch.setattr(ai_review_ui, "list_ai_review_batches", lambda *args: [batch])
    monkeypatch.setattr(ai_review_ui, "get_ai_review_batch", lambda *args: batch)
    calls = []
    monkeypatch.setattr(ai_review_ui, "request_stop_ai_review_batch", lambda *args: calls.append(args))
    at = _open_page(tmp_path)
    assert not at.exception
    assert at.button(key="ai_review_start").disabled
    assert at.button(key="ai_review_single").disabled
    at.button(key="ai_review_stop").click().run()
    assert not at.exception
    assert calls == [(str(tmp_path), "running-test")]


def test_history_combines_review_tags_without_rewriting_recipe(tmp_path: Path) -> None:
    folder = _write_recipe(tmp_path, "探店", hidden=True)
    original = (folder / "recipe.json").read_bytes()
    item = scan_history(tmp_path)[0]
    assert item.tags == ["原标签", "探店"]
    assert (folder / "recipe.json").read_bytes() == original


def test_manual_visibility_survives_stale_ai_and_is_recoverable_in_ui(tmp_path: Path) -> None:
    folder = _write_recipe(tmp_path, "探店", hidden=True)
    set_ai_review_visibility(folder, "hidden")
    recipe = json.loads((folder / "recipe.json").read_text())
    recipe["title"] = "新内容，待重审"
    (folder / "recipe.json").write_text(json.dumps(recipe), encoding="utf-8")
    at = _open_page(tmp_path)
    assert not at.exception
    assert at.metric[3].value == "1"
    assert at.button(key="ai_review_visibility").label == "人工恢复展示"
    at.button(key="ai_review_visibility").click().run()
    assert not at.exception
    assert at.metric[3].value == "0"


def test_manual_workbench_confirms_each_item_and_leaves_original_files_untouched(tmp_path: Path) -> None:
    folder = _write_recipe(tmp_path, "炒蛋")
    recipe = json.loads((folder / "recipe.json").read_text())
    recipe["ingredients"] = [{"name": "鸡蛋", "amount": "两个"}]
    recipe["steps"] = [{"title": "炒熟", "start_time": 2, "action": "中火炒熟"}]
    (folder / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    (folder / "transcript.json").write_text('[{"start":0,"text":"准备两个鸡蛋"},{"start":2,"text":"中火炒熟"}]', encoding="utf-8")
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    at = _open_page(tmp_path)
    next(widget for widget in at.selectbox if widget.label == "人工内容分类").set_value("recipe")
    next(widget for widget in at.button if widget.label == "保存人工分类").click().run()
    assert not at.exception
    for expected, quote in (("ingredients:0", "准备两个鸡蛋"), ("steps:0", "中火炒熟")):
        selector = next(widget for widget in at.selectbox if widget.label == "核对用料或步骤")
        assert selector.value == expected
        next(widget for widget in at.radio if widget.label == "人工核对结论").set_value("supported")
        next(widget for widget in at.text_area if widget.label == "引用字幕原文（可选）").set_value(quote)
        next(widget for widget in at.button if widget.label == "保存并核对下一项").click().run()
        assert not at.exception
        assert not at.error, [message.value for message in at.error]
    next(widget for widget in at.button if widget.label == "完成人工确认").click().run()
    assert not at.exception
    assert get_review_state(folder)["manual_completed"] is True
    assert all((folder / name).read_bytes() == content for name, content in before.items())
    assert list((tmp_path / ".ai-review" / "history").glob("*/*.json"))
    assert any(widget.label == "历史版本" for widget in at.selectbox)

    reopened = _open_page(tmp_path)
    assert not reopened.exception
    assert next(widget for widget in reopened.text_area if widget.label == "引用字幕原文（可选）").value == "准备两个鸡蛋"
    next(widget for widget in reopened.button if widget.label == "保存并核对下一项").click().run()
    assert not reopened.error
    assert get_review_state(folder)["item_decisions"]["ingredients:0"]["evidence"] == [{"text": "准备两个鸡蛋"}]


def test_workbench_handles_malformed_video_url(tmp_path: Path) -> None:
    folder = _write_recipe(tmp_path, "来源链接损坏")
    recipe = json.loads((folder / "recipe.json").read_text())
    recipe["source_url"] = "https://["
    (folder / "recipe.json").write_text(json.dumps(recipe), encoding="utf-8")
    at = _open_page(tmp_path)
    assert not at.exception


def test_next_pending_record_clears_search_and_moves_to_review_queue(tmp_path: Path) -> None:
    first = _write_recipe(tmp_path, "甲")
    second = _write_recipe(tmp_path, "乙")
    at = _open_page(tmp_path)
    at.text_input(key="ai_review_search").set_value("甲").run()
    assert at.selectbox(key="ai_review_record").value == str(first)
    next(widget for widget in at.button if widget.label == "下一条待人工确认").click().run()
    assert not at.exception
    assert at.text_input(key="ai_review_search").value == ""
    assert at.radio(key="ai_review_result_filter").value == "待人工确认"
    assert at.selectbox(key="ai_review_record").value == str(second)


def test_public_pages_hide_ai_classified_content_by_default(tmp_path: Path) -> None:
    from bili_recipe_notes import ui

    _write_recipe(tmp_path, "探店", hidden=True)
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(tmp_path))
    at.selectbox(key="main_page").set_value("菜谱库全览").run()
    assert not at.exception
    assert any("菜谱库还是空的" in message.value for message in at.info)
    at.checkbox(key="overview_include_hidden").check().run()
    assert not at.exception
    assert any("探店" in str(table.value) for table in at.dataframe)
    at.selectbox(key="main_page").set_value("AI 审核归类").run()
    assert not at.exception
    assert any(button.label == "开始 AI 批量审核" for button in at.button)
