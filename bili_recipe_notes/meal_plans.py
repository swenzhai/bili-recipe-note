from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .config import CONFIG_DIR_NAME
from .storage import CorruptDataError, atomic_write_json, file_lock, read_json

MEAL_PLANS_SCHEMA_VERSION = 1
MEAL_PLANS_FILE_NAME = "meal-plans.json"


@dataclass(frozen=True)
class MealPlanItem:
    recipe_id: str
    title: str
    servings_multiplier: float = 1.0
    note: str = ""


@dataclass
class MealPlan:
    id: str
    name: str
    guest_count: int
    child_count: int
    occasion: str
    notes: str
    items: list[MealPlanItem]
    created_at: str
    updated_at: str
    practice_records: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class MealCandidate:
    recipe_id: str
    title: str
    category: str = "未分类"
    cuisine: str = "未分类"
    tags: tuple[str, ...] = ()
    quality_score: int | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def meal_plans_path(project_root: str | Path | None = None) -> Path:
    root = Path(project_root) if project_root else Path.cwd()
    return root / CONFIG_DIR_NAME / MEAL_PLANS_FILE_NAME


def _parse_item(value: Any) -> MealPlanItem:
    if not isinstance(value, dict):
        raise TypeError("套餐菜品必须是对象")
    recipe_id = str(value.get("recipe_id") or "").strip()
    title = str(value.get("title") or "").strip()
    multiplier = float(value.get("servings_multiplier") or 1.0)
    if not recipe_id or not title:
        raise ValueError("套餐菜品缺少 recipe_id 或 title")
    if not 0.25 <= multiplier <= 10:
        raise ValueError("套餐菜品份量倍率必须在 0.25–10 之间")
    return MealPlanItem(
        recipe_id=recipe_id,
        title=title,
        servings_multiplier=multiplier,
        note=str(value.get("note") or "").strip(),
    )


def _parse_plan(value: Any) -> MealPlan:
    if not isinstance(value, dict):
        raise TypeError("套餐必须是对象")
    guest_count = int(value.get("guest_count") or 0)
    child_count = int(value.get("child_count") or 0)
    if not 1 <= guest_count <= 50:
        raise ValueError("用餐人数必须在 1–50 之间")
    if not 0 <= child_count <= guest_count:
        raise ValueError("儿童人数不能超过用餐人数")
    raw_practices = value.get("practice_records") or []
    if not isinstance(raw_practices, list) or not all(isinstance(item, dict) for item in raw_practices):
        raise TypeError("practice_records 必须是对象数组")
    plan_id = str(value.get("id") or "").strip()
    name = str(value.get("name") or "").strip()
    if not plan_id or not name:
        raise ValueError("套餐缺少 id 或 name")
    return MealPlan(
        id=plan_id,
        name=name,
        guest_count=guest_count,
        child_count=child_count,
        occasion=str(value.get("occasion") or "日常家宴").strip(),
        notes=str(value.get("notes") or "").strip(),
        items=[_parse_item(item) for item in value.get("items") or []],
        created_at=str(value.get("created_at") or ""),
        updated_at=str(value.get("updated_at") or ""),
        practice_records=[dict(item) for item in raw_practices],
    )


def load_meal_plans(project_root: str | Path | None = None) -> list[MealPlan]:
    path = meal_plans_path(project_root)
    if not path.is_file():
        return []
    raw = read_json(path, expected_type=dict)
    if raw.get("schema_version") != MEAL_PLANS_SCHEMA_VERSION:
        raise CorruptDataError(f"不支持的套餐库版本：{raw.get('schema_version')!r}")
    plans = raw.get("plans") or []
    if not isinstance(plans, list):
        raise CorruptDataError("套餐库 plans 必须是数组")
    try:
        return [_parse_plan(value) for value in plans]
    except (TypeError, ValueError) as exc:
        raise CorruptDataError(f"套餐库内容无效：{exc}") from exc


