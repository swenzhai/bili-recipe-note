from __future__ import annotations

import json
from pathlib import Path

from bili_recipe_notes.batch_queue import create_batch_state, load_batch_state, save_batch_state
from bili_recipe_notes.output_folders import (
    apply_output_folder_migration,
    plan_output_folder_migration,
    rename_completed_output_folder,
)


def _write_output(folder: Path, *, title: str = "红烧肉", bvid: str = "BV1demo") -> None:
    folder.mkdir(parents=True)
    source_url = f"https://www.bilibili.com/video/{bvid}"
    (folder / "source.json").write_text(
        json.dumps({"source_url": source_url, "bvid": bvid}, ensure_ascii=False),
        encoding="utf-8",
    )
    (folder / "recipe.json").write_text(
        json.dumps({"title": title, "source_url": source_url}, ensure_ascii=False),
        encoding="utf-8",
    )
    (folder / "note.md").write_text(f"# {title}\n", encoding="utf-8")
    (folder / "job.json").write_text(
        json.dumps(
            {
                "source_url": source_url,
                "bvid": bvid,
                "output_folder": str(folder),
                "source_path": str(folder / "source.json"),
                "recipe_path": str(folder / "recipe.json"),
                "note_path": str(folder / "note.md"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_output_folder_migration_updates_jobs_and_batches(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    old_folder = tmp_path / "outputs" / "很长的视频宣传标题 - UP主 - BV1demo"
    _write_output(old_folder)
    state = create_batch_state(
        ["https://www.bilibili.com/video/BV1demo"],
        {},
        batch_id="rename-batch",
        project_root=tmp_path,
    )
    state.items[0].status = "done"
    state.items[0].output_folder = str(old_folder)
    state.items[0].note_path = str(old_folder / "note.md")
    save_batch_state(state, project_root=tmp_path)

    plans = plan_output_folder_migration(tmp_path / "outputs")
    assert [(plan.source.name, plan.target.name) for plan in plans] == [
        (old_folder.name, "红烧肉--BV1demo")
    ]

    result = apply_output_folder_migration(plans, project_root=tmp_path)
    new_folder = tmp_path / "outputs" / "红烧肉--BV1demo"
    assert result.renamed == 1
    assert result.manifest_path and result.manifest_path.is_file()
    assert not old_folder.exists()
    assert new_folder.is_dir()
    job = json.loads((new_folder / "job.json").read_text(encoding="utf-8"))
    assert job["output_folder"] == str(new_folder)
    assert job["note_path"] == str(new_folder / "note.md")
    updated = load_batch_state("rename-batch", project_root=tmp_path)
    assert updated.items[0].output_folder == str(new_folder)
    assert updated.items[0].note_path == str(new_folder / "note.md")


def test_raw_output_uses_pending_title_and_completed_output_uses_recipe_title(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    raw_folder = tmp_path / "outputs" / "旧视频名 - UP主 - BV1raw"
    raw_folder.mkdir(parents=True)
    source_url = "https://www.bilibili.com/video/BV1raw"
    (raw_folder / "source.json").write_text(
        json.dumps({"source_url": source_url, "bvid": "BV1raw"}), encoding="utf-8"
    )
    (raw_folder / "job.json").write_text(
        json.dumps({"source_url": source_url, "bvid": "BV1raw", "output_folder": str(raw_folder)}),
        encoding="utf-8",
    )

    plans = plan_output_folder_migration(tmp_path / "outputs")
    assert plans[0].target.name == "待整理--BV1raw"

    (raw_folder / "recipe.json").write_text(
        json.dumps({"title": "宫保鸡丁", "source_url": source_url}, ensure_ascii=False),
        encoding="utf-8",
    )
    renamed = rename_completed_output_folder(raw_folder)
    assert renamed.name == "宫保鸡丁--BV1raw"
    assert json.loads((renamed / "job.json").read_text())["output_folder"] == str(renamed)
