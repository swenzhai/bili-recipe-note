from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bili_recipe_notes.history import (
    completed_recipe_folders, find_history_by_url, find_history_record_by_url,
    is_complete_output, scan_history,
)
from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.output_folders import plan_output_folder_migration
from bili_recipe_notes.output_layout import (
    iter_output_folders, iter_source_output_folders, load_dish_plan, source_output_folder,
)
from bili_recipe_notes.review_policy import (
    get_review_state, review_input_fingerprint, set_ai_review_visibility,
)
from test_mobile_sync import _recipe


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def collection(project: Path):
    parent = project / "data" / "recipes" / "同一视频两道菜"
    parent.mkdir(parents=True)
    url = "https://www.bilibili.com/video/BV1demo"
    source = {"source_url": url, "bvid": "BV1demo", "cid": "101", "video_title": "两道菜"}
    transcript = [{"start": 0, "end": 10, "text": "炒熟鸡蛋"}, {"start": 10, "end": 20, "text": "番茄炒熟"}]
    _write(parent / "source.json", source)
    _write(parent / "transcript.json", transcript)
    _write(parent / "job.json", {**source, "status": "done", "stages": {"raw": {"status": "done"}}})
    dishes = []
    children = []
    for index, identity in enumerate(("dish-first", "dish-second")):
        child = _recipe(project, f"{parent.name}/dishes/{identity}")
        recipe = json.loads((child / "recipe.json").read_text())
        recipe.update(title="炒菜", dish_id=identity,
                      source_time_ranges=[{"start": index * 10, "end": (index + 1) * 10}],
                      source_segment_ranges=[{"start_index": index, "end_index": index}])
        _write(child / "recipe.json", recipe)
        _write(child / "source.json", {**source, "dish_id": identity})
        _write(child / "job.json", {**source, "status": "done", "dish_id": identity})
        _write(child / "transcript.json", [transcript[index]])
        (child / "note.md").write_text("# 炒菜\n\n![步骤](images/step.jpg)\n")
        children.append(child)
        dishes.append({"dish_id": identity, "title": "炒菜", "output_folder": f"dishes/{identity}", "status": "done"})
    _write(parent / "dish-plan.json", {"schema_version": 1, "status": "identified", "dishes": dishes})
    return parent, children


def test_collection_discovery_is_readonly_and_survives_a_broken_plan(tmp_path):
    parent, children = collection(tmp_path)
    root = parent.parent
    originals = {p: p.read_bytes() for p in parent.rglob("*") if p.is_file()}
    assert set(iter_output_folders(root)) == {parent, *children}
    assert list(iter_source_output_folders(root)) == [parent]
    assert all(source_output_folder(child) == parent for child in children)
    history = scan_history(root)
    group = next(item for item in history if item.output_folder == parent)
    assert group.status == "done" and group.dish_count == group.completed_dish_count == 2
    assert [item["output_folder"] for item in group.dishes] == [str(p) for p in children]
    assert originals == {p: p.read_bytes() for p in parent.rglob("*") if p.is_file()}
    assert plan_output_folder_migration(root) == []
    (parent / "dish-plan.json").write_text("{")
    assert set(iter_output_folders(root)) == {parent, *children}
    assert not is_complete_output(parent)
    (parent / "dishes" / "link").symlink_to(children[0], target_is_directory=True)
    assert set(iter_output_folders(root)) == {parent, *children}


def test_source_completion_and_reuse_never_select_a_completed_subset(tmp_path):
    parent, children = collection(tmp_path)
    url = json.loads((parent / "source.json").read_text())["source_url"]
    plan = load_dish_plan(parent)
    plan["dishes"][1]["status"] = "failed"
    _write(parent / "dish-plan.json", plan)
    job = json.loads((children[1] / "job.json").read_text())
    _write(children[1] / "job.json", {**job, "status": "failed"})
    assert is_complete_output(children[0])
    assert not is_complete_output(parent)
    assert find_history_by_url(parent.parent, url) is None
    assert find_history_record_by_url(parent.parent, url).output_folder == parent
    assert completed_recipe_folders(parent) == [children[0]]
    _write(children[1] / "job.json", job)
    plan["dishes"][1]["status"] = "done"
    _write(parent / "dish-plan.json", plan)
    assert find_history_by_url(parent.parent, url).output_folder == parent


@pytest.mark.parametrize("status", ["needs_review", "unavailable"])
def test_unconfirmed_plan_never_completes_a_source_even_with_existing_dishes(tmp_path, status):
    parent, _ = collection(tmp_path)
    plan = load_dish_plan(parent)
    plan["status"] = status
    _write(parent / "dish-plan.json", plan)
    assert not is_complete_output(parent)
    assert find_history_by_url(parent.parent, "https://www.bilibili.com/video/BV1demo") is None


