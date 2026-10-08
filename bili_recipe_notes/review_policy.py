"""Versioned AI evidence, independent human decisions, and one display policy."""
from __future__ import annotations

from .output_layout import review_location

import hashlib
import base64
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_cache import read_json_cached, file_sha256
from .storage import atomic_write_json, file_lock

AI_REVIEW_FILE_NAME = "recipe.ai-review.json"
AI_REVIEW_SCHEMA_VERSION = 2
AI_REVIEW_RULES_VERSION = "recipe-audit-2"
AI_REVIEW_PROMPT_VERSION = "recipe-audit-2"
AI_REVIEW_CLASSIFICATIONS = frozenset({"recipe", "non_recipe", "technique", "uncertain"})
AI_REVIEW_STATUSES = frozenset({"completed", "needs_review"})
_SECTIONS = ("ingredients", "seasonings", "steps")
_RECIPE_FIELDS = (
    "title", "source_url", "video_title", "uploader", "creator_name",
    "servings", "total_time", "difficulty", "ingredients", "seasonings", "tools", "prep_items",
    "shopping_list", "steps", "summary_tips", "uncertain_points",
    "dish_id", "source_time_ranges", "source_segment_ranges",
)
_SOURCE_FIELDS = ("title", "video_title", "description", "source_url", "webpage_url", "url", "id", "bvid", "uploader", "creator_name", "duration",
                  "dish_id", "source_time_ranges", "source_segment_ranges")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _semantic(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _semantic(item) for key, item in value.items()
                if not key.startswith(("screenshot_", "cover_")) and not key.endswith("_rating")
                and item is not None and item != "" and item != []}
    if isinstance(value, list):
        return [_semantic(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def read_review_inputs(output_folder: str | Path) -> dict[str, Any]:
    """Read exactly the semantic inputs supplied to the model; notes are renderings."""
    folder = Path(output_folder)
    recipe = read_json_cached(folder / "recipe.json")
    if not isinstance(recipe, dict):
        raise ValueError("recipe.json 必须是对象")
    for section in _SECTIONS:
        if not isinstance(recipe.get(section, []), list):
            raise ValueError(f"recipe.json 的 {section} 必须是数组")
    transcript = read_json_cached(folder / "transcript.json") if (folder / "transcript.json").exists() else []
    if not isinstance(transcript, list) or not all(isinstance(item, dict) and isinstance(item.get("text"), str) for item in transcript):
        raise ValueError("字幕必须由包含 text 字符串的对象组成")
    source = read_json_cached(folder / "source.json") if (folder / "source.json").exists() else {}
    if not isinstance(source, dict):
        raise ValueError("source.json 必须是对象")
    return {
        "recipe": _semantic({key: recipe[key] for key in _RECIPE_FIELDS if key in recipe}),
        "transcript": [_semantic({key: item[key] for key in ("text", "start", "end", "start_time", "end_time") if key in item}) for item in transcript],
        "source": _semantic({key: source[key] for key in _SOURCE_FIELDS if key in source}),
    }


def fingerprint_review_inputs(inputs: dict[str, Any]) -> str:
    value = {"rules_version": AI_REVIEW_RULES_VERSION, "prompt_version": AI_REVIEW_PROMPT_VERSION,
             "content_fingerprint": fingerprint_review_content(inputs)}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def fingerprint_review_content(inputs: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def review_input_fingerprint(output_folder: str | Path) -> str:
    return fingerprint_review_inputs(read_review_inputs(output_folder))


def review_content_fingerprint(output_folder: str | Path) -> str:
    return fingerprint_review_content(read_review_inputs(output_folder))


def review_recipe_hash(output_folder: str | Path) -> str:
    """Legacy byte fingerprint, retained for v1 compatibility and provenance."""
    return file_sha256(Path(output_folder) / "recipe.json")


def load_review_record(output_folder: str | Path) -> dict[str, Any] | None:
    """Read the raw sidecar. Stale AI evidence does not erase human decisions."""
    try:
        value = read_json_cached(Path(output_folder) / AI_REVIEW_FILE_NAME)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def _valid_evidence(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            return False
        if "start" in item:
            start = item["start"]
            if type(start) not in (int, float) or start < 0 or not math.isfinite(start):
                return False
    return True


def _verified_hiding_evidence(folder: Path, evidence: list[dict]) -> bool:
    try:
        transcript = read_json_cached(folder / "transcript.json")
    except (OSError, ValueError):
        return False
    if not isinstance(transcript, list) or not evidence:
        return False
    for quote in evidence:
        text = re.sub(r"\s+", "", quote["text"])
        found = False
        for segment in transcript:
            if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
                continue
            if not text or text not in re.sub(r"\s+", "", segment["text"]):
                continue
            start = segment.get("start", segment.get("start_time"))
            if "start" not in quote or (type(start) in (int, float) and abs(start - quote["start"]) <= 1):
                found = True
                break
        if not found:
            return False
    return True


def _validated_review(folder: Path, review: dict | None, fingerprint: str | None) -> dict | None:
    try:
        # V1 stored only a recipe byte hash. Without a transcript/rules baseline
        # its evidence cannot be certified current; retain it for history and
        # independent manual decisions, and request a one-time upgrade review.
        if not review or type(review.get("schema_version")) is not int or review["schema_version"] != 2:
            return None
        if review.get("status") not in AI_REVIEW_STATUSES or review.get("classification") not in AI_REVIEW_CLASSIFICATIONS:
            return None
        if review.get("visibility") not in {"visible", "hidden"}:
            return None
        if review.get("visibility_override") not in {None, "visible", "hidden"}:
            return None
        confidence = review.get("confidence")
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1 or not math.isfinite(confidence):
            return None
        if not isinstance(review.get("tags"), list) or not all(isinstance(tag, str) for tag in review["tags"]):
            return None
        if not isinstance(review.get("reason"), str) or not review["reason"].strip() or not _valid_evidence(review.get("evidence")):
            return None
        checks = review.get("item_checks")
        if not isinstance(checks, list):
            return None
        seen = set()
        for item in checks:
            if not isinstance(item, dict):
                return None
            key = item.get("id")
            if not isinstance(key, str) or not re.fullmatch(r"(?:ingredients|seasonings|steps):\d+", key):
                return None
            if key in seen or item.get("verdict") not in {"supported", "needs_review"} or not isinstance(item.get("reason"), str):
                return None
            seen.add(key)
        recipe = read_json_cached(folder / "recipe.json")
        if not isinstance(recipe, dict) or any(not isinstance(recipe.get(section, []), list) for section in _SECTIONS):
            return None
        expected = {f"{section}:{index}" for section in _SECTIONS for index, _ in enumerate(recipe.get(section, []))}
        if not seen <= expected or (review["classification"] == "recipe" and seen != expected):
            return None
        if review["status"] == "completed" and any(item["verdict"] == "needs_review" for item in checks):
            return None
        if (not fingerprint or review.get("input_fingerprint") != fingerprint
                or review.get("rules_version") != AI_REVIEW_RULES_VERSION):
                return None
        if (review["status"] == "completed" and review["visibility"] == "hidden"
                and review["classification"] in {"non_recipe", "technique"}
                and not _verified_hiding_evidence(folder, review["evidence"])):
            return None
        return review
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def load_ai_review(output_folder: str | Path) -> dict[str, Any] | None:
    folder = Path(output_folder)
    record = load_review_record(folder)
    fingerprint = None
    if record and record.get("schema_version") == 2:
        try:
            fingerprint = review_input_fingerprint(folder)
        except (OSError, ValueError, TypeError):
            return None
    return _validated_review(folder, record, fingerprint)


def ai_review_is_hidden(review: dict[str, Any] | None) -> bool:
    """Compatibility helper; callers should pass a validated AI result."""
    if not review:
        return False
    override = review.get("visibility_override")
    if override in ("visible", "hidden"):
        return override == "hidden"
    return bool(review.get("status") == "completed" and review.get("classification") in {"non_recipe", "technique"}
                and review.get("confidence", 0) >= 0.9 and review.get("visibility") == "hidden"
                and _valid_evidence(review.get("evidence")) and any(item["text"].strip() for item in review["evidence"]))


def resolve_record_visibility(record: dict | None, ai_review: dict | None, *, source_classification: str | None = None, published: bool = True) -> dict:
    record = record or {}
    override = record.get("visibility_override")
    manual_class = record.get("classification_override")
    if manual_class not in ("recipe", "non_recipe", "technique", "uncertain"):
        manual_class = None
    source_class = source_classification if source_classification in ("recipe", "non_recipe", "technique", "uncertain") else None
    classification = manual_class or (ai_review or {}).get("classification") or source_class or "uncertain"
    if override == "hidden":
        hidden, code, reason = True, "manual_hidden", "人工设为隐藏"
    elif override == "visible":
        hidden, code, reason = False, "manual_visible", "人工恢复展示，覆盖 AI 和旧来源分类"
    elif manual_class == "recipe":
        hidden, code, reason = False, "manual_recipe", "人工确认是菜谱，覆盖旧来源分类"
    elif manual_class == "uncertain":
        hidden, code, reason = False, "manual_uncertain", "人工标记待确认，保留展示以便进一步核实"
    elif manual_class in ("non_recipe", "technique"):
        hidden, code, reason = True, "manual_classification", "人工分类为非菜谱或独立技巧"
    elif source_classification in ("non_recipe", "technique"):
        hidden, code, reason = True, "source_classification", "旧来源分类排除；可人工恢复展示或修正为菜谱"
    elif ai_review_is_hidden(ai_review):
        hidden, code, reason = True, "ai_hidden", "有效 AI 审核判断为非菜谱或独立技巧"
    else:
        hidden, code, reason = False, "visible", "正常展示" if ai_review else "尚无有效 AI 审核，保留展示"
    content_reason = reason
    if not hidden and published is False:
        code, reason = "unpublished", "菜单已手动下架；恢复审核展示不会自动上架"
    return {"visible": not hidden and published is not False, "hidden": hidden,
            "content_visible": not hidden, "content_reason": content_reason, "published": published,
            "reason": reason, "reason_code": code, "classification": classification, "ai_valid": ai_review is not None}


def get_review_state(output_folder: str | Path) -> dict:
    folder = Path(output_folder)
    record = load_review_record(folder)
    fingerprint = None
    content_fingerprint = None
    error = None
    inputs = None
    try:
        inputs = read_review_inputs(folder)
        fingerprint = fingerprint_review_inputs(inputs)
        content_fingerprint = fingerprint_review_content(inputs)
    except (OSError, ValueError, TypeError) as exc:
        error = str(exc)
    valid = _validated_review(folder, record, fingerprint)
    policy = resolve_record_visibility(record, valid)
    manual = (record or {}).get("manual_review")
    current = isinstance(manual, dict) and content_fingerprint and manual.get("content_fingerprint") == content_fingerprint
    decisions = manual.get("items", {}) if current else {}
    if not isinstance(decisions, dict):
        decisions = {}
    expected = {f"{section}:{index}" for section in _SECTIONS for index, _ in enumerate((inputs or {}).get("recipe", {}).get(section, []))}
    decisions = {key: value for key, value in decisions.items() if key in expected and isinstance(value, dict)
                 and value.get("verdict") in ("supported", "needs_review")}
    classification = policy["classification"]
    all_supported = (classification in ("non_recipe", "technique") or (classification == "recipe" and bool(expected)
                     and expected == set(decisions) and all(value["verdict"] == "supported" for value in decisions.values())))
    completed = bool(current and manual.get("completed_at") and manual.get("classification") == classification and all_supported)
    return {"record": record, "ai_review": valid, "ai_valid": valid is not None, "classification": classification,
            "manual_completed": completed, "item_decisions": decisions, "content_fingerprint": content_fingerprint,
            "requires_upgrade": (record or {}).get("schema_version") == 1,
            "ai_invalid_reason": ("旧版审核缺少完整来源指纹，需要重新审核" if (record or {}).get("schema_version") == 1
                else "审核内容、来源或规则已变化，或记录不完整" if record and valid is None else ""),
            "needs_confirmation": not completed and ((record or {}).get("classification_override") == "uncertain"
                or valid is None or valid.get("status") == "needs_review"
                or any(item["verdict"] == "needs_review" for item in decisions.values())), "error": error, **policy}


def resolve_recipe_visibility(output_folder: str | Path, *, source_classification: str | None = None, published: bool = True) -> dict:
    record = load_review_record(output_folder)
    valid = load_ai_review(output_folder)
    return resolve_record_visibility(record, valid, source_classification=source_classification, published=published)


def is_recipe_hidden(output_folder: str | Path) -> bool:
    return resolve_recipe_visibility(output_folder)["hidden"]


def archive_review(output_folder: str | Path, record: dict | None, *, event: str) -> None:
    folder = Path(output_folder).resolve()
    if record is None:
        target = folder / AI_REVIEW_FILE_NAME
        if not target.exists():
            return
        original = target.read_bytes()
        record = {"_unparsed_record": True, "raw_base64": base64.b64encode(original).decode("ascii"),
                  "raw_text": original.decode("utf-8", errors="replace")}
    root, identity = review_location(folder)
    runtime = root / ".ai-review"
    history = runtime / "history"
    bucket = history / hashlib.sha256(identity.encode()).hexdigest()[:20]
    if any(path.is_symlink() for path in (runtime, history, bucket)):
        raise ValueError("审核历史目录不能是符号链接")
    filename = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex[:8] + ".json"
    atomic_write_json(bucket / filename, {"event": event, "archived_at": _now(), "folder": identity, "review": record}, backup=False)


def list_review_history(output_folder: str | Path) -> list[dict]:
    """Return readable snapshots newest first; never restore or alter any input."""
    folder = Path(output_folder).resolve()
    root, identity = review_location(folder)
    runtime = root / ".ai-review"
    history = runtime / "history"
    bucket = history / hashlib.sha256(identity.encode()).hexdigest()[:20]
    if any(path.is_symlink() for path in (runtime, history, bucket)):
        raise ValueError("审核历史目录不能是符号链接")
    records = []
    for path in bucket.glob("*.json"):
        if path.is_symlink():
            continue
        try:
            value = read_json_cached(path)
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("folder") in {identity, folder.name} and isinstance(value.get("review"), dict):
            records.append({**value, "history_id": path.stem})
    return sorted(records, key=lambda item: item.get("archived_at", ""), reverse=True)


def _manual_record(folder: Path) -> dict:
    if not (folder / "recipe.json").is_file():
        raise ValueError("菜谱不存在")
    if (folder / AI_REVIEW_FILE_NAME).is_symlink():
        raise ValueError("审核记录不能是符号链接")
    return load_review_record(folder) or {"schema_version": 2}


def set_ai_review_visibility(output_folder: str | Path, visibility: str | None) -> dict:
    if visibility not in (None, "visible", "hidden"):
        raise ValueError("展示状态必须是 visible、hidden 或 None")
    folder = Path(output_folder)
    path = folder / AI_REVIEW_FILE_NAME
    with file_lock(path):
        record = _manual_record(folder)
        archive_review(folder, load_review_record(folder), event="manual_visibility")
        record.update(visibility_override=visibility, override_at=_now())
        atomic_write_json(path, record, backup=False)
    return record


def set_ai_review_classification(output_folder: str | Path, classification: str | None, *, comment: str = "") -> dict:
    if classification not in (None, "recipe", "non_recipe", "technique", "uncertain"):
        raise ValueError("人工分类无效")
    folder = Path(output_folder)
    path = folder / AI_REVIEW_FILE_NAME
    with file_lock(path):
        record = _manual_record(folder)
        archive_review(folder, load_review_record(folder), event="manual_classification")
        record.update(classification_override=classification, classification_override_at=_now(), classification_override_comment=str(comment))
        atomic_write_json(path, record, backup=False)
    return record


def set_ai_review_item_decision(output_folder: str | Path, item_id: str, verdict: str, *, comment: str = "",
                                evidence: list[dict] | None = None, expected_content_fingerprint: str | None = None) -> dict:
    if verdict not in ("supported", "needs_review") or not isinstance(item_id, str):
        raise ValueError("人工逐项决定无效")
    if evidence is not None and not _valid_evidence(evidence):
        raise ValueError("人工证据必须是带 text 和可选 start 的数组")
    if verdict == "supported" and not evidence and not str(comment).strip():
        raise ValueError("没有字幕引用时，请说明人工确认依据，例如对照视频核实")
    folder = Path(output_folder)
    path = folder / AI_REVIEW_FILE_NAME
    with file_lock(path):
        record = _manual_record(folder)
        inputs = read_review_inputs(folder)
        fingerprint = fingerprint_review_content(inputs)
        if expected_content_fingerprint is not None and expected_content_fingerprint != fingerprint:
            raise ValueError("显示后菜谱内容已变更，请刷新后重新确认该项")
        expected = {f"{section}:{index}" for section in _SECTIONS for index, _ in enumerate(inputs["recipe"].get(section, []))}
        if item_id not in expected:
            raise ValueError("人工审核项不属于当前菜谱")
        if evidence:
            for quote in evidence:
                matches = [segment for segment in inputs["transcript"] if quote["text"].strip() and quote["text"] in segment["text"]]
                if not matches:
                    raise ValueError("人工引用无法在当前字幕中核实")
                if "start" in quote and not any(type(segment.get("start", segment.get("start_time"))) in (int, float)
                    and abs(segment.get("start", segment.get("start_time")) - quote["start"]) <= 1 for segment in matches):
                    raise ValueError("人工引用时间与字幕不符")
        archive_review(folder, load_review_record(folder), event="manual_item")
        manual = record.get("manual_review")
        if not isinstance(manual, dict) or manual.get("content_fingerprint") != fingerprint:
            manual = {"content_fingerprint": fingerprint, "items": {}}
        manual.setdefault("items", {})[item_id] = {"verdict": verdict, "comment": str(comment), "evidence": evidence or [], "updated_at": _now()}
        manual["completed_at"] = None
        record["manual_review"] = manual
        if review_content_fingerprint(folder) != fingerprint:
            raise ValueError("审核期间内容已变更，请刷新后重试")
        atomic_write_json(path, record, backup=False)
    return record


def complete_manual_ai_review(output_folder: str | Path, *, expected_content_fingerprint: str | None = None) -> dict:
    folder = Path(output_folder)
    path = folder / AI_REVIEW_FILE_NAME
    with file_lock(path):
        record = _manual_record(folder)
        state = get_review_state(folder)
        classification = state["classification"]
        inputs = read_review_inputs(folder)
        fingerprint = fingerprint_review_content(inputs)
        if expected_content_fingerprint is not None and expected_content_fingerprint != fingerprint:
            raise ValueError("显示后菜谱内容已变更，请刷新后重新确认审核")
        expected = {f"{section}:{index}" for section in _SECTIONS for index, _ in enumerate(inputs["recipe"].get(section, []))}
        if classification not in ("recipe", "non_recipe", "technique"):
            raise ValueError("请先确认内容分类，待确认分类不能完成审核")
        decisions = state["item_decisions"]
        if classification == "recipe" and (not expected or set(decisions) != expected or any(item["verdict"] != "supported" for item in decisions.values())):
            raise ValueError("请逐项确认所有食材、调料和步骤；待确认项不能标记完成")
        if state["content_fingerprint"] != fingerprint:
            raise ValueError("审核期间内容已变更")
        archive_review(folder, load_review_record(folder), event="manual_complete")
        if record.get("classification_override") is None:
            record.update(classification_override=classification, classification_override_at=_now(),
                          classification_override_comment="人工完成审核时确认分类")
        record["manual_review"] = {"content_fingerprint": fingerprint, "items": decisions,
                                   "completed_at": _now(), "classification": classification}
        if review_content_fingerprint(folder) != fingerprint:
            raise ValueError("审核期间内容已变更")
        atomic_write_json(path, record, backup=False)
    return record
