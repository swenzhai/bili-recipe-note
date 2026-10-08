from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from bili_recipe_notes import ui
from bili_recipe_notes.access_control import ADMIN_COOKIE
from bili_recipe_notes import ai_review_dashboard, ai_review_workbench, dish_info_ui, frame_selection_ui
from bili_recipe_notes.config import UIConfig
from bili_recipe_notes.legacy_access import require_legacy_admin_session


class Interrupted(BaseException):
    pass


class PartialRun:
    """Retain actual registered closures, as Streamlit does between reruns."""
    def __init__(self, token):
        self.session_state = {"_legacy_admin_session": token}
        self.fragments = {}
        self.callbacks = {}
        self.reruns = []
        self.rendered = []
        self.callback_mode = False

    def fragment(self, **kwargs):
        def register(function):
            self.fragments[function.__name__] = function
            return lambda: None
        return register

    def dialog(self, *args, **kwargs):
        return self.fragment()

    def rerun(self, **kwargs):
        self.reruns.append(kwargs)
        if not self.callback_mode:
            raise Interrupted

    def stop(self):
        raise Interrupted

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __getattr__(self, name):
        def render(*args, **kwargs):
            self.rendered.append((name, args))
            if name == "form_submit_button":
                self.callbacks[args[0]] = kwargs["on_click"]
                return False
            if name == "columns":
                return [self] * (args[0] if isinstance(args[0], int) else len(args[0]))
            if name in {"form", "container", "expander"}:
                return self
            if name in {"selectbox", "radio"}:
                value = args[1][kwargs.get("index", 0)]
            elif name in {"text_input", "text_area"}:
                value = kwargs.get("value", "")
            elif name in {"checkbox", "toggle", "number_input"}:
                value = kwargs.get("value", False)
            else:
                return None
            if kwargs.get("key"):
                self.session_state.setdefault(kwargs["key"], value)
                return self.session_state[kwargs["key"]]
            return value
        return render


@pytest.fixture
def legacy_server(tmp_path, monkeypatch, authorize_client):
    monkeypatch.chdir(tmp_path)
    with TestClient(FastAPI()) as client:
        authorize_client(client, tmp_path)
        yield client


def app(*, token=None, meal=False):
    value = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20)
    if token is not None:
        value.session_state["_legacy_admin_session"] = token
    if meal:
        value.query_params["mode"] = "meal"
    return value


@pytest.mark.parametrize("meal", [False, True])
def test_legacy_unauthenticated_page_hides_all_management_and_meal_content(legacy_server, meal):
    page = app(meal=meal).run()
    assert not page.exception
    assert [title.value for title in page.title] == ["管理员登录"]
    assert not page.selectbox and not page.dataframe
    assert not page.get("component_instance")
    assert all(widget.label != "输出目录" for widget in page.text_input)
    assert page.text_input[0].label == "管理员密码"


def test_legacy_session_allows_management_until_revoked(legacy_server):
    token = legacy_server.cookies.get(ADMIN_COOKIE)
    page = app(token=token).run()
    assert not page.exception
    assert page.selectbox(key="main_page").value == "单视频生成"
    assert any(widget.label == "输出目录" for widget in page.text_input)
    assert legacy_server.post("/api/access/admin/logout", headers={"X-Access-Request": "1"}).status_code == 200
    page.run()
    assert not page.exception
    assert [title.value for title in page.title] == ["管理员登录"]
    assert not page.selectbox and not page.dataframe


def test_legacy_meal_cannot_bypass_disabled_ordering_even_with_admin_session(legacy_server):
    response = legacy_server.put("/api/access/settings", headers={"X-Access-Request": "1"}, json={
        "ordering_enabled": False, "ordering_password_required": True,
    })
    assert response.status_code == 200
    page = app(token=legacy_server.cookies.get(ADMIN_COOKIE), meal=True).run()
    assert not page.exception
    assert [title.value for title in page.title] == ["点餐页面已关闭"]
    assert not page.selectbox and not page.dataframe
    assert not page.get("component_instance")


