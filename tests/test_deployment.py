from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

from bili_recipe_notes.deployment import export_deployment_bundle, validate_deployment_bundle


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
        assert "bili-recipe-notes/bili_recipe_notes/ui.py" in names
        assert "bili-recipe-notes/start-ui-linux.sh" in names
        launcher_info = archive.getinfo("bili-recipe-notes/start-ui-linux.sh")
        assert ((launcher_info.external_attr >> 16) & stat.S_IXUSR) != 0
        assert "bili-recipe-notes/outputs/宫保鸡丁--BV1demo/recipe.json" in names
        assert "bili-recipe-notes/outputs/宫保鸡丁--BV1demo/images/step_01.jpg" in names
        assert "bili-recipe-notes/outputs/curation-review/curation-decisions.json" in names
        assert "bili-recipe-notes/DEPLOYMENT.md" in names
        assert not any(name.endswith("media.mp4") for name in names)
        assert not any(name.endswith("job.json.bak") for name in names)
        assert not any(name.endswith("run.sh") for name in names)
        assert not any(".venv" in name for name in names)

        config = json.loads(archive.read("bili-recipe-notes/.bili-recipe-notes/config.json"))
        assert config["out_dir"] == "outputs"
        assert config["cookies"] is None
        assert config["obsidian_vault_dir"] == "obsidian-vault"
        batch = json.loads(
            archive.read("bili-recipe-notes/.bili-recipe-notes/batches/batch-demo.json")
        )
        assert batch["options"]["out"] == "outputs"
        assert batch["options"]["cookies"] is None
        assert batch["items"][0]["output_folder"] == "outputs/宫保鸡丁--BV1demo"
        assert batch["items"][0]["note_path"] == "outputs/宫保鸡丁--BV1demo/note.md"
        job = json.loads(archive.read("bili-recipe-notes/outputs/宫保鸡丁--BV1demo/job.json"))
        assert job["output_folder"] == "outputs/宫保鸡丁--BV1demo"
        assert job["recipe_path"] == "outputs/宫保鸡丁--BV1demo/recipe.json"
        review = json.loads(
            archive.read("bili-recipe-notes/outputs/curation-review/recipe-review.json")
        )
        assert review["source_output_dir"] == "outputs"
        assert review["groups"][0]["items"][0]["output_folder"] == "outputs/宫保鸡丁--BV1demo"

    assert manifest["output_file_count"] == 5
    assert result.archive_size_bytes > 0
    assert result.checksum_path.read_text(encoding="utf-8") == f"{result.sha256}  transfer.zip\n"
