from __future__ import annotations

from bili_recipe_notes.recipe_extractor import Recipe, RecipeIngredient, RecipeStep
from bili_recipe_notes.recipe_review import (
    create_recipe_review,
    decide_review_item,
    load_recipe_review,
    recipe_from_completed_review,
)


def _recipe() -> Recipe:
    return Recipe(
        title="炒鸡蛋",
        source_url="https://example.com/video",
        ingredients=[RecipeIngredient(name="鸡蛋", amount="2个", evidence="两个鸡蛋", confidence=0.9)],
        seasonings=[RecipeIngredient(name="盐", amount="少许", evidence="加盐", confidence=0.5)],
        tools=["炒锅"],
        steps=[RecipeStep(title="翻炒", start_time=10, action="中火翻炒", evidence="中火翻炒", confidence=0.8)],
        summary_tips=["不要炒老"],
        uncertain_points=["盐量待确认"],
    )


def test_recipe_review_supports_accept_edit_skip_and_apply(tmp_path) -> None:
    create_recipe_review(_recipe(), tmp_path)
    review = load_recipe_review(tmp_path)
    assert len(review["items"]) == 3
    assert review["status"] == "pending"

    decide_review_item(tmp_path, "ingredients:0", "accepted")
    decide_review_item(
        tmp_path,
        "seasonings:0",
        "edited",
        value={"name": "盐", "amount": "1克", "confidence": 1.0},
        comment="对照视频确认",
    )
    decide_review_item(tmp_path, "steps:0", "edited", value={"title": "翻炒", "start_time": 10, "action": "中火炒熟"})
    recipe = recipe_from_completed_review(tmp_path)

    assert recipe.ingredients[0].name == "鸡蛋"
    assert recipe.seasonings[0].amount == "1克"
    assert recipe.steps[0].action == "中火炒熟"
    assert load_recipe_review(tmp_path)["status"] == "applied"
