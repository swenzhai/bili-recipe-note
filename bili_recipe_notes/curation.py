from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, atomic_write_text, file_lock


DEFAULT_CURATION_REVIEW_DIR = "curation-review"
CURATION_DECISIONS_FILE = "curation-decisions.json"
CURATION_REVIEW_FILE = "recipe-review.json"
CURATION_DECISION_VALUES = {
    "pending",
    "keep_primary",
    "keep_variant",
    "merge_clip",
    "exclude",
    "review",
}

_PROMOTION_PATTERN = re.compile(
    r"调料包|料包|酱汁包|酱料包|新品上线|产品上线|甩手掌柜|不用自己熬|不用熬的|"
    r"一袋[^，。！!]{0,12}(?:酱|汁|料)"
)
_PROMOTION_MATERIAL_PATTERN = re.compile(r"调料包|酱汁调味包|酱料包|预制包|成品酱汁")
_SHOWCASE_PATTERN = re.compile(
    r"探店|餐厅|后厨|宴现场|现场批改|在哪里还能吃到|开业|发布会|对决|"
    r"(?:^|[^a-z])(?:vs|pk)(?:[^a-z]|$)",
    flags=re.IGNORECASE,
)
_TECHNIQUE_PATTERN = re.compile(r"技巧|判断|方法|泡发|处理|切法|刀工|炒糖色|油温|和面|发面")
_TITLE_NORMALIZE_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_TRANSCRIPT_NORMALIZE_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


@dataclass(frozen=True)
class CurationReviewResult:
    json_path: Path
    csv_path: Path
    duplicate_name_groups: int
    similar_name_pairs: int
    review_item_count: int
    primary_candidate_count: int


def _review_directory(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    return path.parent if path.suffix.lower() == ".json" else path


def load_curation_review(value: str | Path) -> dict[str, Any]:
    directory = _review_directory(value)
    payload = _read_object(directory / CURATION_REVIEW_FILE)
    if not payload or payload.get("schema_version") != 1 or not isinstance(payload.get("groups"), list):
        raise ValueError(f"审核报告不存在或格式无效：{directory / CURATION_REVIEW_FILE}")
    return payload


def load_curation_decisions(value: str | Path) -> dict[str, Any]:
    directory = _review_directory(value)
    path = directory / CURATION_DECISIONS_FILE
    if not path.exists():
        return {"schema_version": 1, "updated_at": None, "items": {}}
    payload = _read_object(path)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("items"), dict):
        raise ValueError(f"审核决定文件格式无效：{path}")
    return payload


def save_curation_decisions(
    value: str | Path,
    updates: list[dict[str, Any]],
) -> Path:
    directory = _review_directory(value)
    path = directory / CURATION_DECISIONS_FILE
    directory.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        payload = load_curation_decisions(directory)
        items = payload["items"]
        now = datetime.now(timezone.utc).isoformat()
        for update in updates:
            item_id = str(update.get("item_id") or "").strip()
            decision = str(update.get("decision") or "pending").strip()
            if not item_id:
                raise ValueError("审核决定缺少 item_id")
            if decision not in CURATION_DECISION_VALUES:
                raise ValueError(f"不支持的整理决定：{decision}")
            items[item_id] = {
                "decision": decision,
                "final_title": str(update.get("final_title") or "").strip(),
                "variant_name": str(update.get("variant_name") or "").strip(),
                "review_notes": str(update.get("review_notes") or "").strip(),
                "updated_at": now,
            }
        payload["updated_at"] = now
        atomic_write_json(path, payload)
    return path


def save_curation_decision(
    value: str | Path,
    item_id: str,
    *,
    decision: str,
    final_title: str = "",
    variant_name: str = "",
    review_notes: str = "",
) -> Path:
    return save_curation_decisions(
        value,
        [
            {
                "item_id": item_id,
                "decision": decision,
                "final_title": final_title,
                "variant_name": variant_name,
                "review_notes": review_notes,
            }
        ],
    )


def suggested_curation_decision(role: str) -> str:
    return {
        "primary_candidate": "keep_primary",
        "variant_candidate": "keep_variant",
        "short_clip_candidate": "merge_clip",
        "exclude_candidate": "exclude",
        "name_review_candidate": "review",
    }.get(str(role), "review")


