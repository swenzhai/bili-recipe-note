from __future__ import annotations

import json
import base64

import pytest

from bili_recipe_notes import ai_review, review_policy as policy
from test_ai_review import answer, mock_answer, write_recipe


def _update(path, changes):
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(changes)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_semantic_fingerprint_ignores_presentation_but_tracks_evidence(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    mock_answer(monkeypatch, answer())
    original = ai_review.review_recipe(folder)
    assert original["schema_version"] == 2
    assert original["model_config"]["provider"] == "codex"
    _update(folder / "recipe.json", {"taste_rating": 5, "cover_image_path": "new-cover.jpg"})
    recipe = json.loads((folder / "recipe.json").read_text())
    recipe["steps"][0]["screenshot_path"] = "images/new.jpg"
    recipe["ingredients"][0]["confidence"] = None
    recipe["steps"][0]["tips"] = None
    recipe.update(category="蛋类", cuisine="家常", tags=["快手"], prep_items=[])
    (folder / "recipe.json").write_text(json.dumps(recipe, indent=4, ensure_ascii=False))
    (folder / "note.md").write_text("重新排版后的笔记")
    _update(folder / "source.json", {"thumbnail": "https://example/new.jpg"})
    assert policy.load_ai_review(folder) == original
    _update(folder / "source.json", {"title": "不同的来源内容"})
    assert policy.load_ai_review(folder) is None


@pytest.mark.parametrize("change", ["recipe", "transcript", "rules"])
def test_real_content_changes_invalidate_ai_and_keep_manual_visibility(monkeypatch, tmp_path, change):
    folder = write_recipe(tmp_path)
    mock_answer(monkeypatch, answer())
    ai_review.review_recipe(folder)
    policy.set_ai_review_visibility(folder, "hidden")
    if change == "recipe":
        _update(folder / "recipe.json", {"title": "不同菜谱"})
    elif change == "transcript":
        (folder / "transcript.json").write_text('[{"text":"替换字幕，原有依据已经不成立"}]')
    else:
        monkeypatch.setattr(policy, "AI_REVIEW_RULES_VERSION", "future-rules")
    assert policy.load_ai_review(folder) is None
    assert policy.is_recipe_hidden(folder)
    policy.set_ai_review_visibility(folder, "visible")
    assert not policy.is_recipe_hidden(folder)


def test_changed_transcript_during_call_never_overwrites_old_review(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    mock_answer(monkeypatch, answer())
    ai_review.review_recipe(folder)
    original = (folder / policy.AI_REVIEW_FILE_NAME).read_bytes()
    def response(*args, **kwargs):
        (folder / "transcript.json").write_text('[{"text":"来源内容已改变"}]')
        return json.dumps(answer())
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", response)
    with pytest.raises(RuntimeError, match="已变更"):
        ai_review.review_recipe(folder, only_pending=False)
    assert (folder / policy.AI_REVIEW_FILE_NAME).read_bytes() == original


def test_rereview_history_preserves_old_configuration_and_human_decisions(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    mock_answer(monkeypatch, answer())
    ai_review.review_recipe(folder, codex_model="old-model")
    policy.set_ai_review_visibility(folder, "visible")
    policy.set_ai_review_classification(folder, "recipe", comment="人工对照视频确认")
    policy.set_ai_review_item_decision(folder, "ingredients:0", "supported", comment="视频中可见两个鸡蛋")
    before = policy.load_review_record(folder)
    result = ai_review.review_recipe(folder, only_pending=False, codex_model="new-model")
    archives = [json.loads(path.read_text()) for path in (tmp_path / ".ai-review" / "history").glob("*/*.json")]
    latest = [item for item in archives if item["event"] == "ai_rereview"][-1]
    assert latest["review"] == before
    assert latest["review"]["model_config"]["codex_model"] == "old-model"
    assert result["model_config"]["codex_model"] == "new-model"
    assert result["visibility_override"] == "visible"
    assert result["classification_override"] == "recipe"
    assert result["manual_review"] == before["manual_review"]


def test_manual_review_can_complete_without_ai_and_expires_on_content_change(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    originals = {path.name: path.read_bytes() for path in folder.iterdir()}
    policy.set_ai_review_classification(folder, "recipe", comment="人工确认菜谱")
    with pytest.raises(ValueError, match="逐项"):
        policy.complete_manual_ai_review(folder)
    for key in ("ingredients:0", "seasonings:0", "steps:0"):
        policy.set_ai_review_item_decision(folder, key, "supported", comment="人工对照视频确认")
    policy.complete_manual_ai_review(folder)
    state = policy.get_review_state(folder)
    assert state["manual_completed"] and not state["needs_confirmation"]
    assert not state["ai_valid"]
    assert originals == {name: (folder / name).read_bytes() for name in originals}
    _update(folder / "recipe.json", {"taste_rating": 4})
    assert policy.get_review_state(folder)["manual_completed"]
    monkeypatch.setattr(policy, "AI_REVIEW_RULES_VERSION", "future-rules")
    assert policy.get_review_state(folder)["manual_completed"]
    _update(folder / "recipe.json", {"title": "内容变更"})
    changed = policy.get_review_state(folder)
    assert not changed["manual_completed"] and changed["needs_confirmation"]
    assert changed["item_decisions"] == {}


def test_manual_unsupported_and_false_quote_cannot_complete(tmp_path):
    folder = write_recipe(tmp_path)
    policy.set_ai_review_classification(folder, "recipe")
    with pytest.raises(ValueError, match="人工确认依据"):
        policy.set_ai_review_item_decision(folder, "steps:0", "supported")
    with pytest.raises(ValueError, match="无法.*核实"):
        policy.set_ai_review_item_decision(folder, "steps:0", "supported", evidence=[{"text": "捏造字幕"}])
    policy.set_ai_review_item_decision(folder, "steps:0", "needs_review", comment="没有火候依据")
    with pytest.raises(ValueError, match="逐项"):
        policy.complete_manual_ai_review(folder)
    policy.set_ai_review_classification(folder, "non_recipe")
    policy.complete_manual_ai_review(folder)
    assert policy.get_review_state(folder)["manual_completed"]
    policy.set_ai_review_classification(folder, "uncertain")
    assert not policy.get_review_state(folder)["manual_completed"]
    with pytest.raises(ValueError, match="分类"):
        policy.complete_manual_ai_review(folder)


def test_manual_display_precedence_and_unpublished_menu(tmp_path):
    folder = write_recipe(tmp_path)
    policy.set_ai_review_visibility(folder, "visible")
    visible = policy.resolve_recipe_visibility(folder, source_classification="technique")
    assert visible["visible"] and visible["reason_code"] == "manual_visible"
    unpublished = policy.resolve_recipe_visibility(folder, source_classification="technique", published=False)
    assert not unpublished["visible"] and unpublished["content_visible"]
    assert unpublished["reason_code"] == "unpublished"
    policy.set_ai_review_visibility(folder, None)
    policy.set_ai_review_classification(folder, "recipe")
    assert policy.resolve_recipe_visibility(folder, source_classification="non_recipe")["visible"]
    policy.set_ai_review_visibility(folder, "hidden")
    assert policy.resolve_recipe_visibility(folder)["hidden"]
    assert policy.resolve_record_visibility(None, None, source_classification="technique")["classification"] == "technique"


def test_incomplete_legacy_review_never_automatically_hides(tmp_path):
    folder = write_recipe(tmp_path)
    record = {"schema_version": 1, "status": "completed", "source_recipe_sha256": policy.review_recipe_hash(folder),
              "classification": "non_recipe", "visibility": "hidden", "confidence": 1.0, "tags": []}
    (folder / policy.AI_REVIEW_FILE_NAME).write_text(json.dumps(record))
    assert policy.load_ai_review(folder) is None
    assert not policy.is_recipe_hidden(folder)
    policy.set_ai_review_visibility(folder, "hidden")
    assert policy.is_recipe_hidden(folder)


def test_complete_legacy_record_requests_upgrade_but_manual_decision_survives(tmp_path):
    folder = write_recipe(tmp_path)
    record = {**answer(), "schema_version": 1, "source_recipe_sha256": policy.review_recipe_hash(folder)}
    (folder / policy.AI_REVIEW_FILE_NAME).write_text(json.dumps(record))
    state = policy.get_review_state(folder)
    assert state["requires_upgrade"] and not state["ai_valid"]
    assert state["record"] == record
    policy.set_ai_review_visibility(folder, "hidden")
    assert policy.is_recipe_hidden(folder)


@pytest.mark.parametrize("defect", ["missing", "unknown", "inconsistent_status"])
def test_corrupted_saved_item_checks_require_rereview(monkeypatch, tmp_path, defect):
    folder = write_recipe(tmp_path)
    mock_answer(monkeypatch, answer())
    review = ai_review.review_recipe(folder)
    if defect == "missing":
        review["item_checks"].pop()
    elif defect == "unknown":
        review["item_checks"][0]["id"] = "ingredients:100"
    else:
        review["item_checks"][0]["verdict"] = "needs_review"
    (folder / policy.AI_REVIEW_FILE_NAME).write_text(json.dumps(review))
    assert policy.load_ai_review(folder) is None
    assert not policy.is_recipe_hidden(folder)


def test_real_rating_save_preserves_ai_and_manual_completion(monkeypatch, tmp_path):
    from bili_recipe_notes import ui
    folder = write_recipe(tmp_path)
    recipe = json.loads((folder / "recipe.json").read_text())
    recipe.update(source_url="https://example.com/video", tools=[], summary_tips=[], uncertain_points=[])
    recipe["steps"][0]["start_time"] = 0
    (folder / "recipe.json").write_text(json.dumps(recipe))
    mock_answer(monkeypatch, answer())
    before = ai_review.review_recipe(folder)
    for key in ("ingredients:0", "seasonings:0", "steps:0"):
        policy.set_ai_review_item_decision(folder, key, "supported", comment="人工对照视频确认")
    policy.complete_manual_ai_review(folder)
    ui._save_recipe_ratings(folder, {"taste_rating": 5, "difficulty_rating": 2, "time_rating": 1})
    after = policy.load_ai_review(folder)
    assert after["input_fingerprint"] == before["input_fingerprint"]
    assert policy.get_review_state(folder)["manual_completed"]
    assert json.loads((folder / "recipe.json").read_text())["taste_rating"] == 5


def test_corrupt_sidecar_is_archived_byte_for_byte_when_repaired(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    broken = b'{"visibility_override":"hidden", invalid-json \xff'
    (folder / policy.AI_REVIEW_FILE_NAME).write_bytes(broken)
    assert not policy.is_recipe_hidden(folder)
    mock_answer(monkeypatch, answer())
    ai_review.review_recipe(folder)
    snapshots = policy.list_review_history(folder)
    assert base64.b64decode(snapshots[0]["review"]["raw_base64"]) == broken
    assert snapshots[0]["review"]["_unparsed_record"]


@pytest.mark.parametrize("changed_file", ["recipe.json", "transcript.json"])
def test_manual_submit_rejects_changed_content_since_render(tmp_path, changed_file):
    folder = write_recipe(tmp_path)
    policy.set_ai_review_classification(folder, "recipe")
    for item_id in ("ingredients:0", "seasonings:0", "steps:0"):
        policy.set_ai_review_item_decision(folder, item_id, "supported", comment="视频核对")
    displayed = policy.get_review_state(folder)["content_fingerprint"]
    original = (folder / policy.AI_REVIEW_FILE_NAME).read_bytes()
    if changed_file == "recipe.json":
        _update(folder / changed_file, {"ingredients": [{"name": "鸭蛋", "amount": "3个"}]})
    else:
        (folder / changed_file).write_text('[{"text":"现在使用三个鸭蛋"}]')
    with pytest.raises(ValueError, match="内容已变更"):
        policy.set_ai_review_item_decision(folder, "ingredients:0", "supported", comment="旧页面看到两个鸡蛋",
                                           expected_content_fingerprint=displayed)
    with pytest.raises(ValueError, match="内容已变更"):
        policy.complete_manual_ai_review(folder, expected_content_fingerprint=displayed)
    assert (folder / policy.AI_REVIEW_FILE_NAME).read_bytes() == original


def test_manual_uncertain_keeps_content_visible_and_requires_confirmation(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    payload = answer("non_recipe", "hidden")
    payload["evidence"] = [{"text": "今天我们来这家饭店品尝招牌菜，只吃饭不介绍做法。", "start": 10}]
    mock_answer(monkeypatch, payload)
    ai_review.review_recipe(folder)
    assert policy.is_recipe_hidden(folder)
    policy.set_ai_review_classification(folder, "uncertain", comment="需要重新核实视频主要内容")
    state = policy.get_review_state(folder)
    assert state["ai_valid"] and state["ai_review"]["status"] == "completed"
    assert state["classification"] == "uncertain" and state["needs_confirmation"]
    assert state["content_visible"] and state["reason_code"] == "manual_uncertain"
    legacy = policy.resolve_recipe_visibility(folder, source_classification="technique")
    assert legacy["content_visible"] and legacy["visible"]
    unpublished = policy.resolve_recipe_visibility(folder, source_classification="non_recipe", published=False)
    assert unpublished["content_visible"] and not unpublished["visible"]
    assert unpublished["reason_code"] == "unpublished"
    policy.set_ai_review_visibility(folder, "hidden")
    hidden = policy.resolve_recipe_visibility(folder, source_classification="technique")
    assert hidden["hidden"] and hidden["reason_code"] == "manual_hidden"
    assert policy.get_review_state(folder)["needs_confirmation"]
