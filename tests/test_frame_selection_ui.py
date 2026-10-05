from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from bili_recipe_notes import frame_selection_ui
from bili_recipe_notes.frame_selection import DEFAULT_REQUIREMENT


@pytest.fixture(autouse=True)
def isolated_ui(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BILI_RECIPE_DATA_DIR", raising=False)
    for name in ("start_frame_selection_batch", "stop_frame_selection_batch", "resume_frame_selection_batch"):
        monkeypatch.setattr(frame_selection_ui, name, lambda *_a, **_k: pytest.fail("unexpected real task operation"))
    monkeypatch.setattr(frame_selection_ui, "load_frame_candidate", lambda *_a, **_k: None)


def open_page(root: Path, *, scope=None, **config) -> AppTest:
    app = AppTest.from_string(
        "import streamlit as st\n"
        "from bili_recipe_notes.frame_selection_ui import render_frame_selection_page\n"
        "from bili_recipe_notes.config import UIConfig\n"
        "render_frame_selection_page(st, UIConfig(**st.session_state['config']))\n",
        default_timeout=15,
    )
    app.session_state["config"] = {"out_dir": str(root), "llm_provider": "none", **config}
    if scope is not None:
        app.session_state["_frame_selection_scope_folders"] = scope
    return app.run()


def submit_button(app):
    return next(button for button in app.button if button.label == "开始 AI 菜品选图")


def batch(**updates):
    return {
        "batch_id": "frame-example", "status": "paused", "active": False,
        "total": 6, "completed": 2, "not_found": 1, "failed": 1, "skipped": 1, "pending": 2,
        "created_at": "2026-10-01T10:00:00Z", "finished_at": "2026-10-01T10:02:00Z",
        "items": [
            {"folder": "厨师/炒蛋", "title": "炒蛋", "status": "completed", "result_status": "ok"},
            {"folder": "厨师/米饭", "title": "米饭", "status": "not_found", "result_status": "not_found"},
            {"folder": "厨师/面条", "status": "failed", "error": "视频下载失败"},
            {"folder": "厨师/豆腐", "status": "skipped"},
            {"folder": "厨师/青菜", "status": "pending"},
            {"folder": "厨师/蘑菇", "status": "pending"},
        ], **updates,
    }


def test_browsing_is_read_only_and_uses_independent_model_defaults(tmp_path):
    folder = tmp_path / "厨师" / "菜谱"
    folder.mkdir(parents=True)
    (folder / "recipe.json").write_text('{"title":"菜谱","steps":[]}', encoding="utf-8")
    (folder / "note.md").write_bytes(b"original note\r\n")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    app = open_page(tmp_path)
    assert not app.exception
    assert not submit_button(app).disabled
    assert app.number_input(key="frame_selection_limit").value == 10
    assert app.checkbox(key="frame_selection_only_pending").value is True
    assert app.checkbox(key="frame_selection_published_only").value is True
    assert app.checkbox(key="frame_selection_skip_confirmed").value is True
    assert app.checkbox(key="frame_selection_keep_video").value is False
    assert app.text_area(key="frame_selection_requirement").value == DEFAULT_REQUIREMENT
    assert app.selectbox(key="frame_selection_device").options == ["cpu", "cuda"]
    assert any("不使用侧栏的文本 LLM" in item.value for item in app.info)
    app.button(key="frame_selection_refresh").click().run()
    assert not app.exception
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    assert not (tmp_path / ".frame-selection").exists()
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("custom", [False, True])
def test_start_passes_local_options_and_never_sidebar_llm(tmp_path, monkeypatch, custom):
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [])
    calls = []
    monkeypatch.setattr(frame_selection_ui, "start_frame_selection_batch", lambda *a, **kw: calls.append((a, kw)) or {"batch_id": "created", "total": 10})
    app = open_page(tmp_path, cookies="cookie-file.txt", llm_provider="codex", codex_model="text-model")
    if custom:
        app.number_input(key="frame_selection_limit").set_value(0)
        app.checkbox(key="frame_selection_only_pending").uncheck()
        app.checkbox(key="frame_selection_published_only").uncheck()
        app.checkbox(key="frame_selection_skip_confirmed").uncheck()
        app.text_area(key="frame_selection_requirement").set_value("  找番茄炒蛋成品  ")
        app.text_input(key="frame_selection_model").set_value(" /models/visual ")
        app.text_input(key="frame_selection_python").set_value(" /env/bin/python ")
        app.number_input(key="frame_selection_threads").set_value(4)
        app.checkbox(key="frame_selection_keep_video").check()
    submit_button(app).click().run()
    assert not app.exception
    arguments, options = calls[0]
    assert arguments == (str(tmp_path),)
    assert options == {
        "limit": None if custom else 10, "folders": None,
        "only_pending": not custom, "published_only": not custom, "skip_confirmed": not custom,
        "cookies": "cookie-file.txt", "requirement": "找番茄炒蛋成品" if custom else DEFAULT_REQUIREMENT,
        "model": "/models/visual" if custom else None,
        "python_executable": "/env/bin/python" if custom else None,
        "device": "cpu", "threads": 4 if custom else 16, "coarse_limit": 24, "fine_limit": 8,
        "top_k": 3, "interval": 2.0, "fine_fps": 5.0, "keep_video": custom,
    }
    assert app.session_state["frame_selection_batch_select"] == "created"
    assert any("已启动" in item.value for item in app.success)