def _write_plans(path: Path, plans: list[MealPlan]) -> Path:
    return atomic_write_json(
        path,
        {
            "schema_version": MEAL_PLANS_SCHEMA_VERSION,
            "updated_at": _now(),
            "plans": [asdict(plan) for plan in plans],
        },
    )


def save_meal_plan(
    *,
    name: str,
    guest_count: int,
    child_count: int,
    occasion: str,
    notes: str,
    items: Iterable[MealPlanItem],
    plan_id: str | None = None,
    project_root: str | Path | None = None,
) -> MealPlan:
    cleaned_name = name.strip()
    supplied_items = list(items)
    cleaned_items = list(dict.fromkeys(item.recipe_id for item in supplied_items))
    item_by_id = {item.recipe_id: item for item in supplied_items}
    normalized_items = [item_by_id[recipe_id] for recipe_id in cleaned_items]
    if not cleaned_name:
        raise ValueError("请填写套餐名称")
    if not normalized_items:
        raise ValueError("套餐至少需要一道菜")
    if not 1 <= guest_count <= 50:
        raise ValueError("用餐人数必须在 1–50 之间")
    if not 0 <= child_count <= guest_count:
        raise ValueError("儿童人数不能超过用餐人数")

    path = meal_plans_path(project_root)
    with file_lock(path):
        plans = load_meal_plans(project_root)
        existing = next((plan for plan in plans if plan.id == plan_id), None)
        stamp = _now()
        saved = MealPlan(
            id=existing.id if existing else (plan_id or uuid4().hex),
            name=cleaned_name,
            guest_count=guest_count,
            child_count=child_count,
            occasion=occasion.strip() or "日常家宴",
            notes=notes.strip(),
            items=normalized_items,
            created_at=existing.created_at if existing else stamp,
            updated_at=stamp,
            practice_records=list(existing.practice_records) if existing else [],
        )
        if existing:
            plans[plans.index(existing)] = saved
        else:
            plans.insert(0, saved)
        _write_plans(path, plans)
    return saved


def record_meal_plan_practice(
    plan_id: str,
    *,
    rating: int,
    notes: str,
    practiced_on: str | None = None,
    project_root: str | Path | None = None,
) -> MealPlan:
    if rating not in {1, 2, 3, 4, 5}:
        raise ValueError("实践评分必须在 1–5 星之间")
    path = meal_plans_path(project_root)
    with file_lock(path):
        plans = load_meal_plans(project_root)
        plan = next((item for item in plans if item.id == plan_id), None)
        if plan is None:
            raise KeyError(f"找不到套餐：{plan_id}")
        stamp = _now()
        plan.practice_records.append(
            {
                "id": uuid4().hex,
                "practiced_on": practiced_on or datetime.now().date().isoformat(),
                "rating": rating,
                "notes": notes.strip(),
                "created_at": stamp,
            }
        )
        plan.updated_at = stamp
        _write_plans(path, plans)
    return plan


def delete_meal_plan(plan_id: str, project_root: str | Path | None = None) -> bool:
    path = meal_plans_path(project_root)
    with file_lock(path):
        plans = load_meal_plans(project_root)
        retained = [plan for plan in plans if plan.id != plan_id]
        if len(retained) == len(plans):
            return False
        _write_plans(path, retained)
    return True


SOUP_KEYWORDS = ("汤", "羹", "炖盅")
STAPLE_KEYWORDS = ("饭", "粥", "面", "米粉", "河粉", "粉丝", "饺", "包", "馒头", "饼")
VEGETABLE_KEYWORDS = ("时蔬", "青菜", "菜心", "芥兰", "西兰花", "茄子", "冬瓜", "南瓜", "豆腐")
DESSERT_KEYWORDS = ("糕", "甜", "布丁", "糖水", "沙翁", "蛋挞", "面包")
SPICY_KEYWORDS = ("辣", "麻", "剁椒", "水煮", "香辣", "酸辣")
ALCOHOL_KEYWORDS = ("酒", "醉", "花雕", "啤酒")
KID_FRIENDLY_KEYWORDS = ("蒸", "炖", "蛋", "豆腐", "汤", "粥", "鸡", "虾", "南瓜")


