from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bili_recipe_notes import ui
from bili_recipe_notes.batch_queue import BatchQueueItem, BatchQueueState, create_batch_state
from bili_recipe_notes.curation import build_curation_review, load_curation_decisions


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


def test_batch_import_discovers_saved_creator_links(tmp_path: Path) -> None:
    creator_dir = tmp_path / "creators" / "123-chef"
    creator_dir.mkdir(parents=True)
    links_path = creator_dir / "video_links.txt"
    links_path.write_text("https://www.bilibili.com/video/BV1xx411c7mD\n", encoding="utf-8")
    (creator_dir / "creator.json").write_text(
        json.dumps({"uploader": "厨师", "video_count": 1}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert ui._saved_creator_link_documents(tmp_path) == [links_path]
    assert "厨师 | 1 条" in ui._creator_link_document_label(links_path)
    assert ui._load_batch_urls("", "", links_path) == [
        "https://www.bilibili.com/video/BV1xx411c7mD"
    ]


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
    assert [expander.label for expander in at.expander[:4]] == [
        "采集与生成",
        "审阅与成稿",
        "使用与知识",
        "系统与迁移",
    ]
    assert not any(button.label == "运行环境检查" for button in at.button)
    assert "zip" in ui.EXPORT_KINDS
    assert ui.EXPORT_MIME_TYPES[".zip"] == "application/zip"


def test_curation_page_compares_sources_and_saves_decision(tmp_path: Path) -> None:
    first_recipe = _recipe(
        "宫保鸡丁",
        ingredients=[{"name": "鸡腿肉", "amount": "300克", "note": "切丁"}],
        seasonings=[{"name": "花椒", "amount": "20粒"}],
    )
    first_recipe["video_title"] = "传统宫保鸡丁完整做法"
    first_recipe["steps"] = [
        {"title": "上浆", "action": "鸡丁加盐和淀粉上浆", "evidence": "鸡丁先上浆"},
        {"title": "炒制", "action": "花椒辣椒炒香后下鸡丁", "evidence": "先下花椒"},
    ]
    second_recipe = _recipe(
        "宫保鸡丁",
        ingredients=[{"name": "鸡腿肉", "amount": "适量"}],
        seasonings=[{"name": "宫保汁", "amount": "一袋"}],
    )
    second_recipe["video_title"] = "一袋宫保酱汁快速出锅"
    second_recipe["steps"] = [{"title": "炒制", "action": "鸡丁炒熟后倒入酱汁"}]
    first = _write_record(tmp_path, "宫保鸡丁--BV1full", first_recipe)
    _write_record(tmp_path, "宫保鸡丁--BV1quick", second_recipe)
    build_curation_review(tmp_path, tmp_path / "curation-review")

    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(tmp_path))
    at.selectbox(key="main_page").set_value("最终菜谱整理")
    at.run()

    assert not at.exception
    assert any(button.label == "采用本组自动建议" for button in at.button)
    assert any(box.label == "选择一个来源查看完整内容" for box in at.selectbox)
    assert any("鸡腿肉" in str(table.value) for table in at.dataframe)

    next(box for box in at.selectbox if box.label == "处理方式").set_value("keep_primary")
    next(area for area in at.text_area if area.label == "取舍理由").set_value("步骤完整，作为主版本")
    next(button for button in at.button if button.label == "保存决定").click()
    at.run()

    assert not at.exception
    decisions = load_curation_decisions(tmp_path / "curation-review")
    assert decisions["items"][first.name]["decision"] == "keep_primary"
    assert decisions["items"][first.name]["review_notes"] == "步骤完整，作为主版本"


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


def test_recipe_detail_shows_complete_recipe_and_opens_same_cooking_record(tmp_path: Path) -> None:
    recipe = _recipe(
        "完整番茄炒蛋",
        ingredients=[{"name": "鸡蛋", "amount": "3个", "note": "打散"}],
        seasonings=[{"name": "盐", "amount": "少许"}],
    )
    recipe.update(
        {
            "category": "家常菜",
            "cuisine": "中式",
            "tags": ["快手", "下饭"],
            "servings": "2人份",
            "total_time": "15分钟",
            "difficulty": "简单",
            "tools": ["炒锅"],
            "prep_items": ["番茄切块"],
            "shopping_list": ["购买新鲜番茄"],
            "steps": [
                {
                    "title": "炒制",
                    "start_time": 12,
                    "action": "先炒鸡蛋，再加入番茄。",
                    "heat": "中火",
                    "duration": "3分钟",
                    "tips": "番茄出汁后再调味。",
                }
            ],
            "summary_tips": ["鸡蛋不要炒老。"],
            "uncertain_points": ["盐量需要按口味确认。"],
        }
    )
    folder = _write_record(tmp_path, "detail", recipe)
    record_key = ui._record_key(folder)
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(tmp_path))
    at.selectbox(key="main_page").set_value("菜谱详情")
    at.run()

    assert not at.exception
    rendered = "\n".join(markdown.value for markdown in at.markdown)
    assert "完整番茄炒蛋" in rendered
    assert "**鸡蛋**：3个（打散）" in rendered
    assert "番茄切块" in rendered
    assert "炒锅" in rendered
    assert "1. 炒制" in rendered
    assert any("番茄出汁后再调味" in warning.value for warning in at.warning)
    assert any("鸡蛋不要炒老" in info.value for info in at.info)
    assert any("盐量需要按口味确认" in warning.value for warning in at.warning)
    assert at.button(key=f"detail_{record_key}_cook") is not None

    at.button(key=f"detail_{record_key}_cook").click()
    at.run()

    assert not at.exception
    assert at.selectbox(key="main_page").value == "烹饪模式"
    assert "完整番茄炒蛋" in str(at.selectbox(key="cook_select").value)


