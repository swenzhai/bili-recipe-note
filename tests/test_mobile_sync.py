from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from bili_recipe_notes.mobile_sync import (
    AuthenticationError,
    MobileSyncStore,
    ValidationError,
    is_private_client,
    token_hash,
)


def _recipe(root: Path, name: str, *, bvid: str = "BV1demo", cid: str = "101") -> Path:
    folder = root / "outputs" / name
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

    anonymous = tmp_path / "outputs" / "anonymous"
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
    database = tmp_path / ".bili-recipe-notes" / "mobile-sync.sqlite3"
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
    database = tmp_path / ".bili-recipe-notes" / "mobile-sync.sqlite3"
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
    config = tmp_path / ".bili-recipe-notes"
    config.mkdir()
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
