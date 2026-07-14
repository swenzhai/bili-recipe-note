from __future__ import annotations

import json
from pathlib import Path

import pytest

from bili_recipe_notes.knowledge_base import CookingKnowledgeEntry
from bili_recipe_notes.obsidian_archive import (
    ObsidianArchiveConflict,
    archive_knowledge_to_obsidian,
    archive_recipe_to_obsidian,
    archive_recipes_to_obsidian,
    recipe_archive_status,
)
from bili_recipe_notes.history import scan_history


def _recipe_output(root: Path, name: str = "output", *, bvid: str = "BV1demo123") -> Path:
    folder = root / name
    images = folder / "images"
    images.mkdir(parents=True)
    (images / "step 01.jpg").write_bytes(b"first-image")
    (images / "unused.jpg").write_bytes(b"must-not-be-archived")
    recipe = {
        "title": "../刀鱼:两吃?",
        "source_url": f"https://www.bilibili.com/video/{bvid}",
        "video_title": "刀鱼教学",
        "uploader": "老程",
        "category": "中餐/江浙",
        "cuisine": "淮扬菜",
        "tags": ["鱼类", "宴客"],
        "taste_rating": 5,
        "difficulty_rating": 4,
        "time_rating": 3,
        "ingredients": [],
        "seasonings": [],
        "tools": [],
        "steps": [],
        "summary_tips": [],
        "uncertain_points": [],
    }
    (folder / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    (folder / "job.json").write_text(json.dumps({"bvid": bvid}), encoding="utf-8")
    (folder / "note.md").write_text(
        "# 刀鱼两吃\n\n![去骨](images\\step 01.jpg)\n\n"
        "![远程图](https://example.com/picture.jpg)\n",
        encoding="utf-8",
    )
    return folder


def test_archive_recipe_builds_vault_note_data_images_and_state(tmp_path: Path) -> None:
    output = _recipe_output(tmp_path)
    vault = tmp_path / "My Vault"

    result = archive_recipe_to_obsidian(output, vault, tags=["已确认"])

    assert result.action == "created"
    assert result.source_id == "BV1demo123"
    assert result.note_path.is_file()
    assert result.recipe_data_path.read_bytes() == (output / "recipe.json").read_bytes()
    assert len(result.attachment_paths) == 1
    assert result.attachment_paths[0].read_bytes() == b"first-image"
    assert not any(path.name.startswith("unused") for path in (vault / "附件").rglob("*"))
    assert result.note_path.parent.name == "中餐-江浙"
    assert ".." not in result.note_path.name

    archived_note = result.note_path.read_text(encoding="utf-8")
    assert 'type: "recipe"' in archived_note
    assert 'status: "archived"' in archived_note
    assert 'category: "中餐-江浙"' in archived_note
    assert 'cuisine: "淮扬菜"' in archived_note
    assert 'tags: ["菜谱", "中餐-江浙", "淮扬菜", "鱼类", "宴客", "已确认"]' in archived_note
    assert 'rating: 5' in archived_note
    assert 'taste_rating: 5' in archived_note
    assert 'difficulty_rating: 4' in archived_note
    assert 'time_rating: 3' in archived_note
    assert "- 个人喜爱度：★★★★★（5/5）" in archived_note
    assert "![去骨](<../../附件/菜谱/BV1demo123/" in archived_note
    assert "https://example.com/picture.jpg" in archived_note
    assert "recipe_data: \"../../附件/菜谱/BV1demo123/recipe.json\"" in archived_note

    state = json.loads((output / "archive.json").read_text(encoding="utf-8"))
    assert state["status"] == "archived"
    assert state["action"] == "created"
    assert state["vault"] == str(vault.resolve())
    assert state["vault_root"] == str(vault.resolve())
    assert state["note_path"] == str(result.note_path)
    assert state["source_fingerprint"] == result.source_fingerprint
    assert state["note_fingerprint"]
    assert state["note_sha256"] == state["note_fingerprint"]
    assert state["vault_note_fingerprint"]
    assert state["revision"] == result.revision == 1
    assert state["taste_rating"] == 5
    assert state["difficulty_rating"] == 4
    assert state["time_rating"] == 3
    assert recipe_archive_status(output) == "archived"


def test_same_source_updates_once_and_protects_manual_vault_edits(tmp_path: Path) -> None:
    output = _recipe_output(tmp_path)
    vault = tmp_path / "vault"
    first = archive_recipe_to_obsidian(output, vault)

    (output / "note.md").write_text("# 刀鱼两吃\n\n更新后的做法。\n", encoding="utf-8")
    assert recipe_archive_status(output) == "stale"
    second = archive_recipe_to_obsidian(output, vault)
    assert second.action == "updated"
    assert second.revision == 2
    assert "更新后的做法" in second.note_path.read_text(encoding="utf-8")
    assert len(list((vault / "菜谱").rglob("*.md"))) == 1

    second.note_path.write_text(second.note_path.read_text(encoding="utf-8") + "\n手写补充。\n", encoding="utf-8")
    (output / "note.md").write_text("# 刀鱼两吃\n\n第三版。\n", encoding="utf-8")
    with pytest.raises(ObsidianArchiveConflict, match="edited manually"):
        archive_recipe_to_obsidian(output, vault)
    failed = json.loads((output / "archive.json").read_text(encoding="utf-8"))
    assert failed["status"] == "failed"

    overwritten = archive_recipe_to_obsidian(output, vault, conflict="overwrite")
    assert overwritten.action == "updated"
    assert "第三版" in overwritten.note_path.read_text(encoding="utf-8")
    assert "手写补充" not in overwritten.note_path.read_text(encoding="utf-8")


def test_history_reports_archived_then_stale_workflow_status(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    output = _recipe_output(outputs)
    archive_recipe_to_obsidian(output, tmp_path / "vault")

    assert scan_history(outputs)[0].workflow_status == "archived"
    (output / "note.md").write_text("# 手工修改后的菜谱\n", encoding="utf-8")
    assert scan_history(outputs)[0].workflow_status == "stale"


def test_category_change_moves_same_source_without_duplicate(tmp_path: Path) -> None:
    output = _recipe_output(tmp_path)
    vault = tmp_path / "vault"
    old = archive_recipe_to_obsidian(output, vault, category="中餐")
    new = archive_recipe_to_obsidian(output, vault, category="汤")

    assert new.action == "updated"
    assert new.note_path.parent.name == "汤"
    assert not old.note_path.exists()
    assert len(list((vault / "菜谱").rglob("*.md"))) == 1


def test_unsafe_or_missing_local_image_is_not_silently_archived(tmp_path: Path) -> None:
    output = _recipe_output(tmp_path)
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"secret")
    (output / "note.md").write_text("# Demo\n\n![](../secret.jpg)\n", encoding="utf-8")

    with pytest.raises(Exception, match="escapes"):
        archive_recipe_to_obsidian(output, tmp_path / "vault")


def test_archive_confirmed_knowledge_as_dry_tip_notes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    entry = CookingKnowledgeEntry(
        id="heat-pan",
        title="热锅再下蛋",
        category="火候",
        content="炒蛋前先把锅烧热。",
        rationale="减少粘锅。",
        applicable_to=["炒蛋", "煎蛋"],
        evidence="审核中间信息，不写入成品",
        confidence=0.45,
        tags=["鸡蛋"],
        source_title="鸡蛋教程",
        source_url="https://www.bilibili.com/video/BV1egg",
        review_status="approved",
    )

    result = archive_knowledge_to_obsidian([entry], vault)[0]

    assert result.action == "created"
    assert result.note_path.parent == vault / "烹饪技巧" / "火候"
    markdown = result.note_path.read_text(encoding="utf-8")
    assert 'type: "cooking-tip"' in markdown
    assert 'status: "approved"' in markdown
    assert "炒蛋前先把锅烧热" in markdown
    assert "减少粘锅" in markdown
    assert "审核中间信息" not in markdown
    assert "0.45" not in markdown
    assert "[鸡蛋教程](https://www.bilibili.com/video/BV1egg)" in markdown

    unchanged = archive_knowledge_to_obsidian([entry], vault)[0]
    assert unchanged.action == "unchanged"
    assert len(list((vault / "烹饪技巧").rglob("*.md"))) == 1

    rejected = {
        "id": "draft-tip",
        "title": "待审核技巧",
        "category": "其他",
        "content": "这条不应进入最终笔记本。",
        "review_status": "draft",
    }
    assert archive_knowledge_to_obsidian([rejected], vault) == ()
    assert not list((vault / "烹饪技巧").rglob("*draft-tip*.md"))


def test_recipe_batch_reports_each_item_and_continues(tmp_path: Path) -> None:
    valid = _recipe_output(tmp_path, "valid", bvid="BV1valid")
    broken = tmp_path / "broken"
    broken.mkdir()

    result = archive_recipes_to_obsidian([valid, broken], tmp_path / "vault")

    assert result.archived_count == 1
    assert result.failed_count == 1
    assert result.items[0].result is not None
    assert "Missing finalized recipe note" in result.items[1].error
