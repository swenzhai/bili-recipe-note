from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .recipe_extractor import Recipe
from .storage import atomic_write_json, read_json

REVIEW_FILE_NAME = "recipe.review.json"
REVIEW_SECTIONS = ("ingredients", "seasonings", "steps")
VALID_DECISIONS = {"pending", "accepted", "edited", "skipped"}


def _dump_model(model: Any) -> dict[str, Any]:
    return model.model_dump() if hasattr(model, "model_dump") else dict(model.__dict__)


def review_path(output_folder: str | Path) -> Path:
    return Path(output_folder) / REVIEW_FILE_NAME


def create_recipe_review(recipe: Recipe, output_folder: str | Path) -> Path:
    base = _dump_model(recipe)
    items: list[dict[str, Any]] = []
    for section in REVIEW_SECTIONS:
        for index, value in enumerate(base.get(section) or []):
            items.append(
                {
                    "id": f"{section}:{index}",
                    "section": section,
                    "index": index,
                    "decision": "pending",
                    "original": deepcopy(value),
                    "value": deepcopy(value),
                    "comment": "",
                }
            )
    payload = {
        "version": 1,
        "status": "pending" if items else "ready",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_recipe": base,
        "items": items,
    }
    path = review_path(output_folder)
    atomic_write_json(path, payload)
    return path


def load_recipe_review(output_folder: str | Path) -> dict[str, Any]:
    payload = read_json(review_path(output_folder), expected_type=dict)
    if payload.get("version") != 1 or not isinstance(payload.get("base_recipe"), dict):
        raise ValueError("不支持或已损坏的审核文件")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("审核文件缺少 items")
    return payload


def decide_review_item(
    output_folder: str | Path,
    item_id: str,
    decision: str,
    *,
    value: dict[str, Any] | None = None,
    comment: str = "",
) -> dict[str, Any]:
    if decision not in VALID_DECISIONS - {"pending"}:
        raise ValueError(f"无效审核决定：{decision}")
    payload = load_recipe_review(output_folder)
    item = next((candidate for candidate in payload["items"] if candidate.get("id") == item_id), None)
    if item is None:
        raise KeyError(f"未找到审核项：{item_id}")
    if value is not None:
        if not isinstance(value, dict):
            raise ValueError("审核项修改内容必须是 JSON 对象")
        item["value"] = value
    elif decision == "accepted":
        item["value"] = deepcopy(item["original"])
    item["decision"] = decision
    item["comment"] = comment.strip()
    pending = sum(candidate.get("decision") == "pending" for candidate in payload["items"])
    payload["status"] = "ready" if pending == 0 else "pending"
    atomic_write_json(review_path(output_folder), payload)
    return payload


def accept_all_pending_review_items(output_folder: str | Path) -> dict[str, Any]:
    payload = load_recipe_review(output_folder)
    for item in payload["items"]:
        if item.get("decision") == "pending":
            item["decision"] = "accepted"
            item["value"] = deepcopy(item["original"])
    payload["status"] = "ready"
    atomic_write_json(review_path(output_folder), payload)
    return payload


def recipe_from_completed_review(output_folder: str | Path) -> Recipe:
    payload = load_recipe_review(output_folder)
    pending = [item for item in payload["items"] if item.get("decision") == "pending"]
    if pending:
        raise ValueError(f"还有 {len(pending)} 个审核项未处理")
    recipe_data = deepcopy(payload["base_recipe"])
    for section in REVIEW_SECTIONS:
        section_items = [item for item in payload["items"] if item.get("section") == section]
        recipe_data[section] = [
            deepcopy(item.get("value") or item.get("original"))
            for item in section_items
            if item.get("decision") != "skipped"
        ]
    if not recipe_data.get("steps"):
        raise ValueError("审核结果不能跳过所有烹饪步骤")
    recipe = Recipe.model_validate(recipe_data) if hasattr(Recipe, "model_validate") else Recipe(**recipe_data)
    payload["status"] = "applied"
    payload["applied_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(review_path(output_folder), payload)
    return recipe