def test_racing_start_opens_existing_task_without_claiming_new_work(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [])
    monkeypatch.setattr(frame_selection_ui, "start_frame_selection_batch", lambda *_a, **_k: {
        "batch_id": "existing", "total": 10, "already_running": True,
    })
    app = open_page(tmp_path)
    submit_button(app).click().run()
    assert not app.exception
    assert app.session_state["frame_selection_batch_select"] == "existing"
    assert any("本次未新建任务" in item.value for item in app.warning)
    assert not app.success


@pytest.mark.parametrize("whole_library", [False, True])
def test_scoped_recipe_defaults_to_allow_reselection(tmp_path, monkeypatch, whole_library):
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [])
    calls = []
    monkeypatch.setattr(frame_selection_ui, "start_frame_selection_batch", lambda *a, **kw: calls.append(kw) or {"batch_id": "created", "total": 1})
    folder = str(tmp_path / "厨师" / "炒蛋")
    app = open_page(tmp_path, scope=[folder, folder])
    assert not app.exception
    assert app.checkbox(key="frame_selection_only_pending").value is False
    assert app.checkbox(key="frame_selection_published_only").value is False
    assert app.checkbox(key="frame_selection_skip_confirmed").value is False
    if whole_library:
        app.radio(key="frame_selection_scope").set_value("当前菜谱库")
    submit_button(app).click().run()
    assert not app.exception
    assert calls[0]["folders"] == (None if whole_library else [folder])
    assert calls[0]["skip_confirmed"] is False


def test_scope_change_resets_filters_but_refresh_preserves_choices(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [])
    app = open_page(tmp_path)
    app.session_state["_frame_selection_scope_folders"] = [str(tmp_path / "厨师" / "炒蛋")]
    app.run()
    assert not app.exception
    assert app.checkbox(key="frame_selection_skip_confirmed").value is False
    app.checkbox(key="frame_selection_skip_confirmed").check().run()
    assert app.checkbox(key="frame_selection_skip_confirmed").value is True
    app.session_state["_frame_selection_scope_folders"] = []
    app.run()
    assert not app.exception
    assert app.checkbox(key="frame_selection_only_pending").value is True


def test_returning_to_page_restores_library_filter_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [])
    app = AppTest.from_string(
        "import streamlit as st\n"
        "from bili_recipe_notes.frame_selection_ui import render_frame_selection_page\n"
        "from bili_recipe_notes.config import UIConfig\n"
        "page = st.selectbox('页面', ['选图', '其他'], key='page')\n"
        "if page == '选图': render_frame_selection_page(st, UIConfig(out_dir=st.session_state['root']))\n",
    )
    app.session_state["root"] = str(tmp_path)
    app.run()
    app.selectbox(key="page").set_value("其他").run()
    app.selectbox(key="page").set_value("选图").run()
    assert not app.exception
    for key in ("only_pending", "published_only", "skip_confirmed"):
        assert app.checkbox(key=f"frame_selection_{key}").value is True


