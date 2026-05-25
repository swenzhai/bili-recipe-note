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
    assert "![](images/step_01.jpg)" in md
    assert "时间：00:00:01" in md
