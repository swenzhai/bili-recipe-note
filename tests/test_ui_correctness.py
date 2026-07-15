from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bili_recipe_notes import ui
from bili_recipe_notes.batch_queue import BatchQueueItem, BatchQueueState


def _recipe(title: str, *, ingredients: list[dict] | None = None, seasonings: list[dict] | None = None) -> dict:
    return {
        "title": title,
        "source_url": f"https://example.com/{title}",
        "video_title": title,
        "uploader": "tester",
        "servings": None,
        "total_time": None,
        "difficulty": None,
        "ingredients": ingredients or [],
        "seasonings": seasonings or [],
        "tools": [],
        "prep_items": [],
        "shopping_list": [],
        "steps": [],
        "summary_tips": [],
        "uncertain_points": [],
    }


def _write_record(root: Path, folder_name: str, recipe: dict | str) -> Path:
    folder = root / folder_name
    folder.mkdir(parents=True)
    payload = recipe if isinstance(recipe, str) else json.dumps(recipe, ensure_ascii=False)
    (folder / "recipe.json").write_text(payload, encoding="utf-8")
    (folder / "note.md").write_text(f"# {folder_name}\n", encoding="utf-8")
    (folder / "quality.json").write_text(
        json.dumps({"score": 0, "issues": [], "summary": "test"}),
        encoding="utf-8",
    )
    return folder


def _open_edit_page(out_dir: Path) -> AppTest:
    app_path = Path(ui.__file__)
    at = AppTest.from_file(str(app_path), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(out_dir))
    at.selectbox(key="main_page").set_value("编辑修复")
    return at.run()


def test_empty_recipe_tables_have_fixed_distinct_schemas() -> None:
    assert ui._editor_table([], ui.INGREDIENT_COLUMNS) == {
        "name": [],
        "amount": [],
        "note": [],
    }
    assert ui._editor_rows(
        {"name": ["鸡蛋", ""], "amount": ["2 个", ""], "note": [None, None]},
        ui.INGREDIENT_COLUMNS,
    ) == [{"name": "鸡蛋", "amount": "2 个", "note": None}]
    assert ui._merge_editor_rows(
        {"name": ["鸡蛋"], "amount": ["3 个"], "note": [None]},
        ui.INGREDIENT_COLUMNS,
        [{"name": "鸡蛋", "amount": "2 个", "confidence": 0.8, "evidence": "两个鸡蛋"}],
    ) == [{"name": "鸡蛋", "amount": "3 个", "note": None, "confidence": 0.8, "evidence": "两个鸡蛋"}]


def test_empty_ingredient_and_seasoning_editors_do_not_duplicate(tmp_path: Path) -> None:
    folder = _write_record(tmp_path, "empty", _recipe("空表菜谱"))

    at = _open_edit_page(tmp_path)

    assert not at.exception
    record_key = ui._record_key(folder)
    assert at.text_input(key=f"edit_{record_key}_title").value == "空表菜谱"


def test_switching_recipe_does_not_reuse_editor_widget_state(tmp_path: Path) -> None:
    first = _write_record(tmp_path, "first", _recipe("菜谱甲"))
    second = _write_record(tmp_path, "second", _recipe("菜谱乙"))
    at = _open_edit_page(tmp_path)

    selector = at.selectbox(key="edit_select")
    first_label = next(label for label in selector.options if str(label).startswith("菜谱甲 |"))
    second_label = next(label for label in selector.options if str(label).startswith("菜谱乙 |"))
    selector.set_value(first_label)
    at.run()

    at.text_input(key=f"edit_{ui._record_key(first)}_title").set_value("未保存的甲")
    at.selectbox(key="edit_select").set_value(second_label)
    at.run()

    assert not at.exception
    assert at.text_input(key=f"edit_{ui._record_key(second)}_title").value == "菜谱乙"


def test_save_refreshes_editor_state_and_creates_backups(tmp_path: Path) -> None:
    folder = _write_record(tmp_path, "save", _recipe("待保存菜谱"))
    at = _open_edit_page(tmp_path)
    record_key = ui._record_key(folder)

    next(widget for widget in at.checkbox if widget.label == "使用 LLM 结构化抽取 / 重写").uncheck()
    at.checkbox(key=f"edit_{record_key}_confirm_overwrite").check()
    at.run()
    at.button(key=f"edit_{record_key}_save_recipe").click()
    at.run()

    assert not at.exception
    assert any("已保存" in message.value for message in at.success)
    assert list(folder.glob("recipe.before-recipe-edit-*.json"))
    assert list(folder.glob("note.before-recipe-edit-*.md"))


def test_malformed_recipe_is_reported_without_crashing_page(tmp_path: Path) -> None:
    _write_record(tmp_path, "broken", "{ definitely-not-json")

    at = _open_edit_page(tmp_path)

    assert not at.exception
    assert any("recipe.json 已损坏" in error.value for error in at.error)


