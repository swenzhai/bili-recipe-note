from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from bili_recipe_notes import data_migration as migration
from bili_recipe_notes.data_paths import DATA_DIR_ENV, relocate_value
from bili_recipe_notes.storage import atomic_write_json


@pytest.fixture(autouse=True)
def isolated_root(monkeypatch, tmp_path):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)


def _recipe(folder: Path, bvid: str = "BVdemo", uploader: str = "厨师") -> None:
    atomic_write_json(folder / "source.json", {"source_url": f"https://www.bilibili.com/video/{bvid}", "uploader": uploader})
    atomic_write_json(folder / "recipe.json", {"title": "菜谱", "steps": [], "uploader": uploader})
    atomic_write_json(folder / "recipe.ai-review.json", {"visibility_override": "show"})
    (folder / "note.md").write_bytes(b"# Original\r\n")


def _creator(folder: Path, stamp: str, urls: list[str]) -> None:
    atomic_write_json(folder / "creator.json", {"uid": "42", "crawled_at": stamp, "uploader": folder.name})
    (folder / "video_links.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")


def test_migration_resumes_and_keeps_recipe_and_creator_bytes(monkeypatch, tmp_path: Path) -> None:
    old_recipe = tmp_path / "outputs" / "raw"
    _recipe(old_recipe)
    _recipe(tmp_path / "outputs" / "old_video", "BVold", "旧名字")
    _creator(tmp_path / "creators" / "最新名字", "2026-09-30", ["https://www.bilibili.com/video/BVdemo"])
    _creator(tmp_path / "outputs" / "creators" / "旧名字", "2026-01-01", ["https://www.bilibili.com/video/BVold"])
    atomic_write_json(tmp_path / ".bili-recipe-notes" / "migrations" / "old.json", {"old": "preserve"})
    plan = migration.plan_data_migration(tmp_path)
    expected = {move["source"]: migration._inventory(Path(move["source"])) for move in plan["moves"]}
    real_move = migration._move_verified
    count = 0

    def interrupted(source, target, inventory):
        nonlocal count
        count += 1
        if count == 3:
            raise RuntimeError("interrupted")
        real_move(source, target, inventory)

    monkeypatch.setattr(migration, "_move_verified", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        migration.apply_data_migration(plan)
    monkeypatch.setattr(migration, "_move_verified", real_move)
    resumed = migration.apply_data_migration(migration.plan_data_migration(tmp_path))

    assert resumed["status"] == "completed"
    assert (tmp_path / "data" / "recipes" / "42-最新名字" / "raw").is_dir()
    assert (tmp_path / "data" / "recipes" / "42-最新名字" / "old_video").is_dir()
    assert (tmp_path / "data" / "state" / "migrations" / "legacy" / "old.json").is_file()
    for move in resumed["moves"]:
        assert migration._inventory(Path(move["target"])) == expected[move["source"]]
        assert not Path(move["source"]).exists()
    assert migration.apply_data_migration(resumed) == resumed


def test_nested_recipes_migrate_but_parser_intermediates_remain_source_tree(tmp_path: Path) -> None:
    recipe = tmp_path / "outputs" / "分组" / "菜谱"
    _recipe(recipe)
    parsed = tmp_path / "outputs2" / "面包书" / "recipes" / "面包"
    atomic_write_json(parsed / "recipe.json", {
        "title": "面包", "ingredients_raw": ["面粉"], "steps": ["混合"], "source_pdf": "面包书.pdf",
    })
    plan = migration.plan_data_migration(tmp_path)
    migration.apply_data_migration(plan)

    assert (tmp_path / "data" / "recipes" / "厨师" / "菜谱" / "recipe.json").is_file()
    assert (tmp_path / "data" / "sources" / "outputs2" / "面包书" / "recipes" / "面包" / "recipe.json").is_file()
    assert len(plan["recipe_paths"]) == 1


def test_additional_migration_keeps_previous_path_mappings_and_roots(tmp_path: Path) -> None:
    first = tmp_path / "outputs" / "first"
    _recipe(first)
    migration.apply_data_migration(migration.plan_data_migration(tmp_path))
    layout_path = tmp_path / "data" / "state" / "layout.json"
    layout = json.loads(layout_path.read_text())
    layout["data_roots"] = ["/previous/server/data", str(tmp_path / "data")]
    atomic_write_json(layout_path, layout)
    second = tmp_path / "outputs2" / "second"
    _recipe(second, "BVsecond")
    migration.apply_data_migration(migration.plan_data_migration(tmp_path))

    updated = json.loads(layout_path.read_text())
    assert "/previous/server/data" in updated["data_roots"]
    assert str(first) in updated["path_mappings"]
    assert str(second) in updated["path_mappings"]
    assert relocate_value(str(first / "note.md"), tmp_path) == str(tmp_path / "data" / "recipes" / "厨师" / "first" / "note.md")


def test_cross_filesystem_resume_finishes_verified_copy_without_losing_source(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "outputs" / "old"
    _recipe(source)
    plan = migration.plan_data_migration(tmp_path)
    target = Path(plan["moves"][0]["target"])
    real_rename = Path.rename
    real_remove = migration._remove_source

    def across_devices(path, destination):
        if path == source:
            raise OSError(errno.EXDEV, "cross device")
        return real_rename(path, destination)

    def interrupt_cleanup(path):
        if path == source:
            raise RuntimeError("stopped after verified copy")
        return real_remove(path)

    monkeypatch.setattr(Path, "rename", across_devices)
    monkeypatch.setattr(migration, "_remove_source", interrupt_cleanup)
    with pytest.raises(RuntimeError, match="verified copy"):
        migration.apply_data_migration(plan)
    assert source.is_dir() and target.is_dir()
    assert migration._inventory(source) == migration._inventory(target)
    monkeypatch.setattr(migration, "_remove_source", real_remove)

    assert migration.apply_data_migration(migration.plan_data_migration(tmp_path))["status"] == "completed"
    assert not source.exists()
    assert (target / "recipe.json").is_file()


def test_migration_rejects_nested_symlinks_before_moving_anything(tmp_path: Path) -> None:
    source = tmp_path / "outputs" / "old"
    _recipe(source)
    (source / "alias").symlink_to(source / "note.md")
    plan = migration.plan_data_migration(tmp_path)

    with pytest.raises(ValueError, match="Symlink"):
        migration.apply_data_migration(plan)
    assert source.is_dir()
    assert not Path(plan["moves"][0]["target"]).exists()


@pytest.mark.parametrize("status", ["running", "queued", "paused"])
def test_migration_rejects_active_tasks_and_conflicting_destinations(tmp_path: Path, status: str) -> None:
    source = tmp_path / "outputs" / "old"
    _recipe(source)
    runtime = tmp_path / ".bili-recipe-notes" / "batches" / "runtime" / "active.status.json"
    atomic_write_json(runtime, {"status": status, "pid": os.getpid()})
    plan = migration.plan_data_migration(tmp_path)
    with pytest.raises(RuntimeError, match="running task"):
        migration.apply_data_migration(plan)
    assert source.is_dir()
    runtime.unlink()
    target = Path(plan["moves"][0]["target"])
    target.mkdir(parents=True)
    (target / "foreign.txt").write_text("must stay")
    with pytest.raises(FileExistsError):
        migration.apply_data_migration(plan)
    assert (target / "foreign.txt").read_text() == "must stay"
    assert source.is_dir()


def test_migration_resumes_dead_owner_lock_but_retains_corrupt_lock(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "outputs" / "old"
    _recipe(source)
    plan = migration.plan_data_migration(tmp_path)
    manifest = Path(plan["manifest_path"])
    manifest.parent.mkdir(parents=True)
    lock = manifest.with_name(manifest.name + ".lock")
    lock.write_text("corrupt lock")
    with pytest.raises(RuntimeError, match="Invalid migration lock"):
        migration.apply_data_migration(plan)
    assert lock.read_text() == "corrupt lock" and source.exists()
    lock.write_text("pid=999999 created=123\n")
    monkeypatch.setattr(migration, "_process_alive", lambda pid: pid != 999999)

    assert migration.apply_data_migration(plan)["status"] == "completed"
    assert not lock.exists()
    assert relocate_value("obsidian-vault", tmp_path) == str(tmp_path / "data" / "exports" / "obsidian-vault")
