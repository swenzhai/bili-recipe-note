from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

from conftest import authenticate_streamlit_app

from bili_recipe_notes import ai_review_dashboard as dashboard, ai_review_ui


def _batch():
    return {
        "batch_id": "active-old", "status": "running", "active": True,
        "run_token": "second-run", "created_at": "2026-09-20T10:00:00Z",
        "runs": [{"run_token": "second-run", "started_at": "2026-09-30T10:00:00Z"}],
        "total": 10, "completed": 3, "failed": 1, "skipped": 2,
        "current_folder": "厨师/红烧肉", "max_attempts": 3,
        "items": [
            {"folder": "old", "status": "completed", "started_at": "2026-09-20T10:00:00Z", "finished_at": "2026-09-20T10:01:00Z"},
            {"folder": "new", "status": "completed", "started_at": "2026-09-30T10:00:00Z", "finished_at": "2026-09-30T10:01:00Z",
             "attempts": [{"run_token": "second-run", "status": "completed"}]},
            {"folder": "failed", "status": "failed", "started_at": "2026-09-30T10:01:00Z", "finished_at": "2026-09-30T10:02:00Z", "error": "LLM timeout"},
            {"folder": "skip", "status": "skipped", "started_at": "2026-09-30T10:02:00Z", "finished_at": "2026-09-30T10:02:00Z"},
            {"folder": "厨师/红烧肉", "status": "running", "started_at": "2026-09-30T10:02:00Z"},
        ],
    }


def test_estimates_use_current_run_and_exclude_skips_and_prior_completed_work():
    result = dashboard.review_batch_summary(_batch(), now=datetime(2026, 9, 30, 10, 4, tzinfo=timezone.utc))
    assert result["processed"] == 6 and result["remaining"] == 4
    assert result["progress"] == pytest.approx(0.6)
    assert result["elapsed"] == 240
    assert result["speed_per_hour"] == 30
    assert result["eta"] == 480
    assert result["estimated_finish"] == datetime(2026, 9, 30, 10, 12, tzinfo=timezone.utc)


def test_paused_duration_is_frozen_and_no_eta_is_invented_without_samples():
    batch = _batch()
    batch.update(status="paused", active=False, finished_at="2026-09-30T10:04:00Z")
    result = dashboard.review_batch_summary(batch, now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert result["elapsed"] == 240
    assert result["eta"] is None and result["estimated_finish"] is None
    batch.update(active=True, items=[])
    assert dashboard.review_batch_summary(batch)["speed_per_hour"] is None
    assert dashboard.review_batch_summary({})["progress"] == 0


def test_dashboard_discovers_active_old_task_and_refreshes_counts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    active = _batch()
    newer = {"batch_id": "newer-finished", "active": False, "status": "completed", "total": 1, "completed": 1}
    batches = [newer, active]
    monkeypatch.setattr(dashboard, "list_ai_review_batches", lambda root: deepcopy(batches))
    at = AppTest.from_string(
        "import streamlit as st\nfrom bili_recipe_notes.ai_review_dashboard import render_ai_review_dashboard\n"
        "render_ai_review_dashboard(st, st.session_state['root'])\n"
    )
    authenticate_streamlit_app(at)
    at.session_state["root"] = str(tmp_path)
    at.run()
    assert not at.exception
    assert any("active-old" in caption.value for caption in at.caption)
    metrics = {metric.label: metric.value for metric in at.metric}
    assert metrics["审核完成"] == "3" and metrics["审核剩余"] == "4"
    assert any("每 5 秒" in caption.value for caption in at.caption)
    active.update(completed=4)
    at.button(key="ai_review_dashboard_refresh").click().run()
    assert {metric.label: metric.value for metric in at.metric}["审核完成"] == "4"
    active.update(status="paused", active=False)
    batches[:] = [active, newer]
    at.button(key="ai_review_dashboard_refresh").click().run()
    assert not at.exception
    assert any("已暂停" in caption.value for caption in at.caption)


def test_progress_remains_visible_when_library_scan_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dashboard, "list_ai_review_batches", lambda root: [_batch()])

    def unavailable(root):
        raise OSError("library unavailable")

    monkeypatch.setattr(ai_review_ui, "_review_records", unavailable)
    at = AppTest.from_string(
        "import streamlit as st\nfrom bili_recipe_notes.ai_review_ui import render_ai_review_page\n"
        "from bili_recipe_notes.config import UIConfig\n"
        "render_ai_review_page(st, UIConfig(out_dir=st.session_state['root']))\n"
    )
    authenticate_streamlit_app(at)
    at.session_state["root"] = str(tmp_path)
    at.run()
    assert not at.exception
    assert any(metric.label == "审核总数" and metric.value == "10" for metric in at.metric)
    assert any("library unavailable" in error.value for error in at.error)
