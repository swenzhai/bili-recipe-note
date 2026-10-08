from dataclasses import asdict

import pytest

from bili_recipe_notes.batch_queue import batch_path, create_batch_state, save_batch_state
from bili_recipe_notes.batch_runner import BackgroundBatchStatus, background_status_path, batch_log_path
from bili_recipe_notes.batch_worker import clear_resolved_history
from bili_recipe_notes.storage import atomic_write_json
from test_admin_library import make_recipe


@pytest.mark.parametrize("case", ["resolved", "failed", "missing", "another_run", "running"])
def test_only_current_fully_resolved_batch_drops_obsolete_diagnostics(tmp_path, monkeypatch, case):
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(tmp_path / "data"))
    folder = make_recipe(tmp_path / "data/recipes")
    atomic_write_json(folder / "job.json", {"status": "done"})
    state = create_batch_state(["BV13p411d7oQ"], {"out": str(folder.parent), "target_stage": "recipe"}, project_root=tmp_path)
    item = state.items[0]
    item.status = "failed"
    item.error = "obsolete failure"
    save_batch_state(state, tmp_path)
    item.status, item.output_folder, item.error = "done", str(folder), None
    for stage in item.stages.values():
        stage.status = "done"
    if case == "failed":
        item.status, item.stages["recipe"].status = "failed", "failed"
    if case == "missing":
        (folder / "note.md").unlink()
    save_batch_state(state, tmp_path)
    log = batch_log_path(state.batch_id, tmp_path)
    log.write_text("obsolete failure\nnew success\n")
    runtime = BackgroundBatchStatus(state.batch_id, "running" if case == "running" else "done", "new" if case == "another_run" else "current")
    atomic_write_json(background_status_path(state.batch_id, tmp_path), asdict(runtime))
    original = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    clear_resolved_history(state.batch_id, tmp_path, "current")
    assert log.exists() == (case != "resolved")
    assert batch_path(state.batch_id, tmp_path).with_suffix(".json.bak").exists() == (case != "resolved")
    assert {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()} == original