def test_safe_recipe_loader_and_batch_link_errors(tmp_path: Path) -> None:
    broken = tmp_path / "recipe.json"
    broken.write_text("[]", encoding="utf-8")
    data, error = ui._safe_recipe_to_data(broken)
    assert data is None
    assert "顶层必须" in (error or "")

    links = tmp_path / "links.txt"
    links.write_text("https://example.com/a\nhttps://example.com/a\n", encoding="utf-8")
    assert ui._load_batch_urls("https://example.com/b\n", str(links)) == [
        "https://example.com/b",
        "https://example.com/a",
    ]
    with pytest.raises(FileNotFoundError, match="链接文件不存在"):
        ui._load_batch_urls("", str(tmp_path / "missing.txt"))
    with pytest.raises(IsADirectoryError, match="不是文件"):
        ui._load_batch_urls("", str(tmp_path))


def test_backups_are_timestamped_and_navigation_is_conditional(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("old", encoding="utf-8")
    backups = ui._backup_files([note], "test-edit")
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "old"
    assert backups[0].name.startswith("note.before-test-edit-")

    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    assert not at.exception
    assert not at.tabs
    assert at.selectbox(key="main_page").value == "单视频生成"
    assert not any(button.label == "运行环境检查" for button in at.button)
    assert "zip" in ui.EXPORT_KINDS
    assert ui.EXPORT_MIME_TYPES[".zip"] == "application/zip"


def test_mobile_cooking_mode_scales_ingredients_and_navigates_steps(tmp_path: Path) -> None:
    recipe = _recipe(
        "番茄炒蛋",
        ingredients=[{"name": "鸡蛋", "amount": "2个"}],
        seasonings=[{"name": "油", "amount": "1汤匙"}],
    )
    recipe["servings"] = "2人份"
    recipe["prep_items"] = ["鸡蛋打散"]
    recipe["steps"] = [
        {"title": "炒鸡蛋", "start_time": 5, "action": "中火炒至凝固", "heat": "中火"},
        {"title": "混合", "start_time": 20, "action": "加入番茄翻炒", "duration": "2分钟"},
    ]
    folder = _write_record(tmp_path, "cook", recipe)
    record_key = ui._record_key(folder)
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(tmp_path))
    at.selectbox(key="main_page").set_value("烹饪模式")
    at.run()

    assert not at.exception
    assert at.number_input(key=f"cook_{record_key}_target_servings").value == 2.0
    assert any(widget.label == "鸡蛋：2个" for widget in at.checkbox)
    assert any(widget.label == "鸡蛋打散" for widget in at.checkbox)
    assert "1. 炒鸡蛋" in "\n".join(markdown.value for markdown in at.markdown)

    at.number_input(key=f"cook_{record_key}_target_servings").set_value(4.0)
    at.selectbox(key=f"cook_{record_key}_unit_system").set_value("换算为公制")
    at.run()
    assert any(widget.label == "鸡蛋：4个" for widget in at.checkbox)
    assert any(widget.label == "油：30毫升" for widget in at.checkbox)

    at.button(key=f"cook_{record_key}_next").click()
    at.run()
    assert not at.exception
    assert "2. 混合" in "\n".join(markdown.value for markdown in at.markdown)


def test_history_zip_export_stays_available_for_download(tmp_path: Path) -> None:
    folder = _write_record(tmp_path, "bundle", _recipe("打包菜谱"))
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(tmp_path))
    at.selectbox(key="main_page").set_value("草稿与归档")
    at.run()

    export_selector = next(widget for widget in at.selectbox if widget.label == "导出格式")
    export_selector.set_value("zip")
    at.run()
    next(button for button in at.button if button.label == "导出").click()
    at.run()

    assert not at.exception
    assert (folder / "bundle.recipe.zip").is_file()
    assert any(button.label == "下载导出文件" for button in at.get("download_button"))


def test_note_preview_resolves_local_images_without_changing_markdown(tmp_path: Path) -> None:
    image = tmp_path / "images" / "step_01.jpg"
    image.parent.mkdir()
    image.write_bytes(b"image")

    class FakeStreamlit:
        def __init__(self) -> None:
            self.markdowns: list[str] = []
            self.images: list[tuple[str, str | None, object]] = []
            self.warnings: list[str] = []

        def markdown(self, value: str) -> None:
            self.markdowns.append(value)

        def image(self, value: str, caption: str | None = None, **kwargs) -> None:
            self.images.append((value, caption, kwargs.get("width")))

        def warning(self, value: str) -> None:
            self.warnings.append(value)

    fake = FakeStreamlit()
    markdown = "# Demo\n\n![切菜](images/step_01.jpg)\n\n操作说明\n"

    ui._render_note_preview(fake, markdown, tmp_path)

    assert fake.images == [(str(image.resolve()), "切菜", 360)]
    assert "# Demo" in "\n".join(fake.markdowns)
    assert "操作说明" in "\n".join(fake.markdowns)
    assert not fake.warnings
    assert ui._local_markdown_image(tmp_path, "../secret.jpg") is None


