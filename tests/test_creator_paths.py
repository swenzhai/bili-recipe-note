from __future__ import annotations

import json
from pathlib import Path

from bili_recipe_notes import pipeline
from bili_recipe_notes.creator_paths import creator_directory, creator_uid
from bili_recipe_notes.history import find_history_record_by_url
from bili_recipe_notes.recipe_extractor import TranscriptSegment


def test_new_outputs_group_by_uid_and_reuse_group_after_creator_rename(monkeypatch, tmp_path: Path) -> None:
    info = {"title": "炒鸡蛋", "uploader": "厨房老师", "uploader_id": "123"}
    monkeypatch.setattr(pipeline, "fetch_video_info", lambda *args, **kwargs: info)
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, folder, **kwargs: [folder / "sub.vtt"])
    monkeypatch.setattr(
        pipeline, "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0, end=1, text="把鸡蛋炒熟")],
    )
    out = tmp_path / "recipes"
    first = pipeline.capture_raw_material(pipeline.RecipeJobOptions(url="https://example.com/one", out=str(out)))
    info["uploader"] = "改名的老师"
    second = pipeline.capture_raw_material(pipeline.RecipeJobOptions(url="https://example.com/two", out=str(out)))
    info["uploader_id"] = "456"
    third = pipeline.capture_raw_material(pipeline.RecipeJobOptions(url="https://example.com/three", out=str(out)))

    assert first.output_folder.parent == out / "123-厨房老师"
    assert second.output_folder.parent == first.output_folder.parent
    assert third.output_folder.parent == out / "456-改名的老师"
    assert json.loads(first.source_path.read_text())["creator_uid"] == "123"
    found = find_history_record_by_url(out, "https://example.com/one")
    assert found and found.output_folder == first.output_folder


def test_capture_retry_reuses_flat_legacy_output(monkeypatch, tmp_path: Path) -> None:
    folder = tmp_path / "recipes" / "旧目录"
    folder.mkdir(parents=True)
    url = "https://example.com/legacy"
    (folder / "job.json").write_text(json.dumps({"source_url": url, "status": "failed"}))
    monkeypatch.setattr(pipeline, "fetch_video_info", lambda *args, **kwargs: {"uploader": "厨师", "uploader_id": 123})
    monkeypatch.setattr(pipeline, "download_subtitles", lambda url, folder, **kwargs: [folder / "sub.vtt"])
    monkeypatch.setattr(
        pipeline, "parse_subtitle_file",
        lambda path: [TranscriptSegment(start=0, end=1, text="炒熟")],
    )

    result = pipeline.capture_raw_material(pipeline.RecipeJobOptions(url=url, out=str(folder.parent)))

    assert result.output_folder == folder
    assert not (folder.parent / "123-厨师").exists()


def test_creator_identity_supports_homepage_and_keeps_untrusted_names_visible(tmp_path: Path) -> None:
    assert creator_uid({"uploader_url": "https://space.bilibili.com/123/video"}) == "123"
    assert creator_uid({"owner": {"mid": 123}}) == "123"
    assert creator_uid({"uploader_url": "https://example.com/123"}) is None
    assert creator_directory(tmp_path, {"uploader": ".hidden"}).name == "UP主-.hidden"
    assert creator_directory(tmp_path, {"uploader": "media"}).name == "UP主-media"
    assert creator_directory(tmp_path, {"uploader": "../someone"}).parent == tmp_path


def test_creator_archive_reuses_name_only_directory_by_manifest_uid(tmp_path: Path) -> None:
    legacy = tmp_path / "旧名称"
    legacy.mkdir()
    (legacy / "creator.json").write_text(json.dumps({"uid": "123", "uploader": "旧名称"}))

    assert creator_directory(tmp_path, {"uid": "123", "uploader": "新名称"}) == legacy
    assert creator_directory(tmp_path, {"uid": "456", "uploader": "旧名称"}) == tmp_path / "456-旧名称"
