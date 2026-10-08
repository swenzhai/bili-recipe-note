from __future__ import annotations

import json

import pytest

from bili_recipe_notes import ai_review, review_policy as policy
from bili_recipe_notes.frame_selection import DEFAULT_REQUIREMENT
from bili_recipe_notes.frame_selection_eligibility import frame_selection_eligibility
from test_ai_review import answer, write_recipe


def review(monkeypatch, folder, classification="technique", **changes):
    payload = {**answer(classification, "hidden"), **changes}
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", lambda *args, **kwargs: json.dumps(payload))
    return ai_review.review_recipe(folder, only_pending=False)


@pytest.mark.parametrize("classification", ["technique", "non_recipe"])
def test_confirmed_non_dishes_are_ineligible_only_for_default_request(monkeypatch, tmp_path, classification):
    folder = write_recipe(tmp_path)
    review(monkeypatch, folder, classification)
    original = {file: file.read_bytes() for file in folder.iterdir()}
    visible = policy.resolve_recipe_visibility(folder)
    assert frame_selection_eligibility(folder, DEFAULT_REQUIREMENT, visible) == "not_a_dish"
    assert frame_selection_eligibility(folder, " \n" + DEFAULT_REQUIREMENT + " ") == "not_a_dish"
    assert frame_selection_eligibility(folder) == "not_a_dish"
    assert frame_selection_eligibility(folder, "找到一张展示清洗锅具的画面", visible) is None
    assert frame_selection_eligibility(folder, "找到一张制作完成的菜品，盛在盘中", visible) is None
    assert original == {file: file.read_bytes() for file in folder.iterdir()}


@pytest.mark.parametrize("changes", [
    {"confidence": 0.89},
    {"status": "needs_review", "visibility": "visible"},
    {"evidence": []},
    {"evidence": [{"text": "没有出现在合成字幕中的证据"}]},
])
def test_provisional_or_unverified_classification_does_not_exclude(monkeypatch, tmp_path, changes):
    folder = write_recipe(tmp_path)
    review(monkeypatch, folder, **changes)
    visible = policy.resolve_recipe_visibility(folder)
    assert visible["classification"] == "technique" and visible["content_visible"]
    assert frame_selection_eligibility(folder, DEFAULT_REQUIREMENT, visible) is None


def test_stale_ai_classification_and_title_keywords_do_not_exclude(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    review(monkeypatch, folder)
    recipe_path = folder / "recipe.json"
    recipe = json.loads(recipe_path.read_text())
    recipe["title"] = "锅具保养及翻新技巧"
    recipe_path.write_text(json.dumps(recipe))
    visible = policy.resolve_recipe_visibility(folder)
    assert not visible["ai_valid"] and visible["classification"] == "uncertain"
    assert frame_selection_eligibility(folder, DEFAULT_REQUIREMENT, visible) is None


def test_manual_recipe_decision_overrides_ai_technique(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    review(monkeypatch, folder)
    policy.set_ai_review_classification(folder, "recipe")
    assert frame_selection_eligibility(folder) is None
    policy.set_ai_review_classification(folder, "uncertain")
    assert frame_selection_eligibility(folder) is None


def test_manual_technique_decision_survives_stale_ai(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    review(monkeypatch, folder, "recipe", visibility="visible")
    policy.set_ai_review_classification(folder, "technique")
    (folder / "transcript.json").write_text("[]")
    assert not policy.resolve_recipe_visibility(folder)["ai_valid"]
    assert frame_selection_eligibility(folder) == "not_a_dish"


def test_manual_visibility_and_menu_decisions_are_unchanged(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    review(monkeypatch, folder)
    policy.set_ai_review_visibility(folder, "visible")
    before = (folder / policy.AI_REVIEW_FILE_NAME).read_bytes()
    visible = policy.resolve_recipe_visibility(folder, published=False)
    assert visible["content_visible"] and not visible["published"]
    assert frame_selection_eligibility(folder, DEFAULT_REQUIREMENT, visible) == "not_a_dish"
    assert policy.resolve_recipe_visibility(folder, published=False) == visible
    assert (folder / policy.AI_REVIEW_FILE_NAME).read_bytes() == before


def test_source_classification_is_taken_from_unified_policy(tmp_path):
    folder = write_recipe(tmp_path)
    visible = policy.resolve_recipe_visibility(folder, source_classification="technique")
    assert frame_selection_eligibility(folder, DEFAULT_REQUIREMENT, visible) == "not_a_dish"
    assert frame_selection_eligibility(folder, "寻找锅具近景", visible) is None


def test_valid_recipe_and_missing_review_remain_eligible(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    assert frame_selection_eligibility(folder) is None
    review(monkeypatch, folder, "recipe", visibility="visible")
    assert frame_selection_eligibility(folder) is None
