from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import authenticate_streamlit_app

from bili_recipe_notes import dish_info, dish_info_ui


@pytest.fixture(autouse=True)
def isolated_ui(tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Keep login state outside the recipe fixture whose bytes are read-only.
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(tmp_path_factory.mktemp("dish-ui-state")))
    monkeypatch.setattr(dish_info, "complete_markdown_prompt", lambda *_a, **_k: pytest.fail("UI browsing must not invoke a model"))
    monkeypatch.setattr(dish_info, "search_dish_sources", lambda *_a, **_k: pytest.fail("UI browsing must not search"))
    for name in ("start_dish_info_batch", "stop_dish_info_batch", "resume_dish_info_batch"):
        monkeypatch.setattr(dish_info_ui, name, lambda *_a, **_k: pytest.fail("unexpected real task operation"))


def open_page(root: Path, **config) -> AppTest:
    app = AppTest.from_string(
        "import streamlit as st\n"
        "from bili_recipe_notes.dish_info_ui import render_dish_info_page\n"
        "from bili_recipe_notes.config import UIConfig\n"
        "render_dish_info_page(st, UIConfig(**st.session_state['config']))\n",
        default_timeout=15,
    )
    authenticate_streamlit_app(app)
    app.session_state["config"] = {"out_dir": str(root), "llm_provider": "codex", "codex_model": "demo-model", "codex_profile": "demo-profile", **config}
    return app.run()


def submit_button(app):
    return next(button for button in app.button if button.label == "开始 AI 补充菜品介绍")


def batch(**updates):
    return {
        "batch_id": "intro-example", "status": "paused", "active": False,
        "total": 5, "completed": 1, "failed": 1, "skipped": 1, "pending": 2,
        "created_at": "2026-10-01T10:00:00Z", "finished_at": "2026-10-01T10:02:00Z",
        "items": [
            {"folder": "厨师/炒蛋", "status": "completed", "search_status": "failed"},
            {"folder": "厨师/米饭", "status": "failed", "error": "LLM timeout"},
            {"folder": "厨师/面条", "status": "skipped"},
            {"folder": "厨师/豆腐", "status": "pending"},
            {"folder": "厨师/青菜", "status": "pending"},
        ], **updates,
    }


def test_browsing_is_read_only_and_defaults_to_ten_missing_without_web(tmp_path):
    folder = tmp_path / "厨师" / "菜谱"
    folder.mkdir(parents=True)
    (folder / "recipe.json").write_text('{"title":"菜谱","steps":[]}', encoding="utf-8")
    (folder / "note.md").write_bytes(b"original note\r\n")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    app = open_page(tmp_path)
    assert not app.exception
    assert app.number_input(key="dish_info_limit").value == 10
    assert app.checkbox(key="dish_info_only_pending").value is True
    assert app.checkbox(key="dish_info_web_search").value is False
    assert not submit_button(app).disabled
    app.button(key="dish_info_refresh").click().run()
    assert not app.exception
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    assert not (tmp_path / ".dish-info").exists()
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("all_with_web", [False, True])
def test_start_passes_scope_and_current_model_options(tmp_path, monkeypatch, all_with_web):
    monkeypatch.setattr(dish_info_ui, "list_dish_info_batches", lambda *_: [])
    calls = []
    monkeypatch.setattr(dish_info_ui, "start_dish_info_batch", lambda *a, **kw: calls.append((a, kw)) or {"batch_id": "created", "total": 10})
    app = open_page(tmp_path, llm_cli_extra_instructions="用中文简写")
    if all_with_web:
        app.number_input(key="dish_info_limit").set_value(0)
        app.checkbox(key="dish_info_web_search").check()
        app.checkbox(key="dish_info_only_pending").uncheck()
    submit_button(app).click().run()
    assert not app.exception
    arguments, options = calls[0]
    assert arguments == (str(tmp_path),)
    assert options["limit"] == (None if all_with_web else 10)
    assert options["web_search"] is all_with_web
    assert options["only_pending"] is (not all_with_web)
    assert options["provider"] == "codex"
    assert options["codex_model"] == "demo-model" and options["codex_profile"] == "demo-profile"
    assert options["cli_extra_instructions"] == "用中文简写"
    assert app.session_state["dish_info_batch_select"] == "created"
    assert any("已启动" in item.value for item in app.success)