def test_recipe_detail_picker_searches_and_switches_with_quick_buttons(tmp_path: Path) -> None:
    first_recipe = _recipe("宫保鸡丁")
    first_recipe.update({"category": "家常菜", "cuisine": "川菜", "tags": ["下饭"]})
    first_folder = _write_record(tmp_path, "kung-pao", first_recipe)
    second_recipe = _recipe("清炒时蔬")
    second_recipe.update({"category": "素菜", "cuisine": "中式", "tags": ["清淡"]})
    second_folder = _write_record(tmp_path, "vegetables", second_recipe)
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(tmp_path))
    at.selectbox(key="main_page").set_value("菜谱详情")
    at.run()

    at.text_input(key="detail_search").set_value("川菜")
    at.run()

    assert not at.exception
    assert "宫保鸡丁" in str(at.selectbox(key="detail_select").value)
    assert at.button(key=f"detail_pick_{ui._record_key(first_folder)}") is not None

    at.text_input(key="detail_search").set_value("")
    at.run()
    at.button(key=f"detail_pick_{ui._record_key(second_folder)}").click()
    at.run()

    assert not at.exception
    assert "清炒时蔬" in str(at.selectbox(key="detail_select").value)
    assert at.button(key="detail_previous") is not None
    assert at.button(key="detail_next") is not None


def test_library_count_rows_orders_counts_and_uses_recipe_share() -> None:
    assert ui._library_count_rows(
        ["汤羹", "主食", "汤羹", ""],
        "分类",
        recipe_total=4,
    ) == [
        {"分类": "汤羹", "数量": 2, "占菜谱": "50.0%"},
        {"分类": "主食", "数量": 1, "占菜谱": "25.0%"},
        {"分类": "未分类", "数量": 1, "占菜谱": "25.0%"},
    ]


