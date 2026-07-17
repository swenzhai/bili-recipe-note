from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from bili_recipe_notes.batch_queue import (
    BatchStageState,
    create_batch_state,
    load_batch_state,
    save_batch_state,
)
from bili_recipe_notes.handoff import HandoffError, export_batch_handoff, import_handoff_bundle
from bili_recipe_notes.history import is_complete_output, is_raw_output


def _write_raw(folder: Path, url: str) -> None:
    folder.mkdir(parents=True)
    source = {
        "source_url": url,
        "video_title": "番茄炒蛋",
        "uploader": "厨房老师",
        "bvid": url.rsplit("/", 1)[-1],
        "duration": 60,
    }
    (folder / "source.json").write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    (folder / "transcript.json").write_text(
        json.dumps([{"start": 0, "end": 5, "text": "先把鸡蛋炒熟"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (folder / "job.json").write_text(
        json.dumps(
            {
                **source,
                "status": "raw_ready",
                "output_folder": str(folder.resolve()),
                "source_path": str((folder / "source.json").resolve()),
                "transcript_path": str((folder / "transcript.json").resolve()),
                "stages": {"raw": {"status": "done"}, "recipe": {"status": "pending"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_recipe(folder: Path, url: str) -> None:
    _write_raw(folder, url)
    recipe = {
        "title": "番茄炒蛋",
        "source_url": url,
        "ingredients": [{"name": "鸡蛋", "amount": "2个"}],
        "seasonings": [],
        "tools": ["炒锅"],
        "steps": [{"title": "炒制", "start_time": 0, "action": "炒熟鸡蛋"}],
        "summary_tips": [],
        "uncertain_points": [],
    }
    (folder / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    (folder / "note.md").write_text("# 番茄炒蛋\n\n炒熟鸡蛋。\n", encoding="utf-8")
    images = folder / "images"
    images.mkdir()
    (images / "step_01.jpg").write_bytes(b"image")
    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    job.update(
        {
            "status": "done",
            "recipe_path": str((folder / "recipe.json").resolve()),
            "note_path": str((folder / "note.md").resolve()),
            "stages": {"raw": {"status": "done"}, "recipe": {"status": "done"}},
        }
    )
    (folder / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")


def test_batch_handoff_round_trip_keeps_work_and_remaps_paths(tmp_path: Path) -> None:
    source_root = tmp_path / "mac"
    source_out = source_root / "outputs"
    source_out.mkdir(parents=True)
    raw_url = "https://www.bilibili.com/video/BV1raw"
    recipe_url = "https://www.bilibili.com/video/BV1recipe"
    pending_url = "https://www.bilibili.com/video/BV1pending"
    raw_folder = source_out / "raw-result"
    recipe_folder = source_out / "recipe-result"
    _write_raw(raw_folder, raw_url)
    _write_recipe(recipe_folder, recipe_url)
    (recipe_folder / "media").mkdir()
    (recipe_folder / "media" / "audio.m4a").write_bytes(b"must-not-transfer")

    state = create_batch_state(
        [raw_url, recipe_url, pending_url],
        {
            "cookies": "VERY-SECRET-COOKIE",
            "out": str(source_out.resolve()),
            "local_llm_command": "/usr/local/bin/my-llm",
            "target_stage": "recipe",
        },
        batch_id="handoff-demo",
        project_root=source_root,
    )
    state.items[0].output_folder = str(raw_folder.resolve())
    state.items[0].status = "raw_ready"
    state.items[0].stages["raw"] = BatchStageState(status="done")
    state.items[1].output_folder = str(recipe_folder.resolve())
    state.items[1].note_path = str((recipe_folder / "note.md").resolve())
    state.items[1].status = "done"
    state.items[1].stages["raw"] = BatchStageState(status="done")
    state.items[1].stages["recipe"] = BatchStageState(status="done")
    save_batch_state(state, project_root=source_root)

    creator = source_out / "creators" / "42-demo"
    creator.mkdir(parents=True)
    (creator / "video_links.txt").write_text(f"{raw_url}\n{recipe_url}\n", encoding="utf-8")
    (creator / "creator.json").write_text(
        json.dumps({"uid": "42", "videos": [{"url": raw_url}, {"url": recipe_url}]}), encoding="utf-8"
    )

    exported = export_batch_handoff(
        state.batch_id,
        source_out,
        destination=tmp_path / "transfer.zip",
        project_root=source_root,
    )

    assert exported.raw_count == 1
    assert exported.recipe_count == 1
    with zipfile.ZipFile(exported.path) as archive:
        names = set(archive.namelist())
        all_bytes = b"".join(archive.read(name) for name in names)
        assert "handoff.json" in names
        assert any(name.endswith("/images/step_01.jpg") for name in names)
        assert not any("media/" in name for name in names)
        assert b"VERY-SECRET-COOKIE" not in all_bytes
        assert str(source_root).encode() not in all_bytes

    destination_root = tmp_path / "windows"
    destination_out = destination_root / "outputs"
    imported = import_handoff_bundle(exported.path, destination_out, project_root=destination_root)
    imported_state = load_batch_state(imported.batch_id, project_root=destination_root)

    assert imported.item_count == 3
    assert imported.raw_count == 1
    assert imported.recipe_count == 1
    assert imported.pending_count == 1
    assert imported.creator_document_count == 2
    imported_raw = next(item for item in imported_state.items if item.url == raw_url)
    imported_recipe = next(item for item in imported_state.items if item.url == recipe_url)
    imported_pending = next(item for item in imported_state.items if item.url == pending_url)
    assert is_raw_output(Path(imported_raw.output_folder or ""))
    assert is_complete_output(Path(imported_recipe.output_folder or ""))
    assert imported_pending.status == "pending"
    assert imported_pending.output_folder is None
    assert str(destination_out.resolve()) in (imported_raw.output_folder or "")
    imported_job = json.loads((Path(imported_recipe.output_folder or "") / "job.json").read_text(encoding="utf-8"))
    assert imported_job["output_folder"] == imported_recipe.output_folder
    assert imported_state.options["out"] == str(destination_out.resolve())
    assert "cookies" not in imported_state.options


def test_import_rejects_zip_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "bad")
        archive.writestr("handoff.json", "{}")
        archive.writestr("batch.json", "{}")

    with pytest.raises(HandoffError, match="不安全路径"):
        import_handoff_bundle(archive_path, tmp_path / "outputs", project_root=tmp_path)


def test_import_preserves_more_complete_local_output(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_out = source_root / "outputs"
    source_out.mkdir(parents=True)
    url = "https://www.bilibili.com/video/BV1same"
    raw_folder = source_out / "same-result"
    _write_raw(raw_folder, url)
    source_state = create_batch_state([url], {}, batch_id="same-batch", project_root=source_root)
    source_state.items[0].output_folder = str(raw_folder)
    source_state.items[0].stages["raw"].status = "done"
    source_state.items[0].status = "raw_ready"
    save_batch_state(source_state, project_root=source_root)
    exported = export_batch_handoff(
        source_state.batch_id,
        source_out,
        destination=tmp_path / "raw-transfer.zip",
        project_root=source_root,
    )

    destination_root = tmp_path / "destination"
    destination_out = destination_root / "outputs"
    local_folder = destination_out / "same-result"
    _write_recipe(local_folder, url)
    local_state = create_batch_state([url], {}, batch_id="same-batch", project_root=destination_root)
    local_state.items[0].output_folder = str(local_folder)
    local_state.items[0].stages["raw"].status = "done"
    local_state.items[0].stages["recipe"].status = "done"
    local_state.items[0].status = "done"
    save_batch_state(local_state, project_root=destination_root)

    imported = import_handoff_bundle(exported.path, destination_out, project_root=destination_root)

    assert imported.recipe_count == 1
    assert imported.raw_count == 0
    assert is_complete_output(local_folder)
    assert not (local_folder / "job.json.bak").exists()


def test_same_stage_import_updates_work_and_keeps_local_backup(tmp_path: Path) -> None:
    url = "https://www.bilibili.com/video/BV1roundtrip"
    source_root = tmp_path / "windows"
    source_out = source_root / "outputs"
    incoming_folder = source_out / "roundtrip-result"
    _write_recipe(incoming_folder, url)
    incoming_job = json.loads((incoming_folder / "job.json").read_text(encoding="utf-8"))
    incoming_job["machine_marker"] = "windows-newer"
    (incoming_folder / "job.json").write_text(json.dumps(incoming_job), encoding="utf-8")
    state = create_batch_state([url], {}, batch_id="roundtrip", project_root=source_root)
    state.items[0].output_folder = str(incoming_folder)
    state.items[0].stages["raw"].status = "done"
    state.items[0].stages["recipe"].status = "done"
    state.items[0].status = "done"
    save_batch_state(state, project_root=source_root)
    exported = export_batch_handoff(
        state.batch_id,
        source_out,
        destination=tmp_path / "roundtrip.zip",
        project_root=source_root,
    )

    destination_root = tmp_path / "mac"
    destination_out = destination_root / "outputs"
    local_folder = destination_out / "roundtrip-result"
    _write_recipe(local_folder, url)
    local_job = json.loads((local_folder / "job.json").read_text(encoding="utf-8"))
    local_job["machine_marker"] = "mac-older"
    (local_folder / "job.json").write_text(json.dumps(local_job), encoding="utf-8")
    local_state = create_batch_state([url], {}, batch_id="roundtrip", project_root=destination_root)
    local_state.items[0].output_folder = str(local_folder)
    local_state.items[0].stages["raw"].status = "done"
    local_state.items[0].stages["recipe"].status = "done"
    local_state.items[0].status = "done"
    save_batch_state(local_state, project_root=destination_root)

    import_handoff_bundle(exported.path, destination_out, project_root=destination_root)

    updated_job = json.loads((local_folder / "job.json").read_text(encoding="utf-8"))
    backed_up_job = json.loads((local_folder / "job.json.bak").read_text(encoding="utf-8"))
    assert updated_job["machine_marker"] == "windows-newer"
    assert updated_job["output_folder"] == str(local_folder)
    assert backed_up_job["machine_marker"] == "mac-older"