def curation_decision_conflicts(
    review: dict[str, Any],
    decisions: dict[str, Any],
) -> list[str]:
    report_items = {
        str(item.get("item_id") or ""): item
        for group in review.get("groups", [])
        if isinstance(group, dict)
        for item in group.get("items", [])
        if isinstance(item, dict) and str(item.get("item_id") or "").strip()
    }
    primaries: dict[str, list[str]] = defaultdict(list)
    for item_id, saved in decisions.get("items", {}).items():
        if not isinstance(saved, dict) or saved.get("decision") != "keep_primary":
            continue
        item = report_items.get(str(item_id), {})
        final_title = str(saved.get("final_title") or item.get("group_title") or "").strip()
        if final_title:
            primaries[final_title].append(str(item_id))
    return [
        f"最终菜名“{title}”存在 {len(item_ids)} 个主版本：{'、'.join(item_ids)}"
        for title, item_ids in sorted(primaries.items())
        if len(item_ids) > 1
    ]


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_transcript(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(value, list):
        return ""
    text = "".join(
        str(segment.get("text") or "")
        for segment in value
        if isinstance(segment, dict)
    )
    return _TRANSCRIPT_NORMALIZE_PATTERN.sub("", text.casefold())


def _title_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _TITLE_NORMALIZE_PATTERN.sub("", normalized)


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    left_index = 0
    right_index = 0
    skipped = False
    while left_index < len(left) and right_index < len(right):
        if left[left_index] == right[right_index]:
            left_index += 1
            right_index += 1
            continue
        if skipped:
            return False
        skipped = True
        right_index += 1
    return True


def _similar_title_pairs(titles: list[str]) -> list[dict[str, Any]]:
    keyed = [(title, _title_key(title)) for title in titles]
    pairs: list[dict[str, Any]] = []
    for index, (left_title, left_key) in enumerate(keyed):
        if len(left_key) < 4:
            continue
        for right_title, right_key in keyed[index + 1 :]:
            if len(right_key) < 4 or not _edit_distance_at_most_one(left_key, right_key):
                continue
            pairs.append(
                {
                    "title": left_title,
                    "candidate_title": right_title,
                    "reason": "规范名仅相差一个字，需人工确认是否为别名、错字或不同变体",
                }
            )
    return pairs


def _shingles(text: str, width: int = 5) -> set[str]:
    if len(text) < width:
        return {text} if text else set()
    return {text[index : index + width] for index in range(len(text) - width + 1)}


def _transcript_containment(left: str, right: str) -> float:
    left_grams = _shingles(left)
    right_grams = _shingles(right)
    smaller = min(len(left_grams), len(right_grams))
    if not smaller:
        return 0.0
    return len(left_grams & right_grams) / smaller


def _bvid(folder: Path, *documents: dict[str, Any]) -> str:
    for document in documents:
        value = str(document.get("bvid") or "").strip()
        if value:
            return value
    match = re.search(r"--(BV[0-9A-Za-z]+)", folder.name, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _material_names(recipe: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for field in ("ingredients", "seasonings"):
        values = recipe.get(field)
        if not isinstance(values, list):
            continue
        names.extend(
            str(item.get("name") or "").strip()
            for item in values
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
    return names


def _step_evidence_ratio(recipe: dict[str, Any]) -> float:
    steps = recipe.get("steps") if isinstance(recipe.get("steps"), list) else []
    if not steps:
        return 0.0
    supported = sum(
        bool(str(step.get("evidence") or "").strip())
        for step in steps
        if isinstance(step, dict)
    )
    return supported / len(steps)


def _load_recipe_item(folder: Path) -> dict[str, Any] | None:
    recipe = _read_object(folder / "recipe.json")
    if not recipe or not isinstance(recipe.get("steps"), list) or not recipe["steps"]:
        return None
    source = _read_object(folder / "source.json")
    job = _read_object(folder / "job.json")
    quality = _read_object(folder / "quality.json")
    title = str(recipe.get("title") or job.get("title") or "").strip()
    if not title:
        return None
    video_title = str(
        recipe.get("video_title") or source.get("video_title") or job.get("video_title") or ""
    ).strip()
    materials = _material_names(recipe)
    steps = [step for step in recipe["steps"] if isinstance(step, dict)]
    duration = source.get("duration") or job.get("duration") or 0
    try:
        duration_seconds = round(float(duration), 3)
    except (TypeError, ValueError):
        duration_seconds = 0.0
    try:
        quality_score = int(quality.get("score")) if quality.get("score") is not None else None
    except (TypeError, ValueError):
        quality_score = None
    promotion = bool(
        _PROMOTION_PATTERN.search(video_title)
        or (
            duration_seconds <= 180
            and _PROMOTION_MATERIAL_PATTERN.search(" ".join(materials))
        )
    )
    showcase = bool(_SHOWCASE_PATTERN.search(video_title))
    technique = bool(_TECHNIQUE_PATTERN.search(title))
    return {
        "title": title,
        "bvid": _bvid(folder, recipe, source, job),
        "source_url": str(recipe.get("source_url") or source.get("source_url") or job.get("source_url") or ""),
        "video_title": video_title,
        "uploader": str(recipe.get("uploader") or source.get("uploader") or job.get("uploader") or ""),
        "duration_seconds": duration_seconds,
        "step_count": len(steps),
        "ingredient_count": len(materials),
        "quality_score": quality_score,
        "evidence_ratio": round(_step_evidence_ratio(recipe), 3),
        "output_folder": str(folder),
        "transcript": _read_transcript(folder / "transcript.json"),
        "promotion_signal": promotion,
        "showcase_signal": showcase,
        "technique_signal": technique,
    }


def _score_item(item: dict[str, Any]) -> int:
    score = int(item.get("quality_score") or 0)
    score += min(int(item["step_count"]), 10) * 3
    score += min(int(item["ingredient_count"]), 20)
    score += round(float(item["evidence_ratio"]) * 10)
    duration = float(item["duration_seconds"])
    if duration >= 180:
        score += 20
    elif duration >= 90:
        score += 10
    elif duration < 45:
        score -= 10
    if item["promotion_signal"]:
        score -= 60
    if item["showcase_signal"]:
        score -= 15
    if item.get("short_clip_signal"):
        score -= 30
    return score


def _apply_overlap_signals(items: list[dict[str, Any]]) -> None:
    for item in items:
        item["transcript_overlap"] = 0.0
        item["related_bvid"] = ""
        item["short_clip_signal"] = False
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            overlap = _transcript_containment(left["transcript"], right["transcript"])
            if overlap <= 0:
                continue
            for current, related in ((left, right), (right, left)):
                if overlap > current["transcript_overlap"]:
                    current["transcript_overlap"] = round(overlap, 3)
                    current["related_bvid"] = related["bvid"]
            left_duration = float(left["duration_seconds"])
            right_duration = float(right["duration_seconds"])
            if overlap < 0.2 or not left_duration or not right_duration:
                continue
            shorter, longer = (left, right) if left_duration < right_duration else (right, left)
            if shorter["duration_seconds"] <= longer["duration_seconds"] * 0.5:
                shorter["short_clip_signal"] = True


def _content_type(item: dict[str, Any]) -> str:
    if item["promotion_signal"]:
        return "promotion"
    if item["technique_signal"]:
        return "technique"
    if item["showcase_signal"]:
        return "showcase"
    return "recipe"


def _reasons(
    item: dict[str, Any],
    is_primary: bool,
    similar_titles: set[str],
) -> list[str]:
    reasons: list[str] = []
    if similar_titles:
        reasons.append(f"规范名与 {'、'.join(sorted(similar_titles))} 仅差一个字，需确认别名、错字或变体")
    if is_primary:
        reasons.append("同名组综合完整度最高，建议作为主版本起点")
    if item["promotion_signal"]:
        reasons.append("标题或用料出现调料包、成品酱汁等推广信号")
    if item["showcase_signal"]:
        reasons.append("标题包含探店、后厨或宴会展示信号")
    if item["short_clip_signal"]:
        reasons.append(f"字幕与较长来源 {item['related_bvid']} 明显重合，疑似短剪")
    if float(item["duration_seconds"]) < 90 and not item["short_clip_signal"]:
        reasons.append("视频不足 90 秒，需确认步骤是否完整")
    if int(item["step_count"]) < 4:
        reasons.append("有效步骤少于 4 步")
    if not reasons:
        reasons.append("与同名来源做法可能不同，建议保留为变体候选")
    return reasons


def _suggested_role(item: dict[str, Any], is_primary: bool, has_duplicates: bool) -> str:
    if item["promotion_signal"]:
        return "exclude_candidate"
    if item["short_clip_signal"]:
        return "short_clip_candidate"
    if not has_duplicates:
        return "name_review_candidate"
    if is_primary:
        return "primary_candidate"
    return "variant_candidate"


def build_curation_review(out_dir: str | Path, destination: str | Path) -> CurationReviewResult:
    root = Path(out_dir).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    items: list[dict[str, Any]] = []
    if root.is_dir():
        items = [
            item
            for folder in sorted(path for path in root.iterdir() if path.is_dir())
            if (item := _load_recipe_item(folder)) is not None
        ]
    by_title: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_title[item["title"]].append(item)

    similar_pairs = _similar_title_pairs(sorted(by_title))
    similar_by_title: dict[str, set[str]] = defaultdict(set)
    for pair in similar_pairs:
        similar_by_title[pair["title"]].add(pair["candidate_title"])
        similar_by_title[pair["candidate_title"]].add(pair["title"])

    duplicate_groups = {title: values for title, values in by_title.items() if len(values) > 1}
    review_titles = set(duplicate_groups) | set(similar_by_title)
    rows: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    primary_candidate_count = 0
    for title in sorted(review_titles, key=lambda value: (-len(by_title[value]), value)):
        title_items = by_title[title]
        title_similar_names = similar_by_title.get(title, set())
        has_duplicates = len(title_items) > 1
        _apply_overlap_signals(title_items)
        for item in title_items:
            item["recommendation_score"] = _score_item(item)
        eligible = [
            item
            for item in title_items
            if not item["promotion_signal"] and not item["short_clip_signal"]
        ] or title_items
        primary = max(
            eligible,
            key=lambda item: (item["recommendation_score"], item["duration_seconds"], item["bvid"]),
        )
        group_rows: list[dict[str, Any]] = []
        for item in sorted(
            title_items,
            key=lambda value: (-value["recommendation_score"], value["bvid"]),
        ):
            is_primary = item is primary and has_duplicates
            role = _suggested_role(item, is_primary, has_duplicates)
            if role == "primary_candidate":
                primary_candidate_count += 1
            row = {
                "item_id": Path(item["output_folder"]).name,
                "group_title": title,
                "group_size": len(title_items),
                "similar_titles": " | ".join(sorted(title_similar_names)),
                "suggested_role": role,
                "suggested_content_type": _content_type(item),
                "recommendation_score": item["recommendation_score"],
                "review_reasons": "；".join(_reasons(item, is_primary, title_similar_names)),
                "bvid": item["bvid"],
                "video_title": item["video_title"],
                "duration_seconds": item["duration_seconds"],
                "step_count": item["step_count"],
                "ingredient_count": item["ingredient_count"],
                "quality_score": item["quality_score"],
                "evidence_ratio": item["evidence_ratio"],
                "transcript_overlap": item["transcript_overlap"],
                "related_bvid": item["related_bvid"],
                "source_url": item["source_url"],
                "output_folder": item["output_folder"],
                "decision": "",
                "final_title": "",
                "variant_name": "",
                "review_notes": "",
            }
            rows.append(row)
            group_rows.append(row)
        groups.append(
            {
                "title": title,
                "item_count": len(group_rows),
                "similar_titles": sorted(title_similar_names),
                "items": group_rows,
            }
        )

    fieldnames = list(rows[0]) if rows else [
        "item_id",
        "group_title",
        "group_size",
        "similar_titles",
        "suggested_role",
        "suggested_content_type",
        "recommendation_score",
        "review_reasons",
        "bvid",
        "video_title",
        "duration_seconds",
        "step_count",
        "ingredient_count",
        "quality_score",
        "evidence_ratio",
        "transcript_overlap",
        "related_bvid",
        "source_url",
        "output_folder",
        "decision",
        "final_title",
        "variant_name",
        "review_notes",
    ]
    csv_buffer = StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    target.mkdir(parents=True, exist_ok=True)
    csv_path = target / "recipe-review.csv"
    json_path = target / "recipe-review.json"
    atomic_write_text(csv_path, "\ufeff" + csv_buffer.getvalue(), backup=False)
    atomic_write_json(
        json_path,
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_output_dir": str(root),
            "instructions": {
                "decision": "填写 keep_primary、keep_variant、exclude、merge_clip 或 review",
                "final_title": "确认后的规范菜名；近似名未确认前不要直接合并",
                "variant_name": "不同做法可填写传统版、家常版、简化版等",
                "review_notes": "记录取舍理由或仍需核对的时间点",
            },
            "stats": {
                "complete_recipe_count": len(items),
                "duplicate_name_groups": len(duplicate_groups),
                "duplicate_name_items": sum(len(values) for values in duplicate_groups.values()),
                "similar_name_pairs": len(similar_pairs),
                "review_item_count": len(rows),
                "primary_candidate_count": primary_candidate_count,
            },
            "similar_name_candidates": similar_pairs,
            "groups": groups,
        },
        backup=False,
    )
    return CurationReviewResult(
        json_path=json_path,
        csv_path=csv_path,
        duplicate_name_groups=len(duplicate_groups),
        similar_name_pairs=len(similar_pairs),
        review_item_count=len(rows),
        primary_candidate_count=primary_candidate_count,
    )
