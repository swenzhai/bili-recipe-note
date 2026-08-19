from __future__ import annotations

from bili_recipe_notes.meal_plans import (
    MealCandidate,
    MealPlanItem,
    delete_meal_plan,
    load_meal_plans,
    meal_candidate_kind,
    recommend_meal_recipe_ids,
    record_meal_plan_practice,
    save_meal_plan,
)


def test_meal_plan_store_preserves_items_and_multiple_practices(tmp_path) -> None:
    saved = save_meal_plan(
        name="周末四人餐",
        guest_count=4,
        child_count=1,
        occasion="带小孩",
        notes="少辣",
        items=[
            MealPlanItem("steamed-egg", "蒸水蛋", 2.0, "儿童份少盐"),
            MealPlanItem("soup", "冬瓜汤", 1.5),
        ],
        project_root=tmp_path,
    )
    record_meal_plan_practice(
        saved.id,
        rating=5,
        notes="份量刚好",
        practiced_on="2026-08-19",
        project_root=tmp_path,
    )
    record_meal_plan_practice(
        saved.id,
        rating=4,
        notes="汤可以减半",
        practiced_on="2026-08-26",
        project_root=tmp_path,
    )
    updated = save_meal_plan(
        name="周末四人家庭餐",
        guest_count=4,
        child_count=1,
        occasion="带小孩",
        notes="固定保留",
        items=saved.items,
        plan_id=saved.id,
        project_root=tmp_path,
    )

    assert updated.name == "周末四人家庭餐"
    assert len(updated.practice_records) == 2
    assert updated.items[0].servings_multiplier == 2.0
    assert load_meal_plans(tmp_path) == [updated]
    assert delete_meal_plan(saved.id, tmp_path)
    assert load_meal_plans(tmp_path) == []


def test_meal_recommendation_balances_kinds_and_avoids_spicy_food_for_children() -> None:
    candidates = [
        MealCandidate("spicy", "麻辣水煮鱼", "中餐", quality_score=100),
        MealCandidate("egg", "虾仁蒸水蛋", "中餐", quality_score=70),
        MealCandidate("egg-duplicate", "虾仁蒸水蛋", "中餐", quality_score=60),
        MealCandidate("chicken", "清蒸鸡", "中餐", quality_score=80),
        MealCandidate("vegetable", "白灼菜心", "中餐", tags=("素菜",), quality_score=80),
        MealCandidate("soup", "冬瓜排骨汤", "汤羹", quality_score=80),
        MealCandidate("staple", "瑶柱炒饭", "主食", quality_score=80),
        MealCandidate("dessert", "姜撞奶", "糕点", quality_score=80),
    ]

    selected = recommend_meal_recipe_ids(
        candidates,
        guest_count=4,
        child_count=1,
        occasion="带小孩",
    )

    assert len(selected) == 5
    assert "spicy" not in selected
    assert not {"egg", "egg-duplicate"} <= set(selected)
    assert {"vegetable", "soup", "staple", "dessert"} <= set(selected)
    kinds = {meal_candidate_kind(candidate) for candidate in candidates if candidate.recipe_id in selected}
    assert {"main", "vegetable", "soup", "staple", "dessert"} == kinds
    assert meal_candidate_kind(MealCandidate("tofu", "鲍汁鸡蛋豆腐", "中餐", tags=("调味汤汁",))) != "soup"
    assert meal_candidate_kind(MealCandidate("cake", "香煎玉米糕", "糕点", tags=("面粉",))) == "dessert"