def test_progress_distinguishes_no_match_and_renders_frames_failures(tmp_path, monkeypatch):
    state = batch(status="running", active=True, current_folder="厨师/青菜", current_progress={
        "phase": "coarse", "phase_completed": 3, "phase_total": 24,
        "frames_completed": 3, "frames_total": 32, "timestamp": 18.5,
    })
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [deepcopy(state)])
    app = open_page(tmp_path)
    assert not app.exception
    metrics = {metric.label: metric.value for metric in app.metric}
    assert {key: metrics[key] for key in ("菜品总数", "找到候选", "无匹配", "失败", "跳过", "剩余")} == {
        "菜品总数": "6", "找到候选": "1", "无匹配": "1", "失败": "1", "跳过": "1", "剩余": "2",
    }
    assert any("视频下载失败" in str(frame.value) for frame in app.dataframe)
    assert any("18.50 秒" in item.value and "3/32" in item.value for item in app.caption)
    assert any("3/24" in progress.proto.text for progress in app.get("progress"))
    assert any("不属于运行失败" in item.value for item in app.info)
    assert submit_button(app).disabled
    assert app.button(key="frame_selection_resume").disabled
    assert app.button(key="frame_selection_retry").disabled
    state["current_progress"].update(phase="fine", phase_completed=2, phase_total=8, frames_completed=26)
    app.button(key="frame_selection_refresh").click().run()
    assert not app.exception
    assert any("2/8" in progress.proto.text for progress in app.get("progress"))


def test_running_task_can_pause_after_current_dish(tmp_path, monkeypatch):
    state = batch(status="running", active=True, current_folder="厨师/青菜")
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [deepcopy(state)])
    calls = []
    def stop(*args):
        calls.append(args)
        state["stop_requested"] = True
    monkeypatch.setattr(frame_selection_ui, "stop_frame_selection_batch", stop)
    app = open_page(tmp_path)
    app.button(key="frame_selection_stop").click().run()
    assert not app.exception
    assert calls == [(str(tmp_path), state["batch_id"])]
    assert app.button(key="frame_selection_stop").disabled
    assert any("当前菜品处理结束后保存进度并暂停" in item.value for item in app.caption)


def test_no_match_completion_contributes_to_time_estimate(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    started = (now - timedelta(seconds=120)).isoformat()
    finished = (now - timedelta(seconds=60)).isoformat()
    state = batch(status="running", active=True, total=2, completed=1, not_found=1, failed=0,
                  skipped=0, pending=1, created_at=started, started_at=started,
                  items=[{"folder": "厨师/米饭", "status": "not_found", "started_at": started, "finished_at": finished},
                         {"folder": "厨师/青菜", "status": "running"}])
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [state])
    app = open_page(tmp_path)
    assert not app.exception
    assert {metric.label: metric.value for metric in app.metric}["预计剩余"] != "等待数据"


@pytest.mark.parametrize("retry", [False, True])
def test_resume_defaults_to_original_options_and_allows_current(tmp_path, monkeypatch, retry):
    state = batch()
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [deepcopy(state)])
    calls = []
    monkeypatch.setattr(frame_selection_ui, "resume_frame_selection_batch", lambda *a, **kw: calls.append((a, kw)))
    app = open_page(tmp_path, cookies="current-cookies.txt")
    assert app.checkbox(key="frame_selection_current_config").value is False
    key = "frame_selection_retry" if retry else "frame_selection_resume"
    app.button(key=key).click().run()
    assert not app.exception
    expected = {"cookies": "current-cookies.txt", **({"retry_failed": True} if retry else {})}
    assert calls == [((str(tmp_path), state["batch_id"]), expected)]
    app.text_area(key="frame_selection_requirement").set_value("装盘且无手遮挡").run()
    app.number_input(key="frame_selection_threads").set_value(6).run()
    app.checkbox(key="frame_selection_current_config").check().run()
    app.button(key=key).click().run()
    assert not app.exception
    assert calls[-1][1]["requirement"] == "装盘且无手遮挡"
    assert calls[-1][1]["threads"] == 6
    assert calls[-1][1]["cookies"] == "current-cookies.txt"
    assert calls[-1][1].get("retry_failed", False) is retry