def test_progress_and_failure_reasons_include_partial_search_failure(tmp_path, monkeypatch):
    state = batch(error="任务已暂停")
    monkeypatch.setattr(dish_info_ui, "list_dish_info_batches", lambda *_: [deepcopy(state)])
    app = open_page(tmp_path)
    assert not app.exception
    metrics = {metric.label: metric.value for metric in app.metric}
    assert {key: metrics[key] for key in ("菜品总数", "完成", "失败", "跳过", "剩余")} == {
        "菜品总数": "5", "完成": "1", "失败": "1", "跳过": "1", "剩余": "2",
    }
    assert any("LLM timeout" in str(frame.value) for frame in app.dataframe)
    assert any("联网检索失败" in warning.value for warning in app.warning)
    assert any("任务已暂停" in warning.value for warning in app.warning)
    assert not app.button(key="dish_info_resume").disabled
    assert not app.button(key="dish_info_retry").disabled
    assert app.button(key="dish_info_stop").disabled
    state.update(completed=2, pending=1)
    app.button(key="dish_info_refresh").click().run()
    assert not app.exception
    assert {metric.label: metric.value for metric in app.metric}["完成"] == "2"


def test_running_progress_can_pause_and_disables_resume(tmp_path, monkeypatch):
    state = batch(status="running", active=True, current_folder="厨师/豆腐")
    monkeypatch.setattr(dish_info_ui, "list_dish_info_batches", lambda *_: [deepcopy(state)])
    calls = []
    def stop(*args):
        calls.append(args)
        state["stop_requested"] = True
    monkeypatch.setattr(dish_info_ui, "stop_dish_info_batch", stop)
    app = open_page(tmp_path)
    assert not app.exception
    assert any("厨师/豆腐" in item.value for item in app.info)
    assert app.button(key="dish_info_resume").disabled
    assert app.button(key="dish_info_retry").disabled
    app.button(key="dish_info_stop").click().run()
    assert not app.exception
    assert calls == [(str(tmp_path), state["batch_id"])]
    assert any("当前菜品处理结束后暂停" in item.value for item in app.caption)


@pytest.mark.parametrize("retry", [False, True])
def test_resume_or_retry_uses_current_model_or_preserves_original(tmp_path, monkeypatch, retry):
    state = batch()
    monkeypatch.setattr(dish_info_ui, "list_dish_info_batches", lambda *_: [deepcopy(state)])
    calls = []
    monkeypatch.setattr(dish_info_ui, "resume_dish_info_batch", lambda *a, **kw: calls.append((a, kw)))
    app = open_page(tmp_path)
    key = "dish_info_retry" if retry else "dish_info_resume"
    app.button(key=key).click().run()
    assert not app.exception
    assert calls[0][0] == (str(tmp_path), state["batch_id"])
    assert calls[0][1]["provider"] == "codex"
    assert calls[0][1]["codex_model"] == "demo-model"
    assert calls[0][1].get("retry_failed", False) is retry
    app.checkbox(key="dish_info_current_config").uncheck().run()
    app.button(key=key).click().run()
    assert not app.exception
    assert calls[-1][1] == ({"retry_failed": True} if retry else {})


def test_disabled_current_provider_still_allows_original_task_config(tmp_path, monkeypatch):
    monkeypatch.setattr(dish_info_ui, "list_dish_info_batches", lambda *_: [batch()])
    calls = []
    monkeypatch.setattr(dish_info_ui, "resume_dish_info_batch", lambda *a, **kw: calls.append(kw))
    app = open_page(tmp_path, llm_provider="none")
    assert submit_button(app).disabled
    assert app.button(key="dish_info_resume").disabled
    app.checkbox(key="dish_info_current_config").uncheck().run()
    assert not app.button(key="dish_info_resume").disabled
    app.button(key="dish_info_resume").click().run()
    assert not app.exception
    assert calls == [{}]


def test_start_errors_are_displayed_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(dish_info_ui, "list_dish_info_batches", lambda *_: [])
    def fail(*args, **kwargs):
        raise ValueError("请先配置本地模型")
    monkeypatch.setattr(dish_info_ui, "start_dish_info_batch", fail)
    app = open_page(tmp_path)
    submit_button(app).click().run()
    assert not app.exception
    assert any("请先配置本地模型" in item.value for item in app.error)


def test_dashboard_without_config_prefers_running_task_and_opens_management(tmp_path, monkeypatch):
    active = batch(status="running", active=True)
    newest = batch(batch_id="newer", status="completed", active=False, total=1, completed=1,
                   updated_at="2099-01-01T00:00:00Z")
    monkeypatch.setattr(dish_info_ui, "list_dish_info_batches", lambda *_: [newest, active])
    app = AppTest.from_string(
        "import streamlit as st\n"
        "from bili_recipe_notes.dish_info_ui import render_dish_info_dashboard\n"
        "render_dish_info_dashboard(st, st.session_state['root'], key_prefix='home_intro')\n"
    )
    authenticate_streamlit_app(app)
    app.session_state["root"] = str(tmp_path)
    app.run()
    assert not app.exception
    assert {metric.label: metric.value for metric in app.metric}["菜品总数"] == "5"
    app.button(key="home_intro_open").click().run()
    assert not app.exception
    assert app.session_state["_next_page"] == "菜品介绍"