@pytest.mark.parametrize("kind", ["generation", "audit", "description", "frames"])
def test_retained_dashboard_fragment_revalidates_before_reading_or_operating(legacy_server, tmp_path, monkeypatch, kind):
    st = PartialRun(legacy_server.cookies.get(ADMIN_COOKIE))
    modules = {
        "generation": (ui, "list_batch_states", "render_live_dashboard"),
        "audit": (ai_review_dashboard, "list_ai_review_batches", "live_progress"),
        "description": (dish_info_ui, "list_dish_info_batches", "live"),
        "frames": (frame_selection_ui, "list_frame_selection_batches", "live"),
    }
    module, reader, function = modules[kind]
    reads = []
    monkeypatch.setattr(module, reader, lambda *args: reads.append(args) or [])
    if kind == "generation":
        ui._render_background_dashboard(st, UIConfig(out_dir=tmp_path))
    else:
        renderer = {"audit": ai_review_dashboard.render_ai_review_dashboard,
                    "description": dish_info_ui.render_dish_info_dashboard,
                    "frames": frame_selection_ui.render_frame_selection_dashboard}[kind]
        renderer(st, tmp_path)
    fragment = st.fragments[function]
    fragment()
    assert len(reads) == 1
    before = list(st.rendered)
    legacy_server.app.state.access_control.reset_admin_password("changed-admin-password")
    with pytest.raises(Interrupted):
        fragment()
    assert len(reads) == 1 and st.rendered == before
    assert st.reruns == [{"scope": "app"}]


def test_retained_cover_dialog_cannot_read_preview_or_write_after_revocation(legacy_server, tmp_path, monkeypatch):
    st = PartialRun(legacy_server.cookies.get(ADMIN_COOKIE))
    st.session_state["mobile_cover_crop_dish"] = {"content": b"image", "token": "candidate"}
    selected = {"id": "dish", "title": "鸡蛋", "output_folder": str(tmp_path), "cover_status": "pending"}
    store = SimpleNamespace(list_recipe_cover_reviews=lambda: [selected])
    monkeypatch.setattr(ui, "load_frame_candidate", lambda *args: None)
    ui._render_recipe_cover_review(st, store, UIConfig(out_dir=tmp_path), fixed_output_folder=tmp_path)
    dialog = st.fragments["show_cover_crop_dialog"]
    before = list(st.rendered)
    monkeypatch.setattr(ui, "_cover_crop_preview", lambda *args: pytest.fail("revoked dialog read its image"))
    monkeypatch.setattr(ui, "save_recipe_cover_content", lambda *args, **kwargs: pytest.fail("revoked dialog saved a cover"))
    legacy_server.app.state.access_control.reset_admin_password("changed-admin-password")
    with pytest.raises(Interrupted):
        dialog()
    assert st.rendered == before and st.reruns == [{"scope": "app"}]


@pytest.mark.parametrize("label", ["保存人工分类", "保存并核对下一项"])
def test_registered_review_callback_stops_when_rerun_is_ignored_after_revocation(legacy_server, tmp_path, monkeypatch, label):
    st = PartialRun(legacy_server.cookies.get(ADMIN_COOKIE))
    st.callback_mode = True  # Streamlit treats rerun() inside a callback as a no-op.
    selected = {"folder": tmp_path, "recipe": {"ingredients": [{"name": "鸡蛋"}]}, "review_state": {}}
    ai_review_workbench.render_review_workbench(st, selected, next_folder=None)
    callback = st.callbacks[label]
    monkeypatch.setattr(ai_review_workbench, "set_ai_review_classification", lambda *args, **kwargs: pytest.fail("revoked callback wrote classification"))
    monkeypatch.setattr(ai_review_workbench, "set_ai_review_item_decision", lambda *args, **kwargs: pytest.fail("revoked callback wrote a decision"))
    legacy_server.app.state.access_control.reset_admin_password("changed-admin-password")
    with pytest.raises(Interrupted):
        callback()
    assert st.reruns == [{"scope": "app"}]
    assert "_legacy_admin_session" not in st.session_state


def test_partial_guard_accepts_only_real_current_session_without_rendering_login(legacy_server):
    st = PartialRun(legacy_server.cookies.get(ADMIN_COOKIE))
    require_legacy_admin_session(st)
    assert not st.rendered and not st.reruns