def test_candidate_preview_navigates_to_manual_review_without_applying(tmp_path, monkeypatch):
    state = batch()
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [state])
    folder = tmp_path / "厨师" / "炒蛋"
    folder.mkdir(parents=True)
    (folder / "recipe.json").write_text('{"title":"炒蛋","steps":[]}', encoding="utf-8")
    (folder / "note.md").write_bytes(b"original note\r\n")
    image = tmp_path / "candidate.png"
    Image.new("RGB", (20, 12), "orange").save(image)
    calls = []
    def candidate(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok", "image_path": str(image), "timestamp": 126.0, "reason": "成品清晰"}
    monkeypatch.setattr(frame_selection_ui, "load_frame_candidate", candidate)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    app = open_page(tmp_path)
    assert not app.exception
    assert calls[0] == ((tmp_path, folder), {"include_stale": True})
    assert any("126.00 秒" in item.value for item in app.caption)
    assert any("成品清晰" in item.value for item in app.markdown)
    app.button(key="frame_selection_confirm_candidate").click().run()
    assert not app.exception
    assert app.session_state["_quick_review_preferred_folder"] == str(folder)
    assert app.session_state["quick_review_filter"] == "全部"
    assert app.session_state["_next_page"] == "快速审核"
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("candidate", [None, {"status": "stale", "valid": False}])
def test_unusable_candidate_cannot_be_confirmed(tmp_path, monkeypatch, candidate):
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [batch()])
    monkeypatch.setattr(frame_selection_ui, "load_frame_candidate", lambda *_a, **_k: candidate)
    app = open_page(tmp_path)
    assert not app.exception
    assert any("过期" in item.value for item in app.warning)
    assert not any(button.key == "frame_selection_confirm_candidate" for button in app.button)


def test_no_match_is_result_with_no_confirmation_or_failure_retry(tmp_path, monkeypatch):
    state = batch(total=1, completed=1, not_found=1, failed=0, skipped=0, pending=0, status="completed",
                  items=[{"folder": "厨师/米饭", "status": "not_found"}])
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [state])
    monkeypatch.setattr(frame_selection_ui, "load_frame_candidate", lambda *_a, **_k: {"status": "not_found", "reason": "无装盘镜头"})
    app = open_page(tmp_path)
    assert not app.exception
    assert app.button(key="frame_selection_retry").disabled
    assert app.button(key="frame_selection_resume").disabled
    assert "未找到匹配画面" in app.selectbox(key="frame_selection_result_select").options[0]
    assert any("无装盘镜头" in item.value for item in app.markdown)
    assert not any(button.key == "frame_selection_confirm_candidate" for button in app.button)


def test_candidate_outside_library_is_rejected_before_loading(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [batch(items=[{"folder": "../outside", "status": "completed"}])])
    monkeypatch.setattr(frame_selection_ui, "load_frame_candidate", lambda *_a, **_k: pytest.fail("must not load external folders"))
    app = open_page(tmp_path)
    assert not app.exception
    assert any("超出当前菜谱库" in item.value for item in app.error)


def test_dashboard_without_config_prefers_active_task(tmp_path, monkeypatch):
    active = batch(status="running", active=True)
    newest = batch(batch_id="newer", status="completed", active=False, total=1, completed=1,
                   updated_at="2099-01-01T00:00:00Z")
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [newest, active])
    app = AppTest.from_string(
        "import streamlit as st\n"
        "from bili_recipe_notes.frame_selection_ui import render_frame_selection_dashboard\n"
        "render_frame_selection_dashboard(st, st.session_state['root'], key_prefix='home_frames', show_open_button=True)\n"
    )
    app.session_state["root"] = str(tmp_path)
    app.run()
    assert not app.exception
    assert {metric.label: metric.value for metric in app.metric}["菜品总数"] == "6"
    app.button(key="home_frames_open").click().run()
    assert not app.exception
    assert app.session_state["_next_page"] == "AI 菜品选图"
    assert app.session_state["frame_selection_batch_select"] == active["batch_id"]


@pytest.mark.parametrize("operation", ["list", "load", "start", "resume"])
def test_errors_are_displayed_without_crashing(tmp_path, monkeypatch, operation):
    def fail(*args, **kwargs):
        raise ValueError("合成测试错误")
    monkeypatch.setattr(frame_selection_ui, "list_frame_selection_batches", lambda *_: [batch()] if operation != "start" else [])
    target = {"list": "list_frame_selection_batches", "load": "load_frame_candidate", "start": "start_frame_selection_batch", "resume": "resume_frame_selection_batch"}[operation]
    monkeypatch.setattr(frame_selection_ui, target, fail)
    app = open_page(tmp_path)
    if operation == "start":
        submit_button(app).click().run()
    elif operation == "resume":
        app.button(key="frame_selection_resume").click().run()
    assert not app.exception
    assert any("合成测试错误" in item.value for item in app.error)
    if operation == "list":
        assert submit_button(app).disabled
