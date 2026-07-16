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


def test_private_network_filter() -> None:
    assert is_private_client("127.0.0.1")
    assert is_private_client("192.168.1.10")
    assert is_private_client("testclient")
    assert not is_private_client("8.8.8.8")