def test_single_dish_plan_keeps_legacy_completion_and_identity(tmp_path):
    from test_handoff import _write_recipe
    parent = tmp_path / "data" / "recipes" / "single"
    _write_recipe(parent, "https://www.bilibili.com/video/BV1single")
    _write(parent / "dish-plan.json", {"schema_version": 1, "dishes": [{"dish_id": "dish-one", "output_folder": "."}]})
    assert is_complete_output(parent)
    store = MobileSyncStore(tmp_path)
    assert store._source_identity({"dish_id": ""}, {"bvid": "BV1single", "dish_id": ""}) == "bilibili:bv1single"
    assert store.index_recipes()["indexed"] == 1


def test_same_source_dishes_keep_distinct_ids_visibility_and_publication(tmp_path):
    parent, children = collection(tmp_path)
    store = MobileSyncStore(tmp_path)
    report = store.index_recipes()
    assert report["indexed"] == 2 and report["duplicates"] == []
    ids = [json.loads((child / "sync-meta.json").read_text())["recipe_id"] for child in children]
    assert len(set(ids)) == 2
    store.set_recipe_publications({ids[0]: False})
    set_ai_review_visibility(children[1], "hidden")
    assert store.index_recipes()["deleted"] == 1
    states = store.recipe_visibility_states(children)
    assert not states[str(children[0])]["published"] and not states[str(children[0])]["hidden"]
    assert states[str(children[1])]["hidden"]
    set_ai_review_visibility(children[1], None)
    store.set_video_classifications(["https://www.bilibili.com/video/BV1demo"], "technique")
    assert store.index_recipes()["indexed"] == 0
    set_ai_review_visibility(children[0], "visible")
    store.index_recipes()
    assert store.recipe_visibility_states(children)[str(children[0])]["published"] is False
    store.set_video_classifications(["https://www.bilibili.com/video/BV1demo"], "recipe")
    assert store.index_recipes()["indexed"] == 2
    restarted = MobileSyncStore(tmp_path)
    assert restarted.index_recipes()["deleted"] == 0
    assert {row["id"] for row in restarted.list_indexed_recipes()} == set(ids)


def test_incremental_collection_preserves_missing_children_and_recovers(tmp_path, monkeypatch):
    parent, children = collection(tmp_path)
    store = MobileSyncStore(tmp_path)
    assert store.index_recipes()["rebuilt"] == 2
    monkeypatch.setattr("bili_recipe_notes.mobile_sync.fingerprint_is_stable", lambda _: True)
    assert store.index_recipes()["cache_hits"] == 2
    recipe = json.loads((children[0] / "recipe.json").read_text())
    _write(children[0] / "recipe.json", {**recipe, "title": "改菜名"})
    assert store.index_recipes()["rebuilt"] == 1
    prior = store.list_indexed_recipes()
    plan_bytes = (parent / "dish-plan.json").read_bytes()
    (parent / "dish-plan.json").write_text("{")
    missing = tmp_path / "temporarily-missing"
    children[1].rename(missing)
    failed = store.index_recipes()
    assert failed["deleted"] == failed["changed"] == 0 and failed["errors"]
    assert store.list_indexed_recipes() == prior
    (parent / "dish-plan.json").write_bytes(plan_bytes)
    assert store.index_recipes()["deleted"] == 0
    missing.rename(children[1])
    assert store.index_recipes()["errors"] == []
    assert store.index_recipes()["cache_hits"] == 2


def test_missing_or_copied_child_identity_does_not_collapse_the_menu(tmp_path):
    _, children = collection(tmp_path)
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    original = (children[1] / "sync-meta.json").read_bytes()
    shutil.copyfile(children[0] / "sync-meta.json", children[1] / "sync-meta.json")
    result = store.index_recipes()
    assert result["deleted"] == 0 and result["errors"]
    assert len(store.list_indexed_recipes()) == 2
    (children[1] / "sync-meta.json").write_bytes(original)
    assert not store.index_recipes()["errors"]


def test_dish_evidence_scope_changes_expire_only_its_review(tmp_path):
    from test_mobile_sync import _ai_review
    _, children = collection(tmp_path)
    for child in children:
        _ai_review(child)
    sibling_fingerprint = review_input_fingerprint(children[1])
    assert get_review_state(children[0])["ai_valid"]
    recipe = json.loads((children[0] / "recipe.json").read_text())
    recipe["source_time_ranges"][0]["end"] = 9
    _write(children[0] / "recipe.json", recipe)
    assert not get_review_state(children[0])["ai_valid"]
    assert get_review_state(children[1])["ai_valid"]
    assert review_input_fingerprint(children[1]) == sibling_fingerprint


@pytest.mark.parametrize("child_path", ["../../elsewhere", "/tmp/escape", "dishes/other"])
def test_plan_rejects_paths_outside_the_stable_dish_directory(tmp_path, child_path):
    parent, _ = collection(tmp_path)
    plan = load_dish_plan(parent)
    plan["dishes"][0]["output_folder"] = child_path
    _write(parent / "dish-plan.json", plan)
    with pytest.raises(ValueError):
        load_dish_plan(parent)


