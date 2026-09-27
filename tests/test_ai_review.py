from __future__ import annotations

import copy
import json

import pytest

from bili_recipe_notes import ai_review
from bili_recipe_notes.review_policy import AI_REVIEW_FILE_NAME, load_ai_review, set_ai_review_visibility


def write_recipe(root, name="one"):
    folder = root / name
    folder.mkdir()
    recipe = {
        "title": "炒鸡蛋",
        "ingredients": [{"name": "鸡蛋", "amount": "2个"}],
        "seasonings": [{"name": "盐", "amount": "少许"}],
        "steps": [{"title": "翻炒", "action": "中火炒熟"}],
    }
    (folder / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False), encoding="utf-8")
    (folder / "transcript.json").write_text(json.dumps([
        {"start": 0, "text": "准备两个鸡蛋，加少许盐，中火炒熟。"},
        {"start": 10, "text": "今天我们来这家饭店品尝招牌菜，只吃饭不介绍做法。"},
        {"start": 20, "text": "喜欢这个视频请大家点赞关注并点击下方链接购买。"},
    ], ensure_ascii=False), encoding="utf-8")
    (folder / "source.json").write_text('{"title":"推广 广告"}', encoding="utf-8")
    for name in ("note.md", "raw-report.md", "recipe.review.json", "recipe.html"):
        (folder / name).write_bytes(f"原报告 {name}\r\n保留全部字节".encode())
    return folder


def answer(classification="recipe", visibility="visible", confidence=0.98):
    return {
        "status": "completed", "classification": classification, "visibility": visibility,
        "confidence": confidence, "tags": ["家常菜"], "reason": "有明确教学步骤",
        "evidence": [{"text": "准备两个鸡蛋，加少许盐，中火炒熟。", "start": 0}],
        "item_checks": [
            {"id": "ingredients:0", "verdict": "supported", "reason": "原文“准备两个鸡蛋”支持数量"},
            {"id": "seasonings:0", "verdict": "supported", "reason": "原文“加少许盐”支持用量"},
            {"id": "steps:0", "verdict": "supported", "reason": "原文“中火炒熟”支持操作"},
        ] if classification == "recipe" else [],
    }


def mock_answer(monkeypatch, payload):
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", lambda *args, **kwargs: json.dumps(payload, ensure_ascii=False))


