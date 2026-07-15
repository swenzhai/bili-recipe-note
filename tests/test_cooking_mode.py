from __future__ import annotations

import pytest

from bili_recipe_notes.cooking_mode import (
    build_shopping_list,
    convert_amount,
    parse_servings,
    serving_scale,
    shopping_list_markdown,
)
from bili_recipe_notes.recipe_extractor import Recipe, RecipeIngredient, RecipeStep


def _recipe() -> Recipe:
    return Recipe(
        title="番茄炒蛋",
        source_url="https://example.com/video",
        servings="2人份",
        ingredients=[
            RecipeIngredient(name="鸡蛋", amount="2个"),
            RecipeIngredient(name="番茄", amount="500克", note="切块"),
        ],
        seasonings=[
            RecipeIngredient(name="油", amount="1-2汤匙"),
            RecipeIngredient(name="盐", amount="少许"),
        ],
        tools=["炒锅"],
        shopping_list=["鸡蛋 2个", "厨房纸"],
        steps=[RecipeStep(title="翻炒", start_time=0, action="炒熟")],
        summary_tips=[],
        uncertain_points=[],
    )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2人份", 2.0),
        ("3-4 人", 3.5),
        ("½份", 0.5),
        ("约 6 servings", 6.0),
        ("适量", None),
        (None, None),
    ],
)
def test_parse_servings(label: str | None, expected: float | None) -> None:
    assert parse_servings(label) == expected


def test_serving_scale_requires_a_valid_baseline_and_target() -> None:
    assert serving_scale("2人份", 5) == 2.5
    with pytest.raises(ValueError, match="无法识别"):
        serving_scale("按需", 4)
    with pytest.raises(ValueError, match="大于 0"):
        serving_scale("2人份", 0)


@pytest.mark.parametrize(
    ("amount", "factor", "unit_system", "expected"),
    [
        ("2个", 2, "original", "4个"),
        ("1/2杯", 2, "metric", "240毫升"),
        ("500克", 2, "metric", "1千克"),
        ("1-2汤匙", 2, "metric", "30–60毫升"),
        ("1 1/2 kg", 2, "metric", "3千克"),
        ("约0.5克", 1, "metric", "约500毫克"),
        ("少许", 3, "metric", "少许"),
        ("2个（约200克）", 2, "metric", "2个（约200克）"),
    ],
)
def test_convert_amount_is_conservative_and_supports_metric_units(
    amount: str,
    factor: float,
    unit_system: str,
    expected: str,
) -> None:
    assert convert_amount(amount, factor, unit_system).text == expected


def test_build_shopping_list_scales_structured_items_and_avoids_manual_duplicates() -> None:
    recipe = _recipe()

    items = build_shopping_list(recipe, factor=2, unit_system="metric")

    assert [item.label for item in items] == [
        "鸡蛋：4个",
        "番茄：1千克（切块）",
        "油：30–60毫升",
        "盐：少许",
        "厨房纸：按需",
    ]
    assert [item.category for item in items] == ["主料", "主料", "调料", "调料", "其他"]


def test_shopping_list_markdown_is_a_portable_checkbox_list() -> None:
    recipe = _recipe()
    items = build_shopping_list(recipe, factor=2, unit_system="metric")

    markdown = shopping_list_markdown(recipe, items, 2)

    assert markdown.startswith("# 番茄炒蛋购物清单")
    assert "- 用量倍率：2×" in markdown
    assert "## 主料" in markdown
    assert "- [ ] 番茄：1千克（切块）" in markdown
    assert "## 调料" in markdown
    assert "## 其他" in markdown


def test_legacy_manual_shopping_amounts_are_scaled_when_structured_items_are_missing() -> None:
    recipe = _recipe()
    recipe.ingredients = []
    recipe.seasonings = []
    recipe.shopping_list = ["鸡蛋 2个", "油：1汤匙", "厨房纸"]

    items = build_shopping_list(recipe, factor=2, unit_system="metric")

    assert [item.label for item in items] == ["鸡蛋：4个", "油：30毫升", "厨房纸：按需"]
