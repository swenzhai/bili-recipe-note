from bili_recipe_notes.markdown_writer import render_markdown
from bili_recipe_notes.recipe_extractor import Recipe, RecipeIngredient, RecipeStep


def test_render_markdown_basic() -> None:
    recipe = Recipe(
        title="番茄炒蛋",
        source_url="https://example.com",
        video_title="家常番茄炒蛋",
        uploader="UP主",
        ingredients=[RecipeIngredient(name="鸡蛋")],
        seasonings=[RecipeIngredient(name="盐")],
        tools=["炒锅"],
        steps=[RecipeStep(title="步骤1", start_time=1.0, action="先打蛋", screenshot_path="images/step_01.jpg")],
        summary_tips=["火不要太大"],
        uncertain_points=[],
    )
    md = render_markdown(recipe)
    assert "# 番茄炒蛋" in md
    assert "![步骤 1：步骤1](images/step_01.jpg)" in md
    assert "时间：[00:00:01](https://example.com?t=1)" in md
    assert "## 来源" in md
    assert "- 分类：中餐" in md
    assert "- 菜系：中式" in md
    assert "- 标签：鸡蛋、炒" in md
    assert "## 配料信息" in md
    assert "## 烹饪" in md
    assert "## 关键点速查" in md
    assert "火不要太大" in md
    assert "置信度" not in md
    assert "需要确认" not in md
