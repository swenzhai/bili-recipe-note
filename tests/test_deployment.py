from __future__ import annotations

import json
import sqlite3
import zipfile

import pytest
from pathlib import Path

from bili_recipe_notes.deployment import export_deployment_bundle, validate_deployment_bundle
from bili_recipe_notes.storage import CorruptDataError


def test_deployment_bundle_contains_portable_app_outputs_and_curation_state(tmp_path: Path) -> None:
    (tmp_path / "bili_recipe_notes").mkdir()
    (tmp_path / "bili_recipe_notes" / "ui.py").write_text("print('ui')\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("streamlit==1.58.0\n", encoding="utf-8")
    linux_launcher = tmp_path / "start-ui-linux.sh"
    linux_launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    linux_launcher.chmod(0o755)
    (tmp_path / "run.sh").write_text("private helper\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "secret.txt").write_text("not portable\n", encoding="utf-8")

    recipe_folder = tmp_path / "outputs" / "宫保鸡丁--BV1demo"
    (recipe_folder / "images").mkdir(parents=True)
    (recipe_folder / "recipe.json").write_text(
        json.dumps({"title": "宫保鸡丁", "steps": [{"action": "炒"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (recipe_folder / "job.json").write_text(
        json.dumps(
            {
                "output_folder": "/old/project/outputs/宫保鸡丁--BV1demo",
                "recipe_path": "/old/project/outputs/宫保鸡丁--BV1demo/recipe.json",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (recipe_folder / "job.json.bak").write_text("backup\n", encoding="utf-8")
    (recipe_folder / "media.mp4").write_bytes(b"video")
    (recipe_folder / "images" / "step_01.jpg").write_bytes(b"jpeg")
    review_dir = tmp_path / "outputs" / "curation-review"
    review_dir.mkdir()
    (review_dir / "recipe-review.json").write_text(
        json.dumps(
            {
                "source_output_dir": "/old/project/outputs",
                "groups": [
                    {
                        "items": [
                            {
                                "item_id": "宫保鸡丁--BV1demo",
                                "output_folder": "/old/project/outputs/宫保鸡丁--BV1demo",
                            }
                        ]
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (review_dir / "curation-decisions.json").write_text(
        json.dumps({"schema_version": 1, "items": {"demo": {"decision": "keep_primary"}}}),
        encoding="utf-8",
    )

    config_root = tmp_path / ".bili-recipe-notes"
    (config_root / "batches").mkdir(parents=True)
    (config_root / "config.json").write_text(
        json.dumps(
            {
                "out_dir": "/old/project/outputs",
                "cookies": "/secret/cookies.txt",
                "obsidian_vault_dir": "/old/vault",
            }
        ),
        encoding="utf-8",
    )
    (config_root / "meal-plans.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plans": [{"id": "family", "name": "四人家庭餐", "items": []}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (config_root / "knowledge_base.json").write_text(
        json.dumps({"version": 1, "entries": [{"id": "tip", "title": "热锅", "category": "火候", "content": "先热锅"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (config_root / "batches" / "batch-demo.json").write_text(
        json.dumps(
            {
                "options": {"out": "/old/project/outputs", "cookies": "/secret/cookies.txt"},
                "items": [
                    {
                        "output_folder": "/old/project/outputs/宫保鸡丁--BV1demo",
                        "note_path": "/old/project/outputs/宫保鸡丁--BV1demo/note.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = export_deployment_bundle(
        tmp_path / "outputs",
        tmp_path / "transfer.zip",
        project_root=tmp_path,
    )
    manifest = validate_deployment_bundle(result.path)

    with zipfile.ZipFile(result.path) as archive:
        names = set(archive.namelist())
        assert "bili-recipe-notes/bili_recipe_notes/ui.py" not in names
        assert "bili-recipe-notes/start-ui-linux.sh" not in names
        assert "bili-recipe-notes/data/recipes/宫保鸡丁--BV1demo/recipe.json" in names
        assert "bili-recipe-notes/data/recipes/宫保鸡丁--BV1demo/images/step_01.jpg" in names
        assert "bili-recipe-notes/data/recipes/curation-review/curation-decisions.json" in names
        assert "bili-recipe-notes/DATA-MIGRATION.md" in names
        assert "bili-recipe-notes/data/state/meal-plans.json" in names
        assert "bili-recipe-notes/data/state/knowledge_base.json" in names
        assert not any(name.endswith("media.mp4") for name in names)
        assert not any(name.endswith("job.json.bak") for name in names)
        assert not any(name.endswith("run.sh") for name in names)
        assert not any(".venv" in name for name in names)

        config = json.loads(archive.read("bili-recipe-notes/data/state/config.json"))
        assert config["out_dir"] == "data/recipes"
        assert config["cookies"] is None
        assert config["obsidian_vault_dir"] == "data/exports/obsidian-vault"
        batch = json.loads(
            archive.read("bili-recipe-notes/data/state/batches/batch-demo.json")
        )
        assert "data_only" in manifest and manifest["data_only"] is True
        assert manifest["data_root"] == "data"
        assert manifest["app_file_count"] == 0
        assert batch["options"]["out"] == "data/recipes"
        assert batch["options"]["cookies"] is None
        assert batch["items"][0]["output_folder"] == "data/recipes/宫保鸡丁--BV1demo"
        assert batch["items"][0]["note_path"] == "data/recipes/宫保鸡丁--BV1demo/note.md"
        job = json.loads(archive.read("bili-recipe-notes/data/recipes/宫保鸡丁--BV1demo/job.json"))
        assert job["output_folder"] == "data/recipes/宫保鸡丁--BV1demo"
        assert job["recipe_path"] == "data/recipes/宫保鸡丁--BV1demo/recipe.json"
        review = json.loads(
            archive.read("bili-recipe-notes/data/recipes/curation-review/recipe-review.json")
        )
        assert review["source_output_dir"] == "data/recipes"
        assert review["groups"][0]["items"][0]["output_folder"] == "data/recipes/宫保鸡丁--BV1demo"

    assert manifest["output_file_count"] == 5
    assert result.archive_size_bytes > 0
    assert result.checksum_path.read_text(encoding="utf-8") == f"{result.sha256}  transfer.zip\n"


def test_consolidated_bundle_preserves_creator_hierarchy_and_live_database(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "separate-disk" / "kitchen"
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", str(data))
    folder = data / "recipes" / "123-厨师" / "鸡丁--BV1demo"
    folder.mkdir(parents=True)
    (folder / "recipe.json").write_text('{"title":"鸡丁","steps":[]}', encoding="utf-8")
    state = data / "state"
    (state / "batches").mkdir(parents=True)
    old_folder = "/old/project/outputs/鸡丁--BV1demo"
    original_batch = {"out_dir": "/old/project/outputs", "items": [{"output_folder": old_folder, "note_path": old_folder + "/note.md"}]}
    (state / "layout.json").write_text(json.dumps({"project_root": "/old/project", "data_root": "/old/project/data", "path_mappings": {old_folder: "recipes/123-厨师/鸡丁--BV1demo"}}), encoding="utf-8")
    batch_path = state / "batches" / "batch.json"
    batch_path.write_text(json.dumps(original_batch), encoding="utf-8")
    (state / "cookies").mkdir()
    (state / "cookies" / "bilibili-edge.txt").write_text("secret", encoding="utf-8")
    (state / "mobile-media").mkdir()
    photo = state / "mobile-media" / "practice.jpg"
    photo.write_bytes(b"private practice photo")
    creator = data / "creators" / "123-厨师"
    creator.mkdir(parents=True)
    (creator / "video_links.txt").write_text("https://www.bilibili.com/video/BV1demo\n", encoding="utf-8")
    sources = data / "sources"
    sources.mkdir()
    (sources / "book.pdf").write_bytes(b"source material")
    vault = data / "exports" / "obsidian-vault"
    vault.mkdir(parents=True)
    (vault / "手写笔记.md").write_text("我的调整", encoding="utf-8")
    database = state / "mobile-sync.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA wal_autocheckpoint=0;
            CREATE TABLE recipes(id TEXT PRIMARY KEY, output_folder TEXT, published INTEGER);
            CREATE TABLE assets(sha256 TEXT PRIMARY KEY, path TEXT);
            CREATE TABLE meal_plans(id TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE devices(id TEXT PRIMARY KEY, token_hash TEXT);
        """)
        connection.execute("INSERT INTO recipes VALUES (?, ?, ?)", ("r1", str(folder), 0))
        connection.execute("INSERT INTO assets VALUES (?, ?)", ("a1", str(photo)))
        connection.execute("INSERT INTO meal_plans VALUES (?, ?)", ("family", "家庭餐"))
        connection.execute("INSERT INTO devices VALUES (?, ?)", ("phone", "token-hash"))
        connection.commit()
        assert Path(str(database) + "-wal").stat().st_size > 0
        result = export_deployment_bundle(data / "recipes", project_root=tmp_path)
        # Exporting a portable copy must not rewrite live runtime paths.
        assert connection.execute("SELECT output_folder FROM recipes").fetchone()[0] == str(folder)
    assert result.path.parent == data / "exports" / "deployments"
    assert json.loads(batch_path.read_text(encoding="utf-8")) == original_batch
    with zipfile.ZipFile(result.path) as archive:
        names = archive.namelist()
        assert "bili-recipe-notes/data/recipes/123-厨师/鸡丁--BV1demo/recipe.json" in names
        assert "bili-recipe-notes/data/creators/123-厨师/video_links.txt" in names
        assert "bili-recipe-notes/data/state/mobile-media/practice.jpg" in names
        assert "bili-recipe-notes/data/sources/book.pdf" in names
        assert "bili-recipe-notes/data/exports/obsidian-vault/手写笔记.md" in names
        assert not any("cookies" in name or name.endswith(("-wal", "-shm")) for name in names)
        batch = json.loads(archive.read("bili-recipe-notes/data/state/batches/batch.json"))
        assert batch["items"][0]["output_folder"] == "data/recipes/123-厨师/鸡丁--BV1demo"
        snapshot = tmp_path / "restored.sqlite3"
        snapshot.write_bytes(archive.read("bili-recipe-notes/data/state/mobile-sync.sqlite3"))
    with sqlite3.connect(snapshot) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert restored.execute("SELECT output_folder,published FROM recipes").fetchone() == ("data/recipes/123-厨师/鸡丁--BV1demo", 0)
        assert restored.execute("SELECT path FROM assets").fetchone() == ("data/state/mobile-media/practice.jpg",)
        assert restored.execute("SELECT name FROM meal_plans").fetchone() == ("家庭餐",)
        assert restored.execute("SELECT token_hash FROM devices").fetchone() == ("token-hash",)


def test_windows_nested_runtime_paths_keep_creator_folder(tmp_path: Path) -> None:
    folder = tmp_path / "outputs" / "123-厨师" / "鸡丁--BV1demo"
    folder.mkdir(parents=True)
    (folder / "recipe.json").write_text("{}", encoding="utf-8")
    state = tmp_path / ".bili-recipe-notes" / "batches"
    state.mkdir(parents=True)
    (state / "batch.json").write_text(json.dumps({"items": [{"output_folder": r"C:\old\outputs\123-厨师\鸡丁--BV1demo", "note_path": r"C:\old\outputs\123-厨师\鸡丁--BV1demo\note.md"}]}), encoding="utf-8")
    result = export_deployment_bundle(tmp_path / "outputs", tmp_path / "transfer.zip", project_root=tmp_path)
    with zipfile.ZipFile(result.path) as archive:
        batch = json.loads(archive.read("bili-recipe-notes/data/state/batches/batch.json"))
    assert batch["items"][0]["output_folder"] == "data/recipes/123-厨师/鸡丁--BV1demo"
    assert batch["items"][0]["note_path"] == "data/recipes/123-厨师/鸡丁--BV1demo/note.md"


def test_export_rejects_corrupt_state_without_overwriting_previous_bundle(tmp_path: Path) -> None:
    state = tmp_path / "data" / "state"
    state.mkdir(parents=True)
    (state / "config.json").write_text("broken json", encoding="utf-8")
    target = tmp_path / "backup.zip"
    target.write_bytes(b"previous backup")
    with pytest.raises(CorruptDataError):
        export_deployment_bundle(tmp_path / "data" / "recipes", target, project_root=tmp_path)
    assert target.read_bytes() == b"previous backup"
