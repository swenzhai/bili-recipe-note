from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from bili_recipe_notes import batch_consolidation as compact, batch_runner as runner
from bili_recipe_notes.batch_queue import batch_path, create_batch_state, load_batch_state
from bili_recipe_notes.storage import atomic_write_json


URL = "https://www.bilibili.com/video/BV13p411d7oQ"
OTHER = "https://www.bilibili.com/video/BV1xx411c7mu"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(runner, "_RUNS", {})
    out = tmp_path / "recipes"
    out.mkdir()
    return tmp_path, out


def output(out: Path, url=URL, name="recipe", *, raw=False, llm=False):
    folder = out / name
    folder.mkdir()
    documents = {
        "source.json": {"source_url": url, "title": "原始标题"},
        "job.json": {"source_url": url, "status": "raw_ready" if raw else "done", "stages": {"raw": {"status": "done"}}},
        "transcript.json": [{"start": 0, "text": "放入鸡蛋炒熟。"}],
        "recipe.json": {"title": "炒鸡蛋", "source_url": url, "ingredients": [], "seasonings": [], "tools": [],
                        "steps": [{"title": "炒熟", "action": "炒熟", "start_time": 0}], "summary_tips": [],
                        "uncertain_points": [], "extraction_method": "llm" if llm else "rule"},
        "recipe.review.json": {"manual": "keep"},
    }
    for name, value in documents.items():
        atomic_write_json(folder / name, value, backup=False)
    (folder / "note.md").write_bytes(b"Original note\r\n")
    return folder


def queue(root, out, batch_id, urls=(URL,), *, status="failed", folder=None, timestamp="2026-01-01", **options):
    state = create_batch_state(list(urls), {"out": str(out), **options}, batch_id=batch_id, project_root=root)
    state.created_at = timestamp + "T00:00:00+00:00"
    state.updated_at = timestamp + "T00:00:00+00:00"
    for item in state.items:
        item.status = status
        item.output_folder = str(folder) if folder else None
        item.error = "OLD FAILURE" if status == "failed" else None
        item.stages["raw"].status = "done" if folder else "failed" if status == "failed" else "pending"
        item.stages["recipe"].status = "done" if status in {"done", "skipped"} else "failed" if status == "failed" else "pending"
    atomic_write_json(batch_path(batch_id, root), asdict(state), backup=False)
    return state


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_final_video_results_replace_old_attempts_and_cleanup_files(workspace):
    root, out = workspace
    folder = output(out)
    queue(root, out, "first", folder=folder, status="skipped")
    queue(root, out, "latest", timestamp="2026-01-03")
    for batch_id in ("first", "latest"):
        runner.batch_log_path(batch_id, root).write_text("historic failed attempt")
        atomic_write_json(runner.background_result_path(batch_id, root), {"items": []})
        batch_path(batch_id, root).with_suffix(".json.bak").write_text("old failure")
        atomic_write_json(runner.background_request_path(batch_id, root), {"options": {"batch_id": batch_id, "out": str(out),
                                                                                       "llm_provider": "local", "local_llm_command": "saved command"}})
    before = snapshot(out)
    result = compact.consolidate_batches(root, out)
    assert result["groups"] == 1 and result["removed_tasks"] == 1 and result["recovered_items"] == 1
    assert not batch_path("first", root).exists()
    state = load_batch_state("latest", root)
    assert state.items[0].status == "done" and state.items[0].error is None
    assert all(value.error is None for value in state.items[0].stages.values())
    assert state.options["local_llm_command"] == "saved command"
    assert runner.get_background_batch_status("latest", root).status == "done"
    assert runner._load_result("latest", root).items[0].status == "done"
    for batch_id in ("first", "latest"):
        assert not runner.batch_log_path(batch_id, root).exists()
        assert not batch_path(batch_id, root).with_suffix(".json.bak").exists()
        assert not runner.background_request_path(batch_id, root).exists()
        assert (runner.background_runtime_dir(root) / f"{batch_id}.control.lock").exists()
    assert snapshot(out) == before
    after = snapshot(root / "data/state/batches")
    assert compact.consolidate_batches(root, out)["groups"] == 0
    assert snapshot(root / "data/state/batches") == after


