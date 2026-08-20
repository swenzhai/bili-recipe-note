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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "published" in {row[1] for row in connection.execute("PRAGMA table_info(recipes)")}
    assert list(database.parent.glob("mobile-sync.before-v2-to-v3-*.bak"))


def test_private_network_filter() -> None:
    assert is_private_client("127.0.0.1")


def _meal_op(snapshot: dict, action: str, recipe_id: str | None = None, **values) -> dict:
    return {
        "op_id": str(uuid.uuid4()),
        "entity_type": "meal_selection",
        "action": action,
        "order_id": snapshot["order"]["id"],
        "epoch": snapshot["order"]["epoch"],
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
    complete = _meal_op(current, "complete_order")
    complete["entity_type"] = "meal_order"
    store.sync(device_id, 0, [complete], capabilities)
    result = store.sync(device_id, 0, [_meal_op(current, "add_quantity", recipe_id, quantity=1)], capabilities)
    assert result["operation_results"][0]["reason"] == "order_completed"


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