def test_library_overview_renders_charts_counts_and_filters(tmp_path: Path) -> None:
    recipes = [
        ("番茄蛋汤", "汤羹", "中式", ["快手", "家常"]),
        ("冬瓜排骨汤", "汤羹", "中式", ["炖煮", "家常"]),
        ("黄油吐司", "烘焙", "西式", ["早餐"]),
    ]
    for index, (title, category, cuisine, tags) in enumerate(recipes):
        recipe = _recipe(title)
        recipe.update({"category": category, "cuisine": cuisine, "tags": tags})
        _write_record(tmp_path, f"overview-{index}", recipe)

    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    next(widget for widget in at.text_input if widget.label == "输出目录").set_value(str(tmp_path))
    at.selectbox(key="main_page").set_value("菜谱库全览")
    at.run()

    assert not at.exception
    metrics = {metric.label: metric.value for metric in at.metric}
    assert metrics["菜谱总数"] == "3"
    assert metrics["分类数"] == "2"
    assert metrics["菜系数"] == "2"
    assert metrics["标签数"] == "4"
    assert len(at.get("vega_lite_chart")) == 3
    assert at.button(key="overview_open_detail") is not None

    at.selectbox(key="overview_category_filter").set_value("汤羹")
    at.run()

    assert not at.exception
    assert any("当前显示 2 / 3 道菜谱" in caption.value for caption in at.caption)


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
    assert ui._job_options("https://example.com", config).max_step_images == 3
    assert ui._optimize_options(config).llm_cli_extra_instructions == "严格提取温度"
    assert ui._content_analysis_options(config, "tips.md").llm_cli_extra_instructions == "严格提取温度"
    assert ui._knowledge_extraction_options(config).llm_cli_extra_instructions == "严格提取温度"


def test_mobile_web_export_exposes_image_size_options() -> None:
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    at.selectbox(key="main_page").set_value("手机客户端")
    at.run()

    image_mode = at.radio(key="web_library_image_mode")
    assert image_mode.value == "first"
    assert image_mode.options == ["每道菜仅一张（推荐）", "只导出文字", "全部步骤图"]


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
    rendered_markdown = "\n".join(markdown.value for markdown in at.markdown)
    assert "#### 草稿正文" in rendered_markdown
    assert "#### 审核操作" in rendered_markdown


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
    recipe = _recipe("完整编辑菜谱")
    recipe["steps"] = [{"title": "翻炒", "start_time": 10, "end_time": 20, "action": "翻炒至熟"}]
    folder = _write_record(tmp_path, "editable", recipe)
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
    assert any(button.label == "自动查找候选图" for button in at.button)
    assert any(button.label == "此步骤不配图" for button in at.button)
    assert any(button.label == "采用上传图片" for button in at.button)


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
    assert at.selectbox(key="batch_select").value == "ui-batch"
    at.button(key="refresh_batch_ui-batch").click()
    at.run()

    labels = {button.label for button in at.button}
    assert {"查看这条", "编辑这条", "审核这条", "直接归档这条", "归档本批次全部已完成草稿"} <= labels
    assert at.selectbox(key="batch_select").value == "ui-batch"


def test_batch_refresh_uses_cached_state_after_transient_read_failure(monkeypatch) -> None:
    state = BatchQueueState(
        batch_id="cached-batch",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        options={},
        items=[BatchQueueItem(url="https://example.com/a", status="running")],
    )
    call_count = 0

    def flaky_list():
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise OSError("temporary read failure")
        return [state]

    monkeypatch.setattr("bili_recipe_notes.batch_queue.list_batch_states", flaky_list)
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    at.selectbox(key="main_page").set_value("批量处理")
    at.run()
    assert at.selectbox(key="batch_select").value == "cached-batch"

    at.button(key="refresh_batch_cached-batch").click()
    at.run()

    assert not at.exception
    assert at.selectbox(key="batch_select").value == "cached-batch"
    assert any("暂时显示上次成功读取的数据" in warning.value for warning in at.warning)


def test_preferred_batch_preserves_selection_then_falls_back_to_running() -> None:
    first = BatchQueueState("latest", "", "", {}, [])
    second = BatchQueueState("running", "", "", {}, [])
    runtime = {"running": type("Runtime", (), {"status": "running"})()}

    assert ui._preferred_batch_id([first, second], runtime, None, "latest") == "latest"
    assert ui._preferred_batch_id([first, second], runtime, None, "missing") == "running"


