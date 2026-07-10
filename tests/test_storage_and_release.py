from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bili_recipe_notes.batch_queue import create_batch_state, list_batch_states, save_batch_state
from bili_recipe_notes.config import UIConfig, config_path, load_config, save_config
from bili_recipe_notes.knowledge_base import (
    CookingKnowledgeEntry,
    due_review_entries,
    knowledge_base_path,
    load_knowledge_entries,
    merge_knowledge_entries,
    save_knowledge_entries,
    upsert_knowledge_entries,
)
from bili_recipe_notes.storage import CorruptDataError, atomic_write_text, backup_path
from bili_recipe_notes import ui_launcher


def _entry(entry_id: str, title: str = "热锅再下蛋") -> CookingKnowledgeEntry:
    return CookingKnowledgeEntry(
        id=entry_id,
        title=title,
        category="火候",
        content="炒蛋前先把锅烧热。",
    )


def test_atomic_write_keeps_previous_version_and_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    atomic_write_text(path, "first")
    atomic_write_text(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    assert backup_path(path).read_text(encoding="utf-8") == "first"
    assert list(tmp_path.glob("*.tmp")) == []


def test_config_save_refuses_to_overwrite_corrupt_document(tmp_path: Path) -> None:
    path = save_config(UIConfig(out_dir="first"), tmp_path)
    save_config(UIConfig(out_dir="second"), tmp_path)
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(CorruptDataError):
        save_config(UIConfig(out_dir="third"), tmp_path)

    assert path.read_text(encoding="utf-8") == "{broken"
    assert json.loads(backup_path(path).read_text(encoding="utf-8"))["out_dir"] == "first"


def test_config_wrong_top_level_type_is_corrupt(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(CorruptDataError, match="expected dict"):
        load_config(tmp_path)


def test_batch_updates_are_backed_up_and_corruption_is_not_skipped(tmp_path: Path) -> None:
    state = create_batch_state(["https://example.com/a"], {}, batch_id="demo", project_root=tmp_path)
    state.items[0].status = "running"
    path = save_batch_state(state, project_root=tmp_path)

    assert backup_path(path).exists()
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(CorruptDataError):
        list_batch_states(project_root=tmp_path)


def test_knowledge_base_corruption_blocks_upsert_and_preserves_backup(tmp_path: Path) -> None:
    save_knowledge_entries([_entry("a")], project_root=tmp_path)
    save_knowledge_entries([_entry("a")], project_root=tmp_path)
    path = knowledge_base_path(tmp_path)
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(CorruptDataError):
        load_knowledge_entries(project_root=tmp_path)
    with pytest.raises(CorruptDataError):
        upsert_knowledge_entries([_entry("b")], project_root=tmp_path)

    assert path.read_text(encoding="utf-8") == "{broken"
    assert json.loads(backup_path(path).read_text(encoding="utf-8"))["entries"][0]["id"] == "a"


def test_knowledge_merge_rejects_self_and_unknown_entries(tmp_path: Path) -> None:
    save_knowledge_entries([_entry("a"), _entry("b", "旺火快炒")], project_root=tmp_path)

    with pytest.raises(ValueError, match="cannot be merged into itself"):
        merge_knowledge_entries("a", ["a"], project_root=tmp_path)
    with pytest.raises(KeyError, match="missing"):
        merge_knowledge_entries("a", ["missing"], project_root=tmp_path)

    assert {entry.id for entry in load_knowledge_entries(project_root=tmp_path)} == {"a", "b"}


def test_due_reviews_exclude_future_dates(tmp_path: Path) -> None:
    entries = [
        CookingKnowledgeEntry(
            id="new",
            title="新卡",
            category="技巧",
            content="尚未安排复习。",
        ),
        CookingKnowledgeEntry(
            id="past",
            title="到期卡",
            category="技巧",
            content="已经到期。",
            next_review_at="2020-01-01T00:00:00+00:00",
        ),
        CookingKnowledgeEntry(
            id="future",
            title="未来卡",
            category="技巧",
            content="尚未到期。",
            next_review_at="2999-01-01T00:00:00+00:00",
        ),
    ]
    save_knowledge_entries(entries, project_root=tmp_path)

    assert {entry.id for entry in due_review_entries(project_root=tmp_path)} == {"new", "past"}


def test_ui_launcher_resolves_bundled_ui_and_binds_loopback(monkeypatch, tmp_path: Path) -> None:
    bundled_ui = tmp_path / "bili_recipe_notes" / "ui.py"
    bundled_ui.parent.mkdir(parents=True)
    bundled_ui.write_text("# bundled UI", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert ui_launcher.resolve_ui_path() == bundled_ui

    from streamlit.web import cli as streamlit_cli

    monkeypatch.setattr(streamlit_cli, "main", lambda: 0)
    monkeypatch.setattr(sys, "argv", [])
    assert ui_launcher.main() == 0
    assert "--server.address=127.0.0.1" in sys.argv
    assert "--browser.serverAddress=127.0.0.1" in sys.argv


def test_packaging_specs_use_real_entrypoints_and_bundle_streamlit_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    cli_spec = (root / "bili-recipe-notes.spec").read_text(encoding="utf-8")
    ui_spec = (root / "bili-recipe-notes-ui.spec").read_text(encoding="utf-8")

    assert '"bili_recipe_notes/__main__.py"' in cli_spec
    assert '("bili_recipe_notes/ui.py", "bili_recipe_notes")' in ui_spec
    assert "collect_data_files(\"streamlit\")" in ui_spec