def test_cli_advanced_prompt_is_available_and_propagated() -> None:
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()

    assert not at.exception
    assert at.selectbox(key="llm_cli_prompt_preset").value == "空白 / 自定义"
    assert at.text_area(key="llm_cli_extra_instructions_editor") is not None

    config = ui.UIConfig(llm_provider="codex", llm_cli_extra_instructions="严格提取温度")
    assert ui._job_options("https://example.com", config).llm_cli_extra_instructions == "严格提取温度"
    assert ui._job_options("https://example.com", config).max_recipe_steps == 10
    assert ui._job_options("https://example.com", config).max_step_images == 4
    assert ui._optimize_options(config).llm_cli_extra_instructions == "严格提取温度"
    assert ui._content_analysis_options(config, "tips.md").llm_cli_extra_instructions == "严格提取温度"
    assert ui._knowledge_extraction_options(config).llm_cli_extra_instructions == "严格提取温度"


def test_review_page_can_create_item_by_item_review(tmp_path: Path) -> None:
    recipe = _recipe("待审核菜谱", ingredients=[{"name": "鸡蛋", "amount": "2个", "evidence": "两个鸡蛋", "confidence": 0.8}])
    recipe["steps"] = [
        {"title": "翻炒", "start_time": 10, "action": "中火翻炒", "evidence": "中火翻炒", "confidence": 0.7}
    ]
    folder = _write_record(tmp_path, "review", recipe)
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(tmp_path))
    at.selectbox(key="main_page").set_value("审核确认")
    at.run()
    next(button for button in at.button if button.label == "创建逐项审核版").click()
    at.run()

    assert not at.exception
    assert (folder / "recipe.review.json").is_file()
    assert any(button.label == "采用并下一项" for button in at.button)
    assert any("置信度" in caption.value for caption in at.caption)


def test_draft_can_be_archived_directly_to_obsidian(tmp_path: Path) -> None:
    folder = _write_record(tmp_path / "outputs", "archive", _recipe("直接归档菜谱"))
    record_key = ui._record_key(folder)
    vault = tmp_path / "vault"
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(tmp_path / "outputs"))
    next(widget for widget in at.text_input if widget.label == "笔记本目录").set_value(str(vault))
    at.selectbox(key="main_page").set_value("草稿与归档")
    at.run()
    at.selectbox(key=f"history_{record_key}_rating_taste_rating").set_value(5)
    at.selectbox(key=f"history_{record_key}_rating_difficulty_rating").set_value(4)
    at.selectbox(key=f"history_{record_key}_rating_time_rating").set_value(2)
    next(button for button in at.button if button.label == "无需修改，直接归档").click()
    at.run()

    assert not at.exception
    assert (folder / "archive.json").is_file()
    archived_notes = list((vault / "菜谱").rglob("*.md"))
    assert archived_notes
    saved_recipe = json.loads((folder / "recipe.json").read_text(encoding="utf-8"))
    assert saved_recipe["taste_rating"] == 5
    assert saved_recipe["difficulty_rating"] == 4
    assert saved_recipe["time_rating"] == 2
    archived_note = archived_notes[0].read_text(encoding="utf-8")
    assert 'rating: 5' in archived_note
    assert "- 个人喜爱度：★★★★★（5/5）" in archived_note


def test_edit_page_exposes_taxonomy_and_final_markdown_archive(tmp_path: Path) -> None:
    folder = _write_record(tmp_path, "editable", _recipe("完整编辑菜谱"))
    at = _open_edit_page(tmp_path)
    record_key = ui._record_key(folder)

    assert at.selectbox(key=f"edit_{record_key}_category") is not None
    assert at.selectbox(key=f"edit_{record_key}_cuisine") is not None
    assert at.selectbox(key=f"edit_{record_key}_taste_rating") is not None
    assert at.selectbox(key=f"edit_{record_key}_difficulty_rating") is not None
    assert at.selectbox(key=f"edit_{record_key}_time_rating") is not None
    assert at.text_input(key=f"edit_{record_key}_tags") is not None
    assert at.text_area(key=f"edit_{record_key}_final_markdown") is not None
    assert at.button(key=f"edit_{record_key}_save_and_archive") is not None


def test_batch_results_keep_per_item_edit_review_and_archive_actions(monkeypatch, tmp_path: Path) -> None:
    folder = _write_record(tmp_path, "batch-item", _recipe("批量菜谱"))
    state = BatchQueueState(
        batch_id="ui-batch",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        options={},
        items=[
            BatchQueueItem(
                url="https://example.com/batch",
                status="done",
                output_folder=str(folder),
                note_path=str(folder / "note.md"),
            )
        ],
    )
    monkeypatch.setattr("bili_recipe_notes.batch_queue.list_batch_states", lambda: [state])
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    at.selectbox(key="main_page").set_value("批量处理")
    at.run()
    next(widget for widget in at.selectbox if widget.label == "已有批次").set_value(
        "ui-batch | 2026-01-01T00:00:00+00:00 | 1 条"
    )
    at.run()

    labels = {button.label for button in at.button}
    assert {"编辑这条", "审核这条", "直接归档这条", "归档本批次全部已完成草稿"} <= labels
