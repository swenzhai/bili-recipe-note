from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3

import pytest

from bili_recipe_notes import ai_review_runner as runner
from bili_recipe_notes.ai_review_ui import _review_records
from bili_recipe_notes.history import scan_history
from bili_recipe_notes.curation import build_curation_review, load_curation_review
from bili_recipe_notes.mobile_sync import MobileSyncStore
from bili_recipe_notes.output_layout import iter_output_folders
from bili_recipe_notes.review_policy import list_review_history, set_ai_review_visibility
from test_ai_review import write_recipe
from test_mobile_sync import _recipe


def test_nested_discovery_review_scope_and_distinct_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "recipes"
    (root / "123-甲").mkdir(parents=True)
    (root / "456-乙").mkdir(parents=True)
    first = write_recipe(root / "123-甲", "红烧肉")
    second = write_recipe(root / "456-乙", "红烧肉")
    originals = {path: path.read_bytes() for folder in (first, second) for path in folder.iterdir() if path.is_file()}
    assert set(iter_output_folders(root)) == {first, second}
    assert {item.output_folder for item in scan_history(root)} == {first, second}
    from bili_recipe_notes.ui import _history_options
    assert len(_history_options(scan_history(root))) == 2
    report = build_curation_review(root, root / "curation-review")
    rows = load_curation_review(report.json_path)["groups"][0]["items"]
    assert {row["item_id"] for row in rows} == {"123-甲/红烧肉", "456-乙/红烧肉"}
    assert len(_review_records(root)) == 2
    monkeypatch.setattr(runner, "_launch", lambda root, state: runner._write(root, state))
    state = runner.start_ai_review_batch(root, provider="local", only_pending=False)
    assert {item["folder"] for item in state["items"]} == {"123-甲/红烧肉", "456-乙/红烧肉"}
    for folder, visibility in ((first, "visible"), (second, "hidden")):
        set_ai_review_visibility(folder, visibility)
        set_ai_review_visibility(folder, None)
        assert list_review_history(folder)[0]["review"]["visibility_override"] == visibility
    assert all(path.read_bytes() == content for path, content in originals.items())
    (tmp_path / "elsewhere").mkdir()
    outside = write_recipe(tmp_path / "elsewhere", "outside")
    (root / "123-甲" / "link").symlink_to(outside, target_is_directory=True)
    assert outside not in set(iter_output_folders(root))
    with pytest.raises(ValueError, match="符号链接"):
        runner.start_ai_review_batch(root, provider="local", folders=[root / "123-甲" / "link"])


def test_move_data_keeps_publication_devices_photos_and_recovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = _recipe(tmp_path, "123-甲/红烧肉")
    store = MobileSyncStore(tmp_path)
    assert store.index_recipes()["indexed"] == 1
    recipe_id = json.loads((folder / "sync-meta.json").read_text())["recipe_id"]
    store.set_recipe_publications({recipe_id: False})
    photo = b"practice photo"
    digest = hashlib.sha256(photo).hexdigest()
    store.store_asset(digest, photo, "image/jpeg")
    original = (folder / "recipe.json").read_bytes()
    destination = tmp_path / "portable-data"
    shutil.move(str(tmp_path / "data"), destination)
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(destination))
    restored = MobileSyncStore(tmp_path)
    moved = destination / "recipes" / "123-甲" / "红烧肉"
    assert restored.asset_path(digest)[0].read_bytes() == photo
    assert not restored.recipe_visibility_states()[str(moved)]["published"]
    assert restored.index_recipes()["deleted"] == 0
    assert restored.index_recipes()["changed"] == 0
    # A malformed recipe retains its last good index and recovers after repair.
    (moved / "recipe.json").write_text("{")
    broken = restored.index_recipes()
    assert broken["deleted"] == 0 and broken["errors"]
    (moved / "recipe.json").write_bytes(original)
    assert restored.index_recipes()["errors"] == []
    assert not restored.recipe_visibility_states()[str(moved)]["published"]


def test_readonly_visibility_resolves_portable_paths_using_explicit_project_root(tmp_path, monkeypatch):
    from bili_recipe_notes.mobile_sync import recipe_visibility_states

    project = tmp_path / "restored-project"
    project.mkdir()
    elsewhere = tmp_path / "different-working-directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.delenv("BILI_RECIPE_DATA_DIR", raising=False)
    folder = _recipe(project, "123-甲/红烧肉")
    state = project / "data" / "state"
    state.mkdir()
    with sqlite3.connect(state / "mobile-sync.sqlite3") as connection:
        connection.execute("CREATE TABLE recipes(id TEXT, output_folder TEXT, published INTEGER)")
        connection.execute("INSERT INTO recipes VALUES (?, ?, ?)", ("recipe-id", "data/recipes/123-甲/红烧肉", 0))
    assert not (folder / "sync-meta.json").exists()
    before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    states = recipe_visibility_states(project / "data" / "recipes", project_root=project)
    assert states[str(folder)]["published"] is False
    assert {path: path.read_bytes() for path in project.rglob("*") if path.is_file()} == before
    assert not (elsewhere / "data").exists()
