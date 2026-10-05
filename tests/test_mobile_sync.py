from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from bili_recipe_notes.mobile_sync import (
    AuthenticationError,
    MobileSyncStore,
    ValidationError,
    VisibilityStateError,
    is_private_client,
    recipe_visibility_states,
    token_hash,
)
from bili_recipe_notes.review_policy import (
    AI_REVIEW_FILE_NAME, AI_REVIEW_RULES_VERSION, AI_REVIEW_PROMPT_VERSION,
    review_recipe_hash, review_input_fingerprint, set_ai_review_visibility,
)


def _recipe(root: Path, name: str, *, bvid: str = "BV1demo", cid: str = "101") -> Path:
    folder = root / "data" / "recipes" / name
    images = folder / "images"
    images.mkdir(parents=True)
    (images / "step.jpg").write_bytes(b"fake-jpeg")
    (folder / "recipe.json").write_text(
        json.dumps(
            {
                "title": name,
                "source_url": f"https://www.bilibili.com/video/{bvid}",
                "ingredients": [{"name": "番茄", "amount": "2个"}],
                "seasonings": [],
                "tools": [],
                "steps": [
                    {
                        "title": "炒制",
                        "start_time": 1,
                        "action": "翻炒",
                        "screenshot_path": "images/step.jpg",
                    }
                ],
                "summary_tips": [],
                "uncertain_points": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (folder / "job.json").write_text(json.dumps({"bvid": bvid, "cid": cid}), encoding="utf-8")
    return folder


def _paired(store: MobileSyncStore) -> tuple[str, str]:
    credential = store.issue_pairing_credential("http://192.168.1.2:8765")
    paired = store.pair_device(credential.pairing_token, "测试手机")
    return str(paired["device_id"]), str(paired["access_token"])


def _upsert(log_id: str, recipe_id: str, *, base_version: int = 0, notes: str = "第一次实践") -> dict:
    return {
        "op_id": str(uuid.uuid4()),
        "entity_type": "practice_log",
        "entity_id": log_id,
        "action": "upsert",
        "base_version": base_version,
        "payload": {
            "id": log_id,
            "recipe_id": recipe_id,
            "cooked_on": "2026-07-15",
            "outcome": "success",
            "rating": 5,
            "notes": notes,
        },
    }


def test_recipe_index_uses_part_identity_assets_and_tombstones(tmp_path: Path) -> None:
    first = _recipe(tmp_path, "第一集", cid="101")
    second = _recipe(tmp_path, "第二集", cid="202")
    store = MobileSyncStore(tmp_path)

    indexed = store.index_recipes()
    assert indexed["indexed"] == 2
    assert indexed["changed"] == 2
    assert store.current_revision() == 2
    first_id = json.loads((first / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    second_id = json.loads((second / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    assert first_id != second_id

    assert store.index_recipes()["changed"] == 0
    first.rename(tmp_path / "removed")
    result = store.index_recipes()
    assert result["deleted"] == 1
    changes = store.sync(_paired(store)[0], 0, [])["changes"]
    assert any(change["entity_id"] == first_id and change["action"] == "delete" for change in changes)


def test_non_recipe_sources_are_remembered_excluded_and_reversible(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "广告视频")
    recipe_data = json.loads((folder / "recipe.json").read_text(encoding="utf-8"))
    recipe_data["creator_name"] = "厨房 UP 主"
    (folder / "recipe.json").write_text(json.dumps(recipe_data, ensure_ascii=False), encoding="utf-8")
    source_url = str(recipe_data["source_url"])
    store = MobileSyncStore(tmp_path)

    assert store.index_recipes()["indexed"] == 1
    source = store.list_video_sources()[0]
    assert source["classification"] == "recipe"
    assert source["creator_name"] == "厨房 UP 主"
    assert source["recipe_id"]

    assert store.set_video_classifications(
        [source_url], "non_recipe", creator_name="厨房 UP 主", batch_id="batch-1"
    ) == 1
    assert store.known_non_recipe_urls([source_url, "https://example.com/new"]) == {source_url}
    excluded = store.index_recipes()
    assert excluded["indexed"] == 0
    assert store.list_indexed_recipes() == []
    assert store.list_video_sources("non_recipe")[0]["batch_id"] == "batch-1"

    store.set_video_classifications([source_url], "recipe")
    restored = store.index_recipes()
    assert restored["indexed"] == 1
    assert len(store.list_indexed_recipes()) == 1


def test_technique_sources_are_retained_but_excluded_from_menu(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "火候技巧")
    recipe_data = json.loads((folder / "recipe.json").read_text(encoding="utf-8"))
    source_url = str(recipe_data["source_url"])
    store = MobileSyncStore(tmp_path)

    assert store.index_recipes()["indexed"] == 1
    assert store.set_video_classifications([source_url], "technique") == 1
    assert store.known_non_recipe_urls([source_url]) == {source_url}
    assert store.index_recipes()["indexed"] == 0
    technique_sources = store.list_video_sources("technique")
    assert len(technique_sources) == 1
    assert technique_sources[0]["source_url"] == source_url

    store.set_video_classifications([source_url], "recipe")
    assert store.index_recipes()["indexed"] == 1


def _ai_review(folder: Path, *, classification: str = "non_recipe", visibility: str = "hidden") -> None:
    (folder / "transcript.json").write_text(json.dumps([{"text": "测试引用，仅用于合成测试"}]), encoding="utf-8")
    (folder / AI_REVIEW_FILE_NAME).write_text(json.dumps({
        "schema_version": 2,
        "status": "completed",
        "source_recipe_sha256": review_recipe_hash(folder),
        "input_fingerprint": review_input_fingerprint(folder),
        "rules_version": AI_REVIEW_RULES_VERSION,
        "prompt_version": AI_REVIEW_PROMPT_VERSION,
        "classification": classification,
        "visibility": visibility,
        "confidence": 0.99,
        "tags": ["审核标签"],
        "reason": "测试审核结果",
        "evidence": [{"text": "测试引用，仅用于合成测试"}],
        "item_checks": [],
    }), encoding="utf-8")


def test_ai_hidden_recipe_syncs_removal_and_manual_restore_without_touching_sources(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "审核广告")
    originals = {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = store.list_recipes()[0]["id"]
    device_id, _ = _paired(store)
    cursor = store.current_revision()

    _ai_review(folder)
    result = store.index_recipes()
    assert result["indexed"] == 0
    assert result["deleted"] == 1
    assert store.list_recipes() == []
    removed = store.sync(device_id, cursor, [])["changes"]
    assert [(item["entity_id"], item["action"]) for item in removed] == [(recipe_id, "delete")]
    assert store.list_indexed_recipes(include_deleted=True)[0]["id"] == recipe_id
    assert store.list_video_sources()[0]["classification"] == "recipe"

    cursor = store.current_revision()
    set_ai_review_visibility(folder, "visible")
    assert store.index_recipes()["indexed"] == 1
    restored = store.sync(device_id, cursor, [])["changes"]
    assert [(item["entity_id"], item["action"]) for item in restored] == [(recipe_id, "upsert")]
    assert restored[0]["payload"]["tags"] == ["审核标签"]
    for name, content in originals.items():
        assert (folder / name).read_bytes() == content


def test_ai_review_tags_are_published_without_overwriting_original_recipe(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "审核菜谱")
    path = folder / "recipe.json"
    recipe = json.loads(path.read_text(encoding="utf-8"))
    recipe["tags"] = ["家常菜", "审核标签"]
    path.write_text(json.dumps(recipe), encoding="utf-8")
    before = path.read_bytes()
    _ai_review(folder, classification="recipe", visibility="visible")
    store = MobileSyncStore(tmp_path)

    store.index_recipes()
    assert store.list_recipes()[0]["tags"] == ["家常菜", "审核标签"]
    assert path.read_bytes() == before


def test_stale_ai_review_does_not_keep_updated_recipe_hidden(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "待重新审核")
    _ai_review(folder)
    store = MobileSyncStore(tmp_path)
    assert store.index_recipes()["indexed"] == 0

    recipe_path = folder / "recipe.json"
    changed = json.loads(recipe_path.read_text(encoding="utf-8"))
    changed["steps"][0]["action"] = "补充了新的制作步骤"
    recipe_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    assert store.index_recipes()["indexed"] == 1
    assert "审核标签" not in store.list_recipes()[0].get("tags", [])


def test_hidden_latest_duplicate_does_not_republish_an_older_copy(tmp_path: Path) -> None:
    old = _recipe(tmp_path, "旧副本")
    latest = _recipe(tmp_path, "新副本")
    os.utime(old / "recipe.json", (100, 100))
    os.utime(latest / "recipe.json", (200, 200))
    _ai_review(latest)
    store = MobileSyncStore(tmp_path)

    result = store.index_recipes()
    assert result["indexed"] == 0
    assert len(result["duplicates"]) == 1
    set_ai_review_visibility(latest, "visible")
    store.index_recipes()
    assert store.list_recipes()[0]["title"] == "新副本"


def test_manual_restore_overrides_legacy_exclusion_but_keeps_menu_unpublished(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "旧来源排除")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = store.list_recipes()[0]["id"]
    source_url = store.list_recipes()[0]["source_url"]
    store.set_recipe_publications({recipe_id: False})
    store.set_video_classifications([source_url], "technique")
    assert store.index_recipes()["indexed"] == 0

    set_ai_review_visibility(folder, "visible")
    assert store.index_recipes()["indexed"] == 1
    state = store.recipe_visibility_states()[str(folder)]
    assert state["content_visible"] is True
    assert state["visible"] is False
    assert state["reason_code"] == "unpublished"
    assert state["source_classification"] == "technique"
    assert store.list_video_sources()[0]["classification"] == "technique"
    assert store.list_recipes()[0]["published"] is False

    store.set_recipe_publications({recipe_id: True})
    assert store.recipe_visibility_states()[str(folder)]["visible"] is True
    set_ai_review_visibility(folder, None)
    assert store.index_recipes()["indexed"] == 0


def test_manual_hide_survives_stale_ai_review_and_recipe_read_failure(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "人工隐藏")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    _ai_review(folder, classification="recipe", visibility="visible")
    set_ai_review_visibility(folder, "hidden")
    path = folder / "recipe.json"
    updated = json.loads(path.read_text(encoding="utf-8"))
    updated["title"] = "内容已更新"
    path.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
    assert store.index_recipes()["indexed"] == 0

    set_ai_review_visibility(folder, "visible")
    assert store.index_recipes()["indexed"] == 1
    set_ai_review_visibility(folder, "hidden")
    path.write_text("{", encoding="utf-8")
    result = store.index_recipes()
    assert result["errors"]
    assert store.list_recipes() == []


def test_visibility_reader_does_not_initialize_database_or_modify_reports(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "只读状态")
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    state = recipe_visibility_states(tmp_path / "data" / "recipes", project_root=tmp_path)[str(folder)]
    assert state["visible"] is True
    assert state["published"] is True
    assert state["recipe_id"] is None
    assert {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    assert not (tmp_path / "data" / "state").exists()


def test_visibility_reader_reports_corrupt_database_without_assuming_visible(tmp_path: Path) -> None:
    _recipe(tmp_path, "数据库不可读")
    database = tmp_path / "data" / "state" / "mobile-sync.sqlite3"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"not-a-sqlite-database")
    with pytest.raises(VisibilityStateError, match="无法读取来源分类"):
        recipe_visibility_states(tmp_path / "data" / "recipes", project_root=tmp_path)
    assert database.read_bytes() == b"not-a-sqlite-database"


def test_incremental_index_reuses_unchanged_candidates_and_detects_changed_image(tmp_path: Path, monkeypatch) -> None:
    first = _recipe(tmp_path, "缓存一", cid="1")
    _recipe(tmp_path, "缓存二", cid="2")
    store = MobileSyncStore(tmp_path)
    assert store.index_recipes()["rebuilt"] == 2
    monkeypatch.setattr("bili_recipe_notes.mobile_sync.fingerprint_is_stable", lambda _: True)
    calls = []
    build_assets = store._assets_for_recipe

    def assets(folder, payload, recipe_id):
        calls.append(folder)
        return build_assets(folder, payload, recipe_id)

    monkeypatch.setattr(store, "_assets_for_recipe", assets)
    stable = store.index_recipes()
    assert stable["cache_hits"] == 2
    assert stable["rebuilt"] == stable["changed"] == 0
    assert calls == []

    replacement = first / "images" / "new.jpg"
    replacement.write_bytes(b"new-jpeg!")
    replacement.replace(first / "images" / "step.jpg")
    updated = store.index_recipes()
    assert updated["cache_hits"] == 1
    assert updated["rebuilt"] == updated["changed"] == 1
    assert calls == [first]
    recipe = next(item for item in store.list_recipes() if item["title"] == "缓存一")
    assert recipe["steps"][0]["image_sha256"] == hashlib.sha256(b"new-jpeg!").hexdigest()


def test_incremental_index_detects_image_deletion_and_recreation(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "图片变动")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    image_path = folder / "images" / "step.jpg"
    image_path.unlink()
    assert store.index_recipes()["changed"] == 1
    assert store.list_recipes()[0]["assets"] == []
    assert "image_sha256" not in store.list_recipes()[0]["steps"][0]
    image_path.write_bytes(b"restored-image")
    assert store.index_recipes()["changed"] == 1
    assert store.list_recipes()[0]["assets"][0]["sha256"] == hashlib.sha256(b"restored-image").hexdigest()


def test_dish_info_incrementally_syncs_exports_and_expires_without_rewriting_recipe(tmp_path: Path, monkeypatch) -> None:
    from bili_recipe_notes import dish_info
    from bili_recipe_notes.web_export import build_web_library_payload

    folder = _recipe(tmp_path, "介绍菜谱")
    original = (folder / "recipe.json").read_bytes()
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    device_id, _ = _paired(store)
    cursor = store.current_revision()
    monkeypatch.setattr("bili_recipe_notes.mobile_sync.fingerprint_is_stable", lambda _: True)
    answer = {"summary": "鸡蛋与番茄的家常菜。", "method": "按菜谱将食材翻炒。", "background": None,
              "origin": None, "source_matches": []}
    monkeypatch.setattr(dish_info, "complete_markdown_prompt", lambda *_args, **_kwargs: json.dumps(answer))
    dish_info.generate_dish_info(folder)
    result = store.index_recipes()
    assert result["changed"] == result["rebuilt"] == 1
    indexed = store.list_recipes()[0]["dish_info"]
    assert indexed["summary"] == answer["summary"]
    assert indexed["search_status"] == "not_requested"
    assert "model_config" not in indexed and "input_fingerprint" not in indexed
    assert store.sync(device_id, cursor, [])["changes"][0]["payload"]["dish_info"] == indexed
    assert build_web_library_payload(store, image_mode="none")["recipes"][0]["dish_info"] == indexed
    assert (folder / "recipe.json").read_bytes() == original
    assert store.index_recipes()["cache_hits"] == 1

    answer["summary"] = "更新的菜品介绍。"
    dish_info.generate_dish_info(folder, only_pending=False)
    assert store.index_recipes()["changed"] == 1
    assert store.list_recipes()[0]["dish_info"]["summary"] == answer["summary"]
    changed = json.loads(original)
    changed["steps"][0]["action"] = "修改了菜谱内容"
    (folder / "recipe.json").write_text(json.dumps(changed), encoding="utf-8")
    assert store.index_recipes()["changed"] == 1
    assert "dish_info" not in store.list_recipes()[0]
    assert dish_info.load_dish_info(folder) is None

    dish_info.generate_dish_info(folder)
    assert store.index_recipes()["changed"] == 1
    (folder / dish_info.DISH_INFO_FILE_NAME).unlink()
    assert store.index_recipes()["changed"] == 1
    assert "dish_info" not in store.list_recipes()[0]


@pytest.mark.parametrize("file_name", ["job.json", "source.json", "transcript.json", "note.md"])
def test_incremental_index_checks_all_review_and_identity_inputs(tmp_path: Path, file_name: str, monkeypatch) -> None:
    folder = _recipe(tmp_path, "审核输入缓存")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    path = folder / file_name
    if file_name == "job.json":
        value = json.loads(path.read_text(encoding="utf-8"))
        value["title"] = "更新来源标题"
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        path.write_text("[]" if file_name == "transcript.json" else "{}", encoding="utf-8")
    assert store.index_recipes()["rebuilt"] == 1
    monkeypatch.setattr("bili_recipe_notes.mobile_sync.fingerprint_is_stable", lambda _: True)
    assert store.index_recipes()["cache_hits"] == 1


def test_unreadable_recipe_retains_last_good_payload_and_recovers(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "短暂损坏")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    before = store.list_recipes()
    path = folder / "recipe.json"
    original = path.read_bytes()
    path.write_text("{", encoding="utf-8")
    result = store.index_recipes()
    assert len(result["errors"]) == 1
    assert result["deleted"] == 0
    assert store.list_recipes() == before
    path.write_bytes(original)
    assert store.index_recipes()["errors"] == []


def test_unreadable_latest_duplicate_does_not_fall_back_to_older_content(tmp_path: Path) -> None:
    old = _recipe(tmp_path, "旧副本")
    latest = _recipe(tmp_path, "最新可用副本")
    os.utime(old / "recipe.json", (100, 100))
    os.utime(latest / "recipe.json", (200, 200))
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    path = latest / "recipe.json"
    original = path.read_bytes()
    path.write_text("{", encoding="utf-8")
    result = store.index_recipes()
    assert result["indexed"] == 1
    assert result["changed"] == result["deleted"] == 0
    assert result["errors"]
    assert store.list_recipes()[0]["title"] == "最新可用副本"
    path.write_bytes(original)
    assert store.index_recipes()["errors"] == []


def test_legacy_classification_changes_apply_even_when_candidate_is_cached(tmp_path: Path, monkeypatch) -> None:
    _recipe(tmp_path, "分类变更")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    source = store.list_recipes()[0]["source_url"]
    monkeypatch.setattr("bili_recipe_notes.mobile_sync.fingerprint_is_stable", lambda _: True)
    store.set_video_classifications([source], "technique")
    hidden = store.index_recipes()
    assert hidden["cache_hits"] == 1
    assert hidden["indexed"] == 0
    store.set_video_classifications([source], "recipe")
    restored = store.index_recipes()
    assert restored["cache_hits"] == 1
    assert restored["indexed"] == 1


def test_missing_library_root_does_not_delete_existing_library(tmp_path: Path) -> None:
    _recipe(tmp_path, "离线目录")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    before = store.list_recipes()
    (tmp_path / "data" / "recipes").rename(tmp_path / "temporarily-offline")
    with pytest.raises(OSError, match="暂不可用"):
        store.index_recipes()
    assert store.list_recipes() == before


def test_folder_rename_updates_index_paths_even_when_payload_is_unchanged(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "原目录")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    renamed = folder.with_name("新目录")
    folder.rename(renamed)
    store.index_recipes()
    assert store.list_recipe_cover_reviews()[0]["output_folder"] == str(renamed)
    digest = store.list_recipes()[0]["assets"][0]["sha256"]
    assert store.asset_path(digest)[0] == renamed / "images" / "step.jpg"


def test_recipe_cover_is_published_as_the_preferred_asset(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "成品图菜谱")
    cover = folder / "images" / "cover.jpg"
    cover.write_bytes(b"finished-dish-cover")
    recipe_path = folder / "recipe.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    recipe["cover_image_path"] = "images/cover.jpg"
    recipe["cover_image_status"] = "manual_video"
    recipe["cover_image_time"] = 73.5
    recipe["cover_source_kind"] = "video_frame"
    recipe["cover_source_label"] = "原视频候选帧"
    recipe["cover_source_url"] = "https://www.bilibili.com/video/BV1demo"
    recipe["cover_original_size"] = {"width": 1920, "height": 1080}
    recipe["cover_crop_box"] = {"left": 240, "top": 0, "width": 1440, "height": 1080}
    recipe["cover_selected_at"] = "2026-08-20T10:00:00+00:00"
    recipe_path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    store = MobileSyncStore(tmp_path)

    store.index_recipes()

    published = store.list_recipes()[0]
    expected = hashlib.sha256(cover.read_bytes()).hexdigest()
    assert published["cover_image_sha256"] == expected
    assert published["assets"][0]["kind"] == "recipe_cover"
    assert published["assets"][0]["sha256"] == expected
    review = store.list_recipe_cover_reviews()[0]
    assert review["title"] == "成品图菜谱"
    assert review["cover_image_path"] == "images/cover.jpg"
    assert review["output_folder"] == str(folder)
    assert review["cover_image_time"] == 73.5
    assert review["cover_source_kind"] == "video_frame"
    assert review["cover_source_url"] == "https://www.bilibili.com/video/BV1demo"
    assert review["cover_original_size"] == {"width": 1920, "height": 1080}
    assert review["cover_crop_box"] == {"left": 240, "top": 0, "width": 1440, "height": 1080}
    assert review["cover_selected_at"] == "2026-08-20T10:00:00+00:00"


def test_automatic_cover_is_not_published_as_menu_thumbnail(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "自动封面菜谱")
    cover = folder / "images" / "cover.jpg"
    cover.write_bytes(b"automatic-cover")
    recipe_path = folder / "recipe.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    recipe["cover_image_path"] = "images/cover.jpg"
    recipe["cover_image_status"] = "auto_finished_dish"
    recipe_path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    store = MobileSyncStore(tmp_path)

    store.index_recipes()

    published = store.list_recipes()[0]
    assert "cover_image_sha256" not in published
    assert not any(asset["kind"] == "recipe_cover" for asset in published["assets"])
    assert any(asset["kind"] == "recipe_image" for asset in published["assets"])


def test_recipe_identity_uses_part_number_and_persists_random_fallback(tmp_path: Path) -> None:
    part = _recipe(tmp_path, "无 CID 分集", cid="")
    (part / "job.json").write_text(
        json.dumps({"bvid": "BV1demo", "part_id": "2", "part_label": "p"}),
        encoding="utf-8",
    )
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    metadata = json.loads((part / "sync-meta.json").read_text(encoding="utf-8"))
    assert metadata["source_identity"] == "bilibili:bv1demo:p:2"

    anonymous = tmp_path / "data" / "recipes" / "anonymous"
    anonymous.mkdir()
    (anonymous / "recipe.json").write_text(
        json.dumps({"title": "无来源", "steps": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    store.index_recipes()
    first_id = json.loads((anonymous / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    (anonymous / "recipe.json").touch()
    store.index_recipes()
    second_id = json.loads((anonymous / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    assert first_id == second_id


def test_pairing_is_single_use_and_revocation_is_immediate(tmp_path: Path) -> None:
    store = MobileSyncStore(tmp_path)
    credential = store.issue_pairing_credential("http://192.168.1.2:8765")
    paired = store.pair_device(credential.pairing_token, "iPhone")
    assert store.authenticate(paired["access_token"])["name"] == "iPhone"
    with pytest.raises(AuthenticationError):
        store.pair_device(credential.pairing_token, "重复")
    store.revoke_device(paired["device_id"])
    with pytest.raises(AuthenticationError):
        store.authenticate(paired["access_token"])


def test_self_join_defaults_open_and_persists_lock(tmp_path: Path) -> None:
    store = MobileSyncStore(tmp_path)
    joined = store.join_device("厨房平板")
    assert store.authenticate(joined["access_token"])["name"] == "厨房平板"

    store.set_self_join_enabled(False)
    restarted = MobileSyncStore(tmp_path)

    assert restarted.self_join_enabled() is False
    with pytest.raises(AuthenticationError):
        restarted.join_device("新手机")
    assert restarted.authenticate(joined["access_token"])["id"] == joined["device_id"]


def test_expired_pairing_token_is_rejected(tmp_path: Path) -> None:
    store = MobileSyncStore(tmp_path)
    credential = store.issue_pairing_credential("http://192.168.1.2:8765")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE pairing_tokens SET expires_at=? WHERE token_hash=?",
            ("2000-01-01T00:00:00+00:00", token_hash(credential.pairing_token)),
        )

    with pytest.raises(AuthenticationError):
        store.pair_device(credential.pairing_token, "过期设备")


def test_offline_operations_are_idempotent_and_conflicts_preserve_both_versions(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "番茄炒蛋")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    first_device, _ = _paired(store)
    second_device, _ = _paired(store)
    log_id = str(uuid.uuid4())
    create = _upsert(log_id, recipe_id)

    first = store.sync(first_device, 0, [create])
    repeated = store.sync(first_device, 0, [create])
    assert first["operation_results"] == repeated["operation_results"]
    assert len(store.list_practice_logs(recipe_id)) == 1

    update = _upsert(log_id, recipe_id, base_version=1, notes="设备一修改")
    assert store.sync(first_device, 0, [update])["operation_results"][0]["status"] == "accepted"
    stale = _upsert(log_id, recipe_id, base_version=1, notes="设备二离线修改")
    result = store.sync(second_device, 0, [stale])["operation_results"][0]
    assert result["status"] == "conflict"
    assert store.list_practice_logs(recipe_id)[0]["notes"] == "设备一修改"
    assert store.list_conflicts()[0]["incoming"]["notes"] == "设备二离线修改"

    resolution = _upsert(log_id, recipe_id, base_version=2, notes="设备二离线修改")
    resolution["payload"]["_resolved_conflict_id"] = result["conflict_id"]
    resolution["payload"]["_conflict_resolution"] = "incoming"
    assert store.sync(second_device, 0, [resolution])["operation_results"][0]["status"] == "accepted"
    assert store.list_practice_logs(recipe_id)[0]["notes"] == "设备二离线修改"
    assert store.list_conflicts() == []


def test_photo_hash_size_and_reference_are_validated(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "照片测试")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    device_id, _ = _paired(store)
    photo = b"practice-photo"
    digest = hashlib.sha256(photo).hexdigest()
    assert store.store_asset(digest, photo, "image/jpeg")["sha256"] == digest
    assert store.asset_path(digest) is not None
    with pytest.raises(ValidationError):
        store.store_asset("0" * 64, photo, "image/jpeg")

    operation = _upsert(str(uuid.uuid4()), recipe_id)
    operation["payload"]["photo_sha256"] = "f" * 64
    with pytest.raises(ValidationError):
        store.sync(device_id, 0, [operation])


def test_restart_preserves_device_token_revision_and_open_conflict(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "重启持久化")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    first_device, first_token = _paired(store)
    second_device, _ = _paired(store)
    log_id = str(uuid.uuid4())
    store.sync(first_device, 0, [_upsert(log_id, recipe_id)])
    store.sync(first_device, 0, [_upsert(log_id, recipe_id, base_version=1, notes="服务器版本")])
    conflict = store.sync(
        second_device,
        0,
        [_upsert(log_id, recipe_id, base_version=1, notes="离线版本")],
    )["operation_results"][0]
    revision = store.current_revision()

    restarted = MobileSyncStore(tmp_path)

    assert restarted.authenticate(first_token)["id"] == first_device
    assert restarted.current_revision() == revision
    assert restarted.list_conflicts()[0]["id"] == conflict["conflict_id"]
    assert restarted.sync(first_device, revision, [])["changes"] == []


def test_change_cursor_is_paginated_without_gaps(tmp_path: Path) -> None:
    for index in range(205):
        _recipe(tmp_path, f"分页菜谱-{index}", cid=str(index + 1))
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    device_id, _ = _paired(store)

    first = store.sync(device_id, 0, [])
    second = store.sync(device_id, first["next_cursor"], [])

    assert len(first["changes"]) == 200
    assert first["has_more"] is True
    assert len(second["changes"]) == 5
    assert second["has_more"] is False
    assert second["next_cursor"] == store.current_revision()


def test_authentication_and_sync_read_committed_menu_while_another_process_writes(tmp_path: Path, monkeypatch) -> None:
    _recipe(tmp_path, "正常菜单")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    device = store.join_device("测试设备")
    capabilities = ["recipe", "meal_order", "meal_selection", "meal_dish_state"]
    original = store.sync(device["device_id"], 0, [], capabilities)
    with store._write_connection() as connection:
        connection.execute("UPDATE devices SET last_seen_at='2000-01-01T00:00:00+00:00'")
    connect = store._connect

    def immediate_connect():
        connection = connect()
        connection.execute("PRAGMA busy_timeout=0")
        return connection

    monkeypatch.setattr(store, "_connect", immediate_connect)
    with connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE recipes SET payload_json='{\"id\":\"dish\",\"title\":\"未提交\"}'")
        assert store.authenticate(device["access_token"])["id"] == device["device_id"]
        snapshot = store.sync(device["device_id"], 0, [], capabilities)
        assert snapshot["changes"] == original["changes"]
        assert snapshot["next_cursor"] == original["next_cursor"]
        assert snapshot["meal"] == original["meal"]
        writer.rollback()
    # A busy timestamp update is retried, while subsequent requests are reads.
    store.authenticate(device["access_token"])
    last_seen = store.list_devices()[0]["last_seen_at"]
    assert last_seen > "2000"
    store.authenticate(device["access_token"])
    assert store.list_devices()[0]["last_seen_at"] == last_seen
    store.revoke_device(device["device_id"])
    with pytest.raises(AuthenticationError):
        store.authenticate(device["access_token"])


def test_capable_client_bootstraps_current_snapshot_instead_of_recipe_history(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "快照菜谱")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_path = folder / "recipe.json"
    for version in range(12):
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
        recipe["title"] = f"快照菜谱-{version}"
        recipe_path.write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
        store.index_recipes()
    device_id, _ = _paired(store)

    bootstrapped = store.sync(device_id, 0, [], ["recipe", "practice_log", "meal_plan"])
    legacy = store.sync(device_id, 0, [])

    recipes = [change for change in bootstrapped["changes"] if change["entity_type"] == "recipe"]
    assert bootstrapped["bootstrap"] is True
    assert bootstrapped["has_more"] is False
    assert bootstrapped["next_cursor"] == store.current_revision()
    assert len(recipes) == 1
    assert recipes[0]["payload"]["title"] == "快照菜谱-11"
    assert len([change for change in legacy["changes"] if change["entity_type"] == "recipe"]) == 13
    assert "bootstrap" not in legacy


def test_recipe_publication_persists_and_syncs_without_losing_details(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "上下架菜谱")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    device_id, _ = _paired(store)
    capabilities = ["recipe", "meal_order", "meal_selection", "meal_dish_state"]
    bootstrap = store.sync(device_id, 0, [], capabilities)
    meal = bootstrap["meal"]
    cursor = bootstrap["next_cursor"]

    assert bootstrap["changes"][0]["payload"]["published"] is True
    assert bootstrap["changes"][0]["payload"]["recommended"] is False
    assert store.set_recipe_publications({recipe_id: False}) == 1
    hidden = store.sync(device_id, cursor, [], capabilities)

    recipe_change = next(change for change in hidden["changes"] if change["entity_id"] == recipe_id)
    assert recipe_change["action"] == "upsert"
    assert recipe_change["payload"]["title"] == "上下架菜谱"
    assert recipe_change["payload"]["published"] is False
    assert store.list_indexed_recipes()[0]["published"] is False

    recipe = json.loads((folder / "recipe.json").read_text(encoding="utf-8"))
    recipe["summary_tips"] = ["重新索引"]
    (folder / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    store.index_recipes()
    restarted = MobileSyncStore(tmp_path)
    assert restarted.list_indexed_recipes()[0]["published"] is False

    rejected = restarted.sync(
        device_id,
        restarted.current_revision(),
        [_meal_op(meal, "add_quantity", recipe_id, quantity=1)],
        capabilities,
    )
    assert rejected["operation_results"][0]["reason"] == "recipe_unpublished"

    assert restarted.set_recipe_publications({recipe_id: True}) == 1
    accepted = restarted.sync(
        device_id,
        restarted.current_revision(),
        [_meal_op(restarted.current_meal_order(), "add_quantity", recipe_id, quantity=1)],
        capabilities,
    )
    assert accepted["operation_results"][0]["status"] == "accepted"


def test_database_v2_migration_defaults_existing_recipes_to_published(tmp_path: Path) -> None:
    database = tmp_path / "data" / "state" / "mobile-sync.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE recipes (id TEXT PRIMARY KEY, output_folder TEXT NOT NULL, payload_json TEXT NOT NULL, "
            "content_hash TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, revision INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO recipes VALUES (?,?,?,?,?,NULL,?)",
            (str(uuid.uuid4()), "folder", json.dumps({"title": "旧菜谱"}), "hash", "2026-08-20T00:00:00Z", 1),
        )
        connection.execute("PRAGMA user_version = 2")

    migrated = MobileSyncStore(tmp_path, database_path=database)

    assert migrated.list_indexed_recipes()[0]["published"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        columns = {row[1] for row in connection.execute("PRAGMA table_info(recipes)")}
        assert {"published", "recommended"} <= columns
        assert connection.execute("SELECT recommended FROM recipes").fetchone()[0] == 0
    assert list(database.parent.glob("mobile-sync.before-v2-to-v7-*.bak"))


def test_recipe_recommendation_auto_publishes_and_unpublish_clears_it(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "主厨推荐菜", cid="151")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    device_id, _ = _paired(store)
    capabilities = ["recipe", "meal_order", "meal_selection", "meal_dish_state"]
    cursor = store.sync(device_id, 0, [], capabilities)["next_cursor"]
    store.set_recipe_publications({recipe_id: False})

    assert store.set_recipe_recommendations({recipe_id: True}) == 1
    recommended = store.sync(device_id, cursor, [], capabilities)
    recommended_change = [change for change in recommended["changes"] if change["entity_id"] == recipe_id][-1]
    assert recommended_change["payload"]["published"] is True
    assert recommended_change["payload"]["recommended"] is True
    assert store.list_indexed_recipes()[0]["recommended"] is True

    assert store.set_recipe_publications({recipe_id: False}) == 1
    state = store.list_indexed_recipes()[0]
    assert state["published"] is False
    assert state["recommended"] is False


def test_private_network_filter() -> None:
    assert is_private_client("127.0.0.1")


def _meal_op(snapshot: dict, action: str, recipe_id: str | None = None, **values) -> dict:
    return {
        "op_id": str(uuid.uuid4()),
        "entity_type": "meal_selection",
        "action": action,
        "order_id": snapshot["order"]["id"],
        "epoch": snapshot["order"]["epoch"],
        "phase": snapshot["order"].get("phase", "ordering"),
        **({"recipe_id": recipe_id} if recipe_id else {}),
        **values,
    }


def test_shared_meal_preserves_people_aggregates_and_idempotency(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "多人番茄炒蛋")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    first_device, _ = _paired(store)
    second_device, _ = _paired(store)
    capabilities = ["recipe", "meal_order", "meal_selection", "meal_dish_state"]
    meal = store.sync(first_device, 0, [], capabilities)["meal"]
    first_add = _meal_op(meal, "add_quantity", recipe_id, quantity=1)

    store.sync(first_device, 0, [first_add, first_add], capabilities)
    store.sync(second_device, 0, [_meal_op(meal, "add_quantity", recipe_id, quantity=1.5)], capabilities)
    store.sync(second_device, 0, [_meal_op(meal, "set_note", recipe_id, note="少盐")], capabilities)
    snapshot = store.current_meal_order()

    assert sum(item["quantity"] for item in snapshot["selections"]) == 2.5
    assert {item["device_id"] for item in snapshot["selections"]} == {first_device, second_device}
    assert next(item for item in snapshot["selections"] if item["device_id"] == second_device)["note"] == "少盐"


def test_clear_and_complete_reject_stale_offline_operations(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "旧操作保护")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    device_id, _ = _paired(store)
    capabilities = ["recipe", "meal_order", "meal_selection", "meal_dish_state"]
    meal = store.sync(device_id, 0, [], capabilities)["meal"]
    stale = _meal_op(meal, "add_quantity", recipe_id, quantity=1)
    clear = _meal_op(meal, "clear_order")
    clear["entity_type"] = "meal_order"

    assert store.sync(device_id, 0, [clear], capabilities)["operation_results"][0]["status"] == "accepted"
    assert store.sync(device_id, 0, [stale], capabilities)["operation_results"][0]["reason"] == "stale_order"
    current = store.current_meal_order()
    store.sync(device_id, 0, [_meal_op(current, "add_quantity", recipe_id, quantity=1)], capabilities)
    ordering = store.current_meal_order()
    advance = _meal_op(ordering, "advance_meal_phase")
    advance["entity_type"] = "meal_order"
    assert store.sync(device_id, 0, [advance], capabilities)["operation_results"][0]["status"] == "accepted"
    stale_ordering_op = _meal_op(ordering, "add_quantity", recipe_id, quantity=1)
    assert store.sync(device_id, 0, [stale_ordering_op], capabilities)["operation_results"][0]["reason"] == "meal_phase_changed"

    for phase in ("prep", "cooking", "serving"):
        snapshot = store.current_meal_order()
        stage = _meal_op(snapshot, "set_dish_stage_completed", recipe_id, stage=phase, completed=True)
        stage["entity_type"] = "meal_dish_state"
        assert store.sync(device_id, 0, [stage], capabilities)["operation_results"][0]["status"] == "accepted"
        if phase != "serving":
            snapshot = store.current_meal_order()
            advance = _meal_op(snapshot, "advance_meal_phase")
            advance["entity_type"] = "meal_order"
            assert store.sync(device_id, 0, [advance], capabilities)["operation_results"][0]["status"] == "accepted"

    current = store.current_meal_order()
    complete = _meal_op(current, "complete_order")
    complete["entity_type"] = "meal_order"
    completed_sync = store.sync(device_id, 0, [complete], capabilities)
    assert completed_sync["operation_results"][0]["status"] == "accepted"
    assert completed_sync["meal"]["order"]["id"] != current["order"]["id"]
    assert completed_sync["meal"]["order"]["phase"] == "ordering"
    assert completed_sync["meal"]["selections"] == []
    result = store.sync(device_id, 0, [_meal_op(current, "add_quantity", recipe_id, quantity=1)], capabilities)
    assert result["operation_results"][0]["reason"] == "order_completed"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM meal_selections WHERE order_id=?", (current["order"]["id"],)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT served FROM meal_dish_states WHERE order_id=?", (current["order"]["id"],)
        ).fetchone()[0] == 1
    history = store.list_meal_history()
    assert history[0]["order"]["id"] == current["order"]["id"]
    assert len(history[0]["selections"]) == 1


def test_deleted_recipe_operation_does_not_block_valid_outbox_items(tmp_path: Path) -> None:
    deleted_folder = _recipe(tmp_path, "离线删除菜", cid="301")
    valid_folder = _recipe(tmp_path, "正常菜", cid="302")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    deleted_id = json.loads((deleted_folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    valid_id = json.loads((valid_folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    device_id, _ = _paired(store)
    capabilities = ["recipe", "meal_order", "meal_selection", "meal_dish_state"]
    meal = store.sync(device_id, 0, [], capabilities)["meal"]
    deleted_folder.rename(tmp_path / "deleted-recipe")
    store.index_recipes()

    result = store.sync(
        device_id,
        store.current_revision(),
        [
            _meal_op(meal, "add_quantity", deleted_id, quantity=1),
            _meal_op(meal, "add_quantity", valid_id, quantity=1),
        ],
        capabilities,
    )

    assert result["operation_results"][0]["reason"] == "recipe_unavailable"
    assert result["operation_results"][1]["status"] == "accepted"
    assert [item["recipe_id"] for item in store.current_meal_order()["selections"]] == [valid_id]
    valid_folder.rename(tmp_path / "deleted-after-selection")
    store.index_recipes()
    selected = store.current_meal_order()
    advance = _meal_op(selected, "advance_meal_phase")
    advance["entity_type"] = "meal_order"
    assert store.sync(device_id, 0, [advance], capabilities)["operation_results"][0]["status"] == "accepted"
    prep = store.current_meal_order()
    stage = _meal_op(prep, "set_dish_stage_completed", valid_id, stage="prep", completed=True)
    stage["entity_type"] = "meal_dish_state"
    assert store.sync(device_id, 0, [stage], capabilities)["operation_results"][0]["status"] == "accepted"


def test_loading_plan_skips_unpublished_and_missing_recipes(tmp_path: Path) -> None:
    published_folder = _recipe(tmp_path, "套餐上架菜", cid="311")
    hidden_folder = _recipe(tmp_path, "套餐下架菜", cid="312")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    published_id = json.loads((published_folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    hidden_id = json.loads((hidden_folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    device_id, _ = _paired(store)
    capabilities = ["recipe", "meal_plan", "meal_order", "meal_selection", "meal_dish_state"]
    meal = store.sync(device_id, 0, [], capabilities)["meal"]
    plan_id = str(uuid.uuid4())
    create_plan = {
        "op_id": str(uuid.uuid4()), "entity_type": "meal_plan", "action": "upsert", "base_version": 0,
        "payload": {
            "id": plan_id, "name": "过滤套餐", "items": [
                {"recipe_id": published_id, "servings_multiplier": 1},
                {"recipe_id": hidden_id, "servings_multiplier": 1},
                {"recipe_id": str(uuid.uuid4()), "servings_multiplier": 1},
            ],
        },
    }
    store.sync(device_id, 0, [create_plan], capabilities)
    store.set_recipe_publications({hidden_id: False})
    load = _meal_op(meal, "load_plan", plan_id=plan_id)
    load["entity_type"] = "meal_order"

    result = store.sync(device_id, 0, [load], capabilities)["operation_results"][0]

    assert result["status"] == "accepted"
    assert result["loaded"] == 1
    assert result["skipped"] == 2
    assert [item["recipe_id"] for item in store.current_meal_order()["selections"]] == [published_id]


def test_meal_phase_requires_every_dish_before_advancing(tmp_path: Path) -> None:
    first_folder = _recipe(tmp_path, "阶段菜一", cid="201")
    second_folder = _recipe(tmp_path, "阶段菜二", cid="202")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_ids = [
        json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
        for folder in (first_folder, second_folder)
    ]
    device_id, _ = _paired(store)
    capabilities = ["recipe", "meal_order", "meal_selection", "meal_dish_state"]
    meal = store.sync(device_id, 0, [], capabilities)["meal"]
    store.sync(device_id, 0, [_meal_op(meal, "add_quantity", recipe_id, quantity=1) for recipe_id in recipe_ids], capabilities)
    meal = store.current_meal_order()
    advance = _meal_op(meal, "advance_meal_phase")
    advance["entity_type"] = "meal_order"
    store.sync(device_id, 0, [advance], capabilities)

    prep = store.current_meal_order()
    first_done = _meal_op(prep, "set_dish_stage_completed", recipe_ids[0], stage="prep", completed=True)
    first_done["entity_type"] = "meal_dish_state"
    store.sync(device_id, 0, [first_done], capabilities)
    too_early = _meal_op(store.current_meal_order(), "advance_meal_phase")
    too_early["entity_type"] = "meal_order"
    result = store.sync(device_id, 0, [too_early], capabilities)["operation_results"][0]

    assert result["reason"] == "phase_incomplete"
    snapshot = store.current_meal_order()
    assert snapshot["order"]["phase"] == "prep"
    assert sum(state["prep_completed"] for state in snapshot["dish_states"]) == 1


def test_device_starting_prep_becomes_only_meal_chef(tmp_path: Path) -> None:
    folder = _recipe(tmp_path, "主厨权限菜", cid="401")
    store = MobileSyncStore(tmp_path)
    store.index_recipes()
    recipe_id = json.loads((folder / "sync-meta.json").read_text(encoding="utf-8"))["recipe_id"]
    chef_device, _ = _paired(store)
    guest_device, _ = _paired(store)
    capabilities = ["recipe", "meal_order", "meal_selection", "meal_dish_state"]
    meal = store.sync(chef_device, 0, [], capabilities)["meal"]
    store.sync(guest_device, 0, [_meal_op(meal, "add_quantity", recipe_id, quantity=1)], capabilities)
    ordering = store.current_meal_order()
    start = _meal_op(ordering, "advance_meal_phase")
    start["entity_type"] = "meal_order"

    started = store.sync(chef_device, 0, [start], capabilities)

    assert started["operation_results"][0]["status"] == "accepted"
    assert started["meal"]["order"]["chef_device_id"] == chef_device
    prep = store.current_meal_order()
    guest_stage = _meal_op(prep, "set_dish_stage_completed", recipe_id, stage="prep", completed=True)
    guest_stage["entity_type"] = "meal_dish_state"
    assert store.sync(guest_device, 0, [guest_stage], capabilities)["operation_results"][0]["reason"] == "chef_only"
    chef_stage = _meal_op(prep, "set_dish_stage_completed", recipe_id, stage="prep", completed=True)
    chef_stage["entity_type"] = "meal_dish_state"
    assert store.sync(chef_device, 0, [chef_stage], capabilities)["operation_results"][0]["status"] == "accepted"
    ready = store.current_meal_order()
    guest_advance = _meal_op(ready, "advance_meal_phase")
    guest_advance["entity_type"] = "meal_order"
    assert store.sync(guest_device, 0, [guest_advance], capabilities)["operation_results"][0]["reason"] == "chef_only"
    guest_add = _meal_op(ready, "add_quantity", recipe_id, quantity=1)
    assert store.sync(guest_device, 0, [guest_add], capabilities)["operation_results"][0]["reason"] == "meal_phase_locked"


def test_database_v3_migration_adds_meal_stages(tmp_path: Path) -> None:
    database = tmp_path / "data" / "state" / "mobile-sync.sqlite3"
    database.parent.mkdir(parents=True)
    order_id = str(uuid.uuid4())
    recipe_id = str(uuid.uuid4())
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE meal_orders (id TEXT PRIMARY KEY,status TEXT NOT NULL,version INTEGER NOT NULL,"
            "epoch INTEGER NOT NULL,created_at TEXT NOT NULL,completed_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE meal_dish_states (order_id TEXT NOT NULL,recipe_id TEXT NOT NULL,sort_order INTEGER NOT NULL,"
            "completed INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(order_id,recipe_id))"
        )
        connection.execute("INSERT INTO meal_orders VALUES (?, 'active', 1, 1, 'now', NULL)", (order_id,))
        connection.execute("INSERT INTO meal_dish_states VALUES (?, ?, 0, 1, 'now')", (order_id, recipe_id))
        connection.execute("PRAGMA user_version = 3")

    MobileSyncStore(tmp_path, database_path=database)

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        order = connection.execute("SELECT * FROM meal_orders").fetchone()
        state = connection.execute("SELECT * FROM meal_dish_states").fetchone()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert order["phase"] == "ordering"
        assert order["chef_device_id"] is None
        assert state["prep_completed"] == 0
        assert state["cook_completed"] == 1
        assert state["served"] == 0
    assert list(database.parent.glob("mobile-sync.before-v3-to-v7-*.bak"))


def test_legacy_capabilities_hide_shared_meal_changes(tmp_path: Path) -> None:
    store = MobileSyncStore(tmp_path)
    device_id, _ = _paired(store)
    store.sync(device_id, 0, [], ["meal_order", "meal_selection", "meal_dish_state"])

    legacy = store.sync(device_id, 0, [])

    assert legacy["capabilities"] == ["practice_log", "recipe"]
    assert all(change["entity_type"] in {"recipe", "practice_log"} for change in legacy["changes"])
    assert "meal" not in legacy


def test_legacy_meal_plans_migrate_once_with_backup(tmp_path: Path) -> None:
    config = tmp_path / "data" / "state"
    config.mkdir(parents=True)
    source = config / "meal-plans.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "plans": [{
            "id": "family-plan", "name": "家庭套餐", "guest_count": 3, "child_count": 0,
            "occasion": "日常家宴", "notes": "", "items": [{"recipe_id": "r1", "title": "菜一", "servings_multiplier": 1}],
            "created_at": "2026-08-19T00:00:00+00:00", "updated_at": "2026-08-19T00:00:00+00:00",
        }],
    }, ensure_ascii=False), encoding="utf-8")

    store = MobileSyncStore(tmp_path)
    restarted = MobileSyncStore(tmp_path)

    assert store.list_meal_plans()[0]["name"] == "家庭套餐"
    assert restarted.list_meal_plans()[0]["version"] == 1
    assert len(list(config.glob("meal-plans.migrated-*.json.bak"))) == 1
    assert is_private_client("192.168.1.10")
    assert is_private_client("testclient")
    assert not is_private_client("8.8.8.8")