def test_archiving_same_video_and_title_keeps_each_dish_independent(tmp_path):
    from bili_recipe_notes.obsidian_archive import archive_recipe_to_obsidian
    _, children = collection(tmp_path)
    first, second = [archive_recipe_to_obsidian(child, tmp_path / "vault") for child in children]
    assert first.note_path != second.note_path
    assert first.note_path.is_file() and second.note_path.is_file()
    assert archive_recipe_to_obsidian(children[0], tmp_path / "vault").note_path == first.note_path
    assert len(list((tmp_path / "vault" / "菜谱").rglob("*.md"))) == 2


def _export_collection(project: Path, parent: Path, destination: Path):
    from bili_recipe_notes.batch_queue import create_batch_state, save_batch_state
    from bili_recipe_notes.handoff import export_batch_handoff
    state = create_batch_state(["https://www.bilibili.com/video/BV1demo"],
                               {"out": str(parent.parent), "target_stage": "recipe"},
                               batch_id="multi-dish-transfer", project_root=project)
    state.items[0].output_folder = str(parent)
    state.items[0].status = "done" if is_complete_output(parent) else "failed"
    save_batch_state(state, project_root=project)
    return export_batch_handoff(state.batch_id, parent.parent, destination, project_root=project)


def test_handoff_preserves_collection_images_audits_and_local_completed_dishes(tmp_path):
    from bili_recipe_notes.handoff import import_handoff_bundle
    source_project = tmp_path / "source"
    parent, children = collection(source_project)
    set_ai_review_visibility(children[0], "visible")
    exported = _export_collection(source_project, parent, tmp_path / "first.zip")
    target_project = tmp_path / "target"
    target_root = target_project / "data" / "recipes"
    imported = import_handoff_bundle(exported.path, target_root, project_root=target_project)
    assert imported.recipe_count == 1
    restored = next(iter_source_output_folders(target_root))
    assert is_complete_output(restored)
    for child in children:
        local = restored / "dishes" / child.name
        assert (local / "images" / "step.jpg").read_bytes() == b"fake-jpeg"
        assert json.loads((local / "job.json").read_text())["output_folder"] == str(local)
    local_first = restored / "dishes" / children[0].name
    assert get_review_state(local_first)["record"]["visibility_override"] == "visible"
    set_ai_review_visibility(local_first, "hidden")
    local_originals = {p.relative_to(local_first): p.read_bytes() for p in local_first.rglob("*") if p.is_file()}
    # Both sources become partial, but different children have completed work.
    local_second = restored / "dishes" / children[1].name
    _write(local_second / "job.json", {"status": "failed"})
    local_plan = load_dish_plan(restored)
    local_plan["dishes"][1]["status"] = "failed"
    _write(restored / "dish-plan.json", local_plan)
    _write(restored / "job.json", {"source_url": "https://www.bilibili.com/video/BV1demo", "status": "failed", "stages": {"raw": {"status": "done"}}})
    incoming_plan = load_dish_plan(parent)
    incoming_plan["dishes"][0]["status"] = "failed"
    _write(parent / "dish-plan.json", incoming_plan)
    _write(children[0] / "job.json", {"status": "failed"})
    (children[0] / "recipe.json").write_text("{")
    exported = _export_collection(source_project, parent, tmp_path / "partial.zip")
    import_handoff_bundle(exported.path, target_root, project_root=target_project)
    assert {p.relative_to(local_first): p.read_bytes() for p in local_first.rglob("*") if p.is_file()} == local_originals
    assert load_dish_plan(restored)["dishes"][0]["status"] == "done"
    assert is_complete_output(local_second)


def test_generation_task_detail_reports_partial_dishes_without_changing_batch_schema(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from bili_recipe_notes import admin_tasks
    from bili_recipe_notes.admin_common import AdminContext
    from bili_recipe_notes.batch_queue import create_batch_state
    parent, _ = collection(tmp_path)
    plan = load_dish_plan(parent)
    plan["dishes"][1].update(status="failed", error="模型超时")
    _write(parent / "dish-plan.json", plan)
    store = MobileSyncStore(tmp_path)
    ctx = AdminContext(store, SimpleNamespace())
    state = create_batch_state(["https://www.bilibili.com/video/BV1demo"],
                               {"out": str(parent.parent), "target_stage": "recipe"}, project_root=tmp_path)
    state.items[0].output_folder, state.items[0].status = str(parent), "failed"
    monkeypatch.setattr(admin_tasks.generation, "get_background_batch_status", lambda *_: None)
    detail = admin_tasks._generation_state(ctx, state, detail=True)
    item = detail["items"][0]
    assert item["status"] == "failed" and item["dish_count"] == 2 and item["completed_dish_count"] == 1
    assert item["dishes"][1]["error"] == "模型超时"
    assert not hasattr(state.items[0], "dishes")