def test_review_preserves_every_original_byte_and_uses_provider_options(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    before = {file.name: file.read_bytes() for file in folder.iterdir()}
    seen = {}
    def completion(prompt, **options):
        seen.update(prompt=prompt, options=options)
        return json.dumps(answer(), ensure_ascii=False)
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", completion)

    result = ai_review.review_recipe(folder, provider="codex", codex_model="test-model", codex_profile="test-profile")

    assert result["status"] == "completed"
    assert result == load_ai_review(folder)
    assert seen["options"] == {"provider": "codex", "codex_model": "test-model", "codex_profile": "test-profile"}
    assert "不要调用工具" in seen["prompt"]
    assert "<untrusted_material>" in seen["prompt"]
    assert before == {name: (folder / name).read_bytes() for name in before}
    assert {file.name for file in folder.iterdir()} == set(before) | {AI_REVIEW_FILE_NAME}


@pytest.mark.parametrize("response", [None, "not json", '{"classification":"non_recipe"}'])
def test_failure_keeps_previous_review_and_originals(monkeypatch, tmp_path, response):
    folder = write_recipe(tmp_path)
    mock_answer(monkeypatch, answer())
    ai_review.review_recipe(folder)
    before = {file.name: file.read_bytes() for file in folder.iterdir()}
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", lambda *args, **kwargs: response)
    with pytest.raises((RuntimeError, ValueError)):
        ai_review.review_recipe(folder, only_pending=False)
    assert before == {file.name: file.read_bytes() for file in folder.iterdir()}


@pytest.mark.parametrize("defect", ["missing", "duplicate", "unknown", "extra", "nan", "boolean"])
def test_strict_contract_rejects_incomplete_or_invalid_response(monkeypatch, tmp_path, defect):
    folder = write_recipe(tmp_path)
    payload = answer()
    if defect == "missing":
        payload["item_checks"].pop()
    elif defect == "duplicate":
        payload["item_checks"].append(copy.deepcopy(payload["item_checks"][0]))
    elif defect == "unknown":
        payload["item_checks"][0]["id"] = "ingredients:100"
    elif defect == "extra":
        payload["command"] = "rm -rf ."
    else:
        payload["confidence"] = float("nan") if defect == "nan" else True
    mock_answer(monkeypatch, payload)
    with pytest.raises(ValueError):
        ai_review.review_recipe(folder)
    assert not (folder / AI_REVIEW_FILE_NAME).exists()


@pytest.mark.parametrize("defect", ["no_transcript", "fake_quote", "advertising", "low_confidence", "uncertain", "wrong_time", "short_quote"])
def test_weak_non_recipe_evidence_cannot_hide(monkeypatch, tmp_path, defect):
    folder = write_recipe(tmp_path)
    payload = answer("non_recipe", "hidden")
    payload["evidence"] = [{"text": "今天我们来这家饭店品尝招牌菜，只吃饭不介绍做法。", "start": 10}]
    if defect == "no_transcript":
        (folder / "transcript.json").unlink()
    elif defect == "fake_quote":
        payload["evidence"][0]["text"] = "模型编造这不是菜谱的视频证据"
    elif defect == "advertising":
        payload["evidence"] = [{"text": "喜欢这个视频请大家点赞关注并点击下方链接购买。", "start": 20}]
    elif defect == "low_confidence":
        payload["confidence"] = 0.89
    elif defect == "uncertain":
        payload["classification"] = "uncertain"
    elif defect == "wrong_time":
        payload["evidence"][0]["start"] = 88
    elif defect == "short_quote":
        payload["evidence"] = [{"text": "只吃饭", "start": 10}]
    mock_answer(monkeypatch, payload)
    result = ai_review.review_recipe(folder)
    assert result["visibility"] == "visible"
    assert result["status"] == "needs_review"


def test_clear_non_recipe_can_hide_and_manual_override_survives_rereview(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    payload = answer("non_recipe", "hidden")
    payload["evidence"] = [{"text": "今天我们来这家饭店品尝招牌菜，只吃饭不介绍做法。", "start": 10}]
    mock_answer(monkeypatch, payload)
    assert ai_review.review_recipe(folder)["visibility"] == "hidden"
    human = {}
    def completing_after_manual_change(*args, **kwargs):
        human.update(set_ai_review_visibility(folder, "visible"))
        return json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", completing_after_manual_change)
    result = ai_review.review_recipe(folder, only_pending=False)
    assert result["visibility_override"] == "visible"
    assert result["override_at"] == human["override_at"]


def test_invalid_old_sidecar_can_be_repaired(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    (folder / AI_REVIEW_FILE_NAME).write_text('{"visibility_override": []}', encoding="utf-8")
    mock_answer(monkeypatch, answer())
    result = ai_review.review_recipe(folder)
    assert result == load_ai_review(folder)
    assert "visibility_override" not in result


def test_recipe_change_during_call_does_not_save_stale_review(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    mock_answer(monkeypatch, answer())
    ai_review.review_recipe(folder)
    previous = (folder / AI_REVIEW_FILE_NAME).read_bytes()
    def completion(*args, **kwargs):
        (folder / "recipe.json").write_text('{"title":"Changed"}', encoding="utf-8")
        return json.dumps(answer())
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", completion)
    with pytest.raises(RuntimeError, match="已变更"):
        ai_review.review_recipe(folder, only_pending=False)
    assert (folder / AI_REVIEW_FILE_NAME).read_bytes() == previous


def test_existing_valid_review_is_skipped_and_unsupported_assertions_are_flagged(monkeypatch, tmp_path):
    folder = write_recipe(tmp_path)
    payload = answer()
    payload["item_checks"][0]["reason"] = "我认为原文支持，没有引用"
    mock_answer(monkeypatch, payload)
    result = ai_review.review_recipe(folder)
    assert result["status"] == "needs_review"
    assert result["item_checks"][0]["verdict"] == "needs_review"
    def unexpected(*args, **kwargs):
        raise AssertionError("valid review must not be called again")
    monkeypatch.setattr(ai_review, "complete_markdown_prompt", unexpected)
    assert ai_review.review_recipe(folder) == result