def test_running_batch_overlap_blocks_duplicate_work() -> None:
    state = BatchQueueState(
        "active",
        "",
        "",
        {},
        [
            BatchQueueItem(url="https://example.com/a"),
            BatchQueueItem(url="https://example.com/b"),
        ],
    )

    assert ui._running_batch_overlaps(
        ["https://example.com/b", "https://example.com/c"],
        ["active"],
        {"active": state},
    ) == [("active", 1)]


def test_batch_dashboard_summary_estimates_speed_and_finish_time() -> None:
    items = [
        *[BatchQueueItem(url=f"https://example.com/done-{index}", status="done") for index in range(4)],
        BatchQueueItem(url="https://example.com/failed", status="failed"),
        BatchQueueItem(url="https://example.com/running", status="raw_running"),
        *[BatchQueueItem(url=f"https://example.com/pending-{index}") for index in range(4)],
    ]
    state = BatchQueueState("dashboard", "", "", {}, items)
    runtime = type(
        "Runtime",
        (),
        {
            "status": "running",
            "started_at": "2026-08-19T03:00:00+00:00",
            "finished_at": None,
        },
    )()

    summary = ui._batch_dashboard_summary(
        state,
        runtime,
        now=datetime(2026, 8, 19, 4, 0, tzinfo=timezone.utc),
    )

    assert summary["processed"] == 5
    assert summary["remaining"] == 5
    assert summary["progress"] == 0.5
    assert summary["elapsed_seconds"] == 3600
    assert summary["speed_per_hour"] == 5
    assert summary["eta_seconds"] == 3600
    assert summary["estimated_finish"] == datetime(2026, 8, 19, 5, 0, tzinfo=timezone.utc)
    assert ui._format_duration(summary["eta_seconds"]) == "1 小时"


def test_background_dashboard_renders_running_batch(monkeypatch) -> None:
    state = BatchQueueState(
        "dashboard-running",
        "2026-08-19T03:00:00+00:00",
        "2026-08-19T03:30:00+00:00",
        {},
        [
            BatchQueueItem(url="https://example.com/done", status="done"),
            BatchQueueItem(url="https://example.com/current", status="raw_running"),
            BatchQueueItem(url="https://example.com/pending"),
        ],
    )
    runtime = type(
        "Runtime",
        (),
        {
            "status": "running",
            "started_at": "2026-08-19T03:00:00+00:00",
            "finished_at": None,
            "error": None,
        },
    )()
    monkeypatch.setattr("bili_recipe_notes.batch_queue.list_batch_states", lambda: [state])
    monkeypatch.setattr("bili_recipe_notes.batch_runner.get_background_batch_status", lambda _batch_id: runtime)
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    at.selectbox(key="main_page").set_value("任务仪表盘")
    at.run()

    assert not at.exception
    metrics = {metric.label: metric.value for metric in at.metric}
    assert metrics["总数"] == "3"
    assert metrics["已处理"] == "1"
    assert metrics["成功"] == "1"
    assert metrics["剩余"] == "2"
    assert at.selectbox(key="dashboard_refresh_seconds").value == 10
    assert at.button(key="dashboard_refresh_now") is not None
    assert at.button(key="dashboard_open_dashboard-running") is not None
    assert any("当前处理" in info.value and "current" in info.value for info in at.info)
    assert len(at.dataframe) == 1


def test_large_batch_remains_paged_while_switching_pages(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    create_batch_state(
        [f"https://www.bilibili.com/video/BV{index:010d}" for index in range(1206)],
        {},
        batch_id="large-ui-batch",
        project_root=tmp_path,
    )
    at = AppTest.from_file(str(Path(ui.__file__)), default_timeout=20).run()
    at.selectbox(key="main_page").set_value("批量处理")
    at.run()
    at.selectbox(key="batch_select").set_value("large-ui-batch")
    at.run()

    assert not at.exception
    assert at.selectbox(key="batch_table_page_large-ui-batch") is not None
    for page in ("环境检查", "草稿与归档", "批量处理", "工作交接", "批量处理"):
        at.selectbox(key="main_page").set_value(page)
        at.run()
        assert not at.exception