def test_merge_retains_latest_unresolved_failure_and_supports_retry(workspace):
    root, out = workspace
    folder = output(out)
    first = queue(root, out, "first", (URL, OTHER), folder=None)
    first.items[0].output_folder = str(folder)
    atomic_write_json(batch_path("first", root), asdict(first), backup=False)
    last = queue(root, out, "latest", (URL, OTHER), timestamp="2026-01-03")
    last.items[1].error = "CURRENT FAILURE"
    atomic_write_json(batch_path("latest", root), asdict(last), backup=False)
    assert compact.consolidate_batches(root, out)["groups"] == 1
    state = load_batch_state("latest", root)
    assert [item.status for item in state.items] == ["done", "failed"]
    assert state.items[1].error == "CURRENT FAILURE"
    assert runner.read_batch_log("latest", project_root=root) == f"{OTHER}: CURRENT FAILURE\n"
    assert runner.get_background_batch_status("latest", root).status == "done_with_errors"
    from bili_recipe_notes.batch_queue import selectable_items
    assert [item.url for item in selectable_items(state, "retry-failed")] == [OTHER]


@pytest.mark.parametrize("status", ["running", "paused", "no-runtime"])
def test_active_or_paused_groups_are_unchanged(workspace, status):
    root, out = workspace
    queue(root, out, "one")
    queue(root, out, "two", status="recipe_running" if status == "no-runtime" else "failed")
    if status != "no-runtime":
        runner._write_status(runner.BackgroundBatchStatus("two", status, "2026-01-01"), root)
    before = {path: value for path, value in snapshot(root).items() if not path.endswith(".control.lock")}
    assert compact.consolidate_batches(root, out)["skipped_groups"] == 1
    assert {path: value for path, value in snapshot(root).items() if not path.endswith(".control.lock")} == before


@pytest.mark.parametrize("difference", ["part", "stage", "out", "llm", "screenshot", "force"])
def test_different_scopes_are_not_merged(workspace, difference):
    root, out = workspace
    queue(root, out, "one")
    options = {"target_stage": "raw"} if difference == "stage" else {"require_llm": True} if difference == "llm" else {"require_screenshot": True} if difference == "screenshot" else {"skip_existing": False} if difference == "force" else {}
    queue(root, root / "other" if difference == "out" else out, "two", (URL + "?p=2",) if difference == "part" else (URL,), **options)
    assert compact.consolidate_batches(root, out)["groups"] == 0
    assert batch_path("one", root).exists() and batch_path("two", root).exists()


def test_runtime_options_define_group_and_are_preserved(workspace):
    root, out = workspace
    queue(root, out, "one", target_stage="raw")
    queue(root, out, "two", timestamp="2026-01-03")
    atomic_write_json(runner.background_request_path("one", root), {"options": {"batch_id": "one", "out": str(out), "target_stage": "recipe"}})
    assert compact.consolidate_batches(root, out)["groups"] == 1
    assert load_batch_state("two", root).options.get("target_stage", "recipe") == "recipe"