def meal_candidate_kind(candidate: MealCandidate) -> str:
    title_and_category = " ".join((candidate.title, candidate.category))
    searchable = " ".join((title_and_category, *candidate.tags))
    if candidate.category == "汤羹" or any(keyword in title_and_category for keyword in SOUP_KEYWORDS):
        return "soup"
    if candidate.category in {"糕点", "小吃"} or any(
        keyword in title_and_category for keyword in DESSERT_KEYWORDS
    ):
        return "dessert"
    if candidate.category == "主食" or any(keyword in candidate.title for keyword in STAPLE_KEYWORDS):
        return "staple"
    if any(keyword in searchable for keyword in VEGETABLE_KEYWORDS) or "素菜" in candidate.tags:
        return "vegetable"
    return "main"


def _candidate_score(candidate: MealCandidate, *, child_count: int, occasion: str) -> float:
    text = " ".join((candidate.title, candidate.category, candidate.cuisine, *candidate.tags))
    score = float(candidate.quality_score or 0) / 10
    if child_count or occasion == "带小孩":
        score += sum(8 for keyword in KID_FRIENDLY_KEYWORDS if keyword in text)
        score -= sum(35 for keyword in (*SPICY_KEYWORDS, *ALCOHOL_KEYWORDS) if keyword in text)
        score -= sum(10 for keyword in ("炸", "冰镇", "生食") if keyword in text)
    if occasion == "清淡家宴":
        score += sum(5 for keyword in ("蒸", "炖", "汤", "白灼", "清炒") if keyword in text)
        score -= sum(20 for keyword in ("炸", "辣", "麻", "油") if keyword in text)
    return score


def recommend_meal_recipe_ids(
    candidates: Iterable[MealCandidate],
    *,
    guest_count: int,
    child_count: int = 0,
    occasion: str = "日常家宴",
) -> list[str]:
    if not 1 <= guest_count <= 50:
        raise ValueError("用餐人数必须在 1–50 之间")
    if not 0 <= child_count <= guest_count:
        raise ValueError("儿童人数不能超过用餐人数")
    unique_by_id = {candidate.recipe_id: candidate for candidate in candidates}
    ranked = sorted(
        unique_by_id.values(),
        key=lambda candidate: (
            -_candidate_score(candidate, child_count=child_count, occasion=occasion),
            candidate.title,
            candidate.recipe_id,
        ),
    )
    ordered: list[MealCandidate] = []
    seen_titles: set[str] = set()
    for candidate in ranked:
        normalized_title = "".join(candidate.title.split()).casefold()
        if normalized_title in seen_titles:
            continue
        seen_titles.add(normalized_title)
        ordered.append(candidate)
    target_count = min(10, max(3, guest_count + 1))
    pools = {
        kind: [candidate for candidate in ordered if meal_candidate_kind(candidate) == kind]
        for kind in ("main", "vegetable", "soup", "staple", "dessert")
    }
    selected: list[MealCandidate] = []

    def take(kind: str, count: int) -> None:
        for candidate in pools[kind]:
            if len(selected) >= target_count or count <= 0:
                break
            if candidate not in selected:
                selected.append(candidate)
                count -= 1

    vegetable_target = 1 if target_count <= 5 else 2
    reserved = (
        (vegetable_target if pools["vegetable"] else 0)
        + (1 if pools["soup"] else 0)
        + (1 if pools["staple"] else 0)
    )
    if child_count and pools["dessert"] and target_count >= 5:
        reserved += 1
    take("main", max(1, target_count - reserved))
    take("vegetable", vegetable_target)
    take("soup", 1)
    take("staple", 1)
    if child_count and target_count >= 5:
        take("dessert", 1)
    for candidate in ordered:
        if len(selected) >= target_count:
            break
        if candidate not in selected:
            selected.append(candidate)
    return [candidate.recipe_id for candidate in selected]
