from __future__ import annotations

import json
from pathlib import Path

import pytest

from bili_recipe_notes.review_policy import (
    AI_REVIEW_FILE_NAME,
    is_recipe_hidden,
    load_ai_review,
    review_recipe_hash,
    set_ai_review_visibility,
)


def _write_review(folder: Path, **changes: object) -> dict:
    if not (folder / "recipe.json").exists():
        (folder / "recipe.json").write_text('{"title":"广告","steps":[]}', encoding="utf-8")
    review = {
        "schema_version": 1,
        "status": "completed",
        "source_recipe_sha256": review_recipe_hash(folder),
        "classification": "non_recipe",
        "visibility": "hidden",
        "confidence": 0.99,
        "tags": ["非菜谱", "广告"],
        "reason": "仅介绍商品，没有烹饪过程",
        **changes,
    }
    (folder / AI_REVIEW_FILE_NAME).write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return review


def test_review_policy_ignores_missing_corrupt_and_outdated_reviews(tmp_path: Path) -> None:
    assert not is_recipe_hidden(tmp_path)
    _write_review(tmp_path)
    assert is_recipe_hidden(tmp_path)
    (tmp_path / "recipe.json").write_text('{"title":"新菜谱","steps":[]}', encoding="utf-8")
    assert load_ai_review(tmp_path) is None
    assert not is_recipe_hidden(tmp_path)
    (tmp_path / AI_REVIEW_FILE_NAME).write_bytes(b"\xffbad-json")
    assert load_ai_review(tmp_path) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"status": "running"},
        {"status": []},
        {"classification": "other"},
        {"visibility": "other"},
        {"confidence": float("nan")},
        {"confidence": float("inf")},
        {"confidence": True},
        {"confidence": 1.2},
        {"confidence": "1.0"},
        {"tags": [42]},
        {"visibility_override": "maybe"},
        {"reason": []},
        {"evidence": ["not an evidence object"]},
        {"evidence": [{"text": 42}]},
        {"evidence": [{"text": "字幕", "start": float("inf")}]},
        {"item_checks": ["not an item object"]},
        {"item_checks": [{"id": "steps:0", "verdict": "supported", "reason": 42}]},
        {"item_checks": [{"id": "wrong", "verdict": "supported", "reason": "字幕"}]},
        {"item_checks": [{"id": "steps:0", "verdict": "wrong", "reason": "字幕"}]},
    ],
)
def test_invalid_reviews_cannot_hide_recipes(tmp_path: Path, changes: dict) -> None:
    _write_review(tmp_path, **changes)
    assert load_ai_review(tmp_path) is None
    assert not is_recipe_hidden(tmp_path)


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": 0.89},
        {"classification": "recipe"},
        {"classification": "uncertain"},
        {"status": "needs_review"},
        {"visibility": "visible"},
    ],
)
def test_only_confident_completed_non_recipes_are_automatically_hidden(tmp_path: Path, changes: dict) -> None:
    _write_review(tmp_path, **changes)
    assert load_ai_review(tmp_path) is not None
    assert not is_recipe_hidden(tmp_path)


def test_review_accepts_structured_evidence_and_item_checks(tmp_path: Path) -> None:
    _write_review(
        tmp_path,
        evidence=[{"text": "只介绍火候技巧", "start": 3.5}],
        item_checks=[{"id": "steps:0", "verdict": "needs_review", "reason": "缺少字幕依据"}],
    )
    assert load_ai_review(tmp_path) is not None


def test_manual_visibility_is_reversible_and_changes_only_the_sidecar(tmp_path: Path) -> None:
    _write_review(tmp_path, classification="technique")
    recipe_before = (tmp_path / "recipe.json").read_bytes()
    original = load_ai_review(tmp_path)

    visible = set_ai_review_visibility(tmp_path, "visible")
    assert not is_recipe_hidden(tmp_path)
    assert visible["override_at"]
    assert visible["classification"] == original["classification"]
    assert visible["visibility"] == "hidden"

    set_ai_review_visibility(tmp_path, "hidden")
    assert is_recipe_hidden(tmp_path)
    set_ai_review_visibility(tmp_path, None)
    assert is_recipe_hidden(tmp_path)
    assert (tmp_path / "recipe.json").read_bytes() == recipe_before

    (tmp_path / "recipe.json").write_bytes(recipe_before + b"\n")
    with pytest.raises(ValueError, match="有效"):
        set_ai_review_visibility(tmp_path, "visible")


def test_human_can_hide_a_valid_review_that_needs_verification(tmp_path: Path) -> None:
    _write_review(tmp_path, status="needs_review", visibility="visible", classification="uncertain")
    set_ai_review_visibility(tmp_path, "hidden")
    assert is_recipe_hidden(tmp_path)
    set_ai_review_visibility(tmp_path, None)
    assert not is_recipe_hidden(tmp_path)