@pytest.mark.parametrize("broken", ["state", "request", "status", "output"])
def test_corrupt_files_protect_group(workspace, broken):
    root, out = workspace
    folder = output(out)
    queue(root, out, "one", status="done", folder=folder)
    queue(root, out, "two", timestamp="2026-01-03")
    path = {"state": batch_path("one", root), "request": runner.background_request_path("one", root),
            "status": runner.background_status_path("one", root), "output": folder / "recipe.json"}[broken]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{")
    before = {path: value for path, value in snapshot(root).items() if not path.endswith(".control.lock")}
    assert compact.consolidate_batches(root, out)["skipped_groups"] == 1
    assert {path: value for path, value in snapshot(root).items() if not path.endswith(".control.lock")} == before


def test_missing_failed_artifacts_do_not_mask_other_valid_success(workspace):
    root, out = workspace
    good = output(out)
    bad = out / "incomplete"
    bad.mkdir()
    (bad / "note.md").write_text("partial note")
    atomic_write_json(bad / "recipe.json", json.loads((good / "recipe.json").read_text()))
    queue(root, out, "one", status="done", folder=good)
    queue(root, out, "two", folder=bad, timestamp="2026-01-02")
    queue(root, out, "three", timestamp="2026-01-03")
    before = snapshot(out)
    result = compact.consolidate_batches(root, out)
    assert result["removed_tasks"] == 2
    assert load_batch_state("three", root).items[0].output_folder == str(good)
    assert snapshot(out) == before


def test_missing_success_and_pure_pending_are_protected(workspace):
    root, out = workspace
    queue(root, out, "one", status="done", folder=out / "missing")
    queue(root, out, "two")
    assert compact.consolidate_batches(root, out)["skipped_groups"] == 1
    queue(root, out, "one", status="pending")
    queue(root, out, "two", status="pending")
    assert compact.consolidate_batches(root, out)["groups"] == 0


def test_source_classification_and_requested_group_are_preserved(workspace):
    root, out = workspace
    queue(root, out, "one", status="technique")
    queue(root, out, "two", timestamp="2026-01-03")
    queue(root, out, "other-a", (OTHER,))
    queue(root, out, "other-b", (OTHER,))
    assert compact.consolidate_batches(root, out, batch_ids=["two"])["removed_tasks"] == 1
    assert load_batch_state("two", root).items[0].status == "technique"
    assert batch_path("other-a", root).exists() and batch_path("other-b", root).exists()


def test_url_helpers_preserve_parts_and_match_new_request(workspace):
    root, out = workspace
    state = queue(root, out, "one", ("http://m.bilibili.com/video/BV13p411d7oQ/?spm=test&p=1",))
    assert compact.batch_group_key(state, root, out) == compact.group_key([URL], {"out": str(out)}, out)
    assert compact.video_key(URL + "?p=2&tracking=x") != compact.video_key(URL)


@pytest.mark.parametrize("invalid", ["url", "strict", "missing"])
def test_reuse_rejects_wrong_source_missing_or_insufficient_result(workspace, invalid):
    root, out = workspace
    folder = output(out, OTHER if invalid == "url" else URL)
    state = queue(root, out, "one", status="done", folder=folder,
                  require_llm=invalid == "strict")
    if invalid == "missing":
        (folder / "transcript.json").unlink()
    assert not compact.item_has_result(state.items[0], state.options, out)
    queue(root, out, "two", timestamp="2026-01-03", require_llm=invalid == "strict")
    assert compact.consolidate_batches(root, out)["skipped_groups"] == 1
    assert batch_path("one", root).exists()


def test_unwritable_record_directory_does_not_delete_group(workspace, monkeypatch):
    root, out = workspace
    queue(root, out, "one")
    queue(root, out, "two")
    monkeypatch.setattr(compact.os, "access", lambda *args: False)
    before = {path: value for path, value in snapshot(root).items() if not path.endswith(".control.lock")}
    assert compact.consolidate_batches(root, out)["skipped_groups"] == 1
    assert {path: value for path, value in snapshot(root).items() if not path.endswith(".control.lock")} == before


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX process locks")
def test_group_control_locks_each_batch_across_processes(workspace):
    import subprocess
    import sys

    root, out = workspace
    script = """import fcntl, sys
for path in sys.argv[1:]:
    with open(path, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            continue
        raise SystemExit(1)
"""
    paths = [str(runner.background_runtime_dir(root) / f"{batch_id}.control.lock") for batch_id in ("first", "second")]
    with runner._batch_group_control_lock(["second", "first"], root):
        assert subprocess.run([sys.executable, "-c", script, *paths], check=False).returncode == 0
