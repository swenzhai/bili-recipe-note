from __future__ import annotations

import shutil
from urllib.parse import quote

import pytest

from bili_recipe_notes.batch_queue import batches_dir, create_batch_state, load_batch_state, save_batch_state
from bili_recipe_notes.branding import branding_dir
from bili_recipe_notes.config import UIConfig, config_path, load_config, save_config
from bili_recipe_notes.data_paths import (
    DATA_DIR_ENV, creators_dir, data_root, ensure_data_layout, exports_dir, logs_dir, recipes_dir,
    relocate_value, resolve_stored_path, state_dir,
)
from bili_recipe_notes.downloader import imported_cookie_path
from bili_recipe_notes.knowledge_base import CookingKnowledgeEntry, knowledge_base_path, load_knowledge_entries, save_knowledge_entries
from bili_recipe_notes.meal_plans import meal_plans_path
from bili_recipe_notes.mobile_sync import MobileSyncStore, recipe_visibility_states
from bili_recipe_notes.storage import atomic_write_json


def test_default_paths_share_one_data_directory_without_creating_it(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert data_root() == tmp_path / "data"
    assert recipes_dir() == tmp_path / "data" / "recipes"
    assert creators_dir() == tmp_path / "data" / "creators"
    assert exports_dir() == tmp_path / "data" / "exports"
    assert logs_dir() == tmp_path / "data" / "logs"
    expected_state = tmp_path / "data" / "state"
    assert config_path() == expected_state / "config.json"
    assert batches_dir() == expected_state / "batches"
    assert knowledge_base_path() == expected_state / "knowledge_base.json"
    assert meal_plans_path() == expected_state / "meal-plans.json"
    assert branding_dir() == expected_state / "branding"
    assert imported_cookie_path() == expected_state / "cookies" / "bilibili-edge.txt"
    assert load_config().out_dir == str(recipes_dir())
    assert recipe_visibility_states(recipes_dir()) == {}
    assert not data_root().exists()


@pytest.mark.parametrize("external", ["portable-data", "nested/portable-data"])
def test_data_override_is_lazy_and_used_by_config_and_mobile_store(tmp_path, monkeypatch, external):
    project = tmp_path / "source"
    project.mkdir()
    monkeypatch.setenv(DATA_DIR_ENV, external)
    assert data_root(project) == project / external
    assert load_config(project).out_dir == str(project / external / "recipes")
    external_root = tmp_path / "external drive" / "recipes-data"
    monkeypatch.setenv(DATA_DIR_ENV, str(external_root))
    assert UIConfig().out_dir == str(external_root / "recipes")
    assert config_path(project) == external_root / "state" / "config.json"
    config = load_config(project)
    save_config(config, project)
    store = MobileSyncStore(project)
    assert store.out_dir == external_root / "recipes"
    assert store.database_path == external_root / "state" / "mobile-sync.sqlite3"
    assert store.media_dir == external_root / "state" / "mobile-media"
    assert not (project / "data").exists()


def _layout(root):
    atomic_write_json(state_dir(root) / "layout.json", {
        "data_root": "/old/project/data",
        "project_root": "/old/project",
        "path_mappings": {
            "/old/project/outputs": "recipes",
            "/old/project/outputs/chef": "recipes/王刚",
            "C:\\old\\.bili-recipe-notes": "state",
        },
    })


def test_moved_data_rebases_only_known_path_prefixes_and_longest_match(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    _layout(tmp_path)
    relocated = relocate_value({
        "folder": "/old/project/outputs/chef/dish",
        "relative": "outputs/chef/dish/note.md",
        "database": "C:\\old\\.bili-recipe-notes\\mobile-sync.sqlite3",
        "url": "https://example.com/old/project/outputs/dish",
        "text": "查看 /old/project/outputs/chef/dish",
        "sibling": "/old/project/outputs-other/dish",
        "values": ["/old/project/data/creators/王刚/video_links.txt", 3],
    }, tmp_path)
    assert relocated["folder"] == str(recipes_dir(tmp_path) / "王刚/dish")
    assert relocated["relative"] == str(recipes_dir(tmp_path) / "王刚/dish/note.md")
    assert relocated["database"] == str(state_dir(tmp_path) / "mobile-sync.sqlite3")
    assert relocated["url"] == "https://example.com/old/project/outputs/dish"
    assert relocated["text"] == "查看 /old/project/outputs/chef/dish"
    assert relocated["sibling"] == "/old/project/outputs-other/dish"
    assert relocated["values"] == [str(creators_dir(tmp_path) / "王刚/video_links.txt"), 3]
    assert resolve_stored_path("data/recipes/王刚/dish", tmp_path) == recipes_dir(tmp_path) / "王刚/dish"


def test_config_batch_and_knowledge_paths_relocate_without_rewriting_data(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    _layout(tmp_path)
    old_folder = "/old/project/outputs/chef/dish"
    config_file = save_config(UIConfig(out_dir="/old/project/outputs", llm_cli_extra_instructions=old_folder), tmp_path)
    batch = create_batch_state(["https://example.com/video"], {
        "out": "/old/project/outputs", "llm_cli_extra_instructions": old_folder,
    }, batch_id="portable", project_root=tmp_path)
    batch.items[0].output_folder = old_folder
    batch.items[0].note_path = old_folder + "/note.md"
    batch_file = save_batch_state(batch, tmp_path)
    knowledge_file = save_knowledge_entries([CookingKnowledgeEntry(
        id="test", title="技巧", category="火候", content=old_folder,
        source_output_folder=old_folder,
        source_refs=[{"output_folder": old_folder, "title": "来源"}],
    )], tmp_path)
    before = {path: path.read_bytes() for path in (config_file, batch_file, knowledge_file)}
    loaded_config = load_config(tmp_path)
    loaded_batch = load_batch_state("portable", tmp_path)
    loaded_knowledge = load_knowledge_entries(tmp_path)[0]
    assert loaded_config.out_dir == str(recipes_dir(tmp_path))
    assert loaded_config.llm_cli_extra_instructions == old_folder
    assert loaded_batch.options["out"] == str(recipes_dir(tmp_path))
    assert loaded_batch.options["llm_cli_extra_instructions"] == old_folder
    assert loaded_batch.items[0].output_folder == str(recipes_dir(tmp_path) / "王刚/dish")
    assert loaded_batch.items[0].note_path == str(recipes_dir(tmp_path) / "王刚/dish/note.md")
    assert loaded_knowledge.source_output_folder == str(recipes_dir(tmp_path) / "王刚/dish")
    assert loaded_knowledge.source_refs[0]["output_folder"] == str(recipes_dir(tmp_path) / "王刚/dish")
    assert loaded_knowledge.content == old_folder
    assert {path: path.read_bytes() for path in before} == before


def test_new_library_and_successive_moves_preserve_all_previous_roots(tmp_path, monkeypatch):
    project = tmp_path / "source"
    project.mkdir()
    first = tmp_path / "first-library"
    second = tmp_path / "second-library"
    third = tmp_path / "third-library"
    monkeypatch.setenv(DATA_DIR_ENV, str(first))
    save_config(load_config(project), project)
    original_recipe_path = str(recipes_dir(project) / "王刚/dish")

    shutil.move(first, second)
    monkeypatch.setenv(DATA_DIR_ENV, str(second))
    loaded = load_config(project)
    assert loaded.out_dir == str(second / "recipes")
    save_config(loaded, project)
    second_recipe_path = str(recipes_dir(project) / "王刚/dish")

    shutil.move(second, third)
    monkeypatch.setenv(DATA_DIR_ENV, str(third))
    ensure_data_layout(project)
    assert load_config(project).out_dir == str(third / "recipes")
    for stored_path in (original_recipe_path, second_recipe_path):
        assert resolve_stored_path(stored_path, project) == third / "recipes/王刚/dish"
    assert resolve_stored_path("data/sources/book.pdf", project) == third / "sources/book.pdf"


def test_path_mapping_cache_observes_replaced_layout_and_retries_invalid_layout(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    _layout(tmp_path)
    path = state_dir(tmp_path) / "layout.json"
    stored = "/old/project/outputs/chef/dish"
    assert resolve_stored_path(stored, tmp_path) == recipes_dir(tmp_path) / "王刚/dish"
    atomic_write_json(path, {"path_mappings": {"/old/project/outputs/chef": "recipes/新版"}})
    assert resolve_stored_path(stored, tmp_path) == recipes_dir(tmp_path) / "新版/dish"
    atomic_write_json(path, {"path_mappings": {"/old/project/outputs/chef": "../../outside"}})
    with pytest.raises(ValueError, match="escapes data directory"):
        resolve_stored_path(stored, tmp_path)
    _layout(tmp_path)
    assert resolve_stored_path(stored, tmp_path) == recipes_dir(tmp_path) / "王刚/dish"


def test_document_source_urls_relocate_only_known_files_without_rewriting_library(tmp_path, monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    _layout(tmp_path)
    document_uri = "file:///old/project/data/sources/" + quote("中文 食谱.pdf") + "#page=9"
    windows_uri = "file:///C:/old/.bili-recipe-notes/knowledge-imports/cook%20book.pdf"
    untouched = ["file:///unmapped/document.pdf", "https://www.bilibili.com/video/BV123?p=2"]
    entries = [CookingKnowledgeEntry(
        id=str(index), title="文档知识", category="技巧", content=uri, source_url=uri,
        source_refs=[{"url": uri, "title": "原始来源"}],
    ) for index, uri in enumerate([document_uri, windows_uri, *untouched])]
    path = save_knowledge_entries(entries, tmp_path)
    before = path.read_bytes()

    loaded = load_knowledge_entries(tmp_path)

    expected = [
        (data_root(tmp_path) / "sources" / "中文 食谱.pdf").as_uri() + "#page=9",
        (state_dir(tmp_path) / "knowledge-imports" / "cook book.pdf").as_uri(),
        *untouched,
    ]
    assert [entry.source_url for entry in loaded] == expected
    assert [entry.source_refs[0]["url"] for entry in loaded] == expected
    assert [entry.content for entry in loaded] == [document_uri, windows_uri, *untouched]
    assert path.read_bytes() == before
