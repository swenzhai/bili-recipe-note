from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .recipe_extractor import Recipe

QUALITY_FILE_NAME = "quality.json"


@dataclass
class QualityIssue:
    severity: str
    code: str
    message: str
    suggestion: str


@dataclass
class QualityReport:
    score: int
    issues: list[QualityIssue]
    summary: str


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _model_validate_recipe(data: dict[str, Any]) -> Recipe | None:
    if not data:
        return None
    try:
        if hasattr(Recipe, "model_validate"):
            return Recipe.model_validate(data)
        return Recipe(**data)
    except Exception:
        return None


def _meaningful_items(items: list[Any]) -> list[Any]:
    meaningful = []
    for item in items:
        name = getattr(item, "name", None)
        if name is None and isinstance(item, dict):
            name = item.get("name")
        if name in {"未识别", "", None}:
            continue
        meaningful.append(item)
    return meaningful


def _add_issue(issues: list[QualityIssue], severity: str, code: str, message: str, suggestion: str) -> None:
    issues.append(QualityIssue(severity=severity, code=code, message=message, suggestion=suggestion))


def analyze_recipe_quality(output_folder: str | Path) -> QualityReport:
    folder = Path(output_folder)
    recipe = _model_validate_recipe(_read_json(folder / "recipe.json"))
    note = (folder / "note.md").read_text(encoding="utf-8") if (folder / "note.md").exists() else ""
    issues: list[QualityIssue] = []
    score = 100

    if not recipe:
        return QualityReport(
            score=0,
            issues=[
                QualityIssue(
                    severity="error",
                    code="missing_recipe",
                    message="缺少可读取的 recipe.json。",
                    suggestion="重新生成菜谱，或在编辑修复页补齐 recipe.json。",
                )
            ],
            summary="缺少菜谱结构，无法评估。",
        )

    if not _meaningful_items(recipe.ingredients):
        score -= 18
        _add_issue(issues, "error", "missing_ingredients", "未识别到有效食材。", "补充食材名称和用量。")

    if not _meaningful_items(recipe.seasonings):
        score -= 10
        _add_issue(issues, "warning", "missing_seasonings", "未识别到有效调料。", "补充调料和大致用量。")

    if len(recipe.steps) < 2:
        score -= 15
        _add_issue(issues, "warning", "too_few_steps", "步骤数量偏少。", "检查 transcript，必要时手动拆分步骤。")

    short_steps = [idx for idx, step in enumerate(recipe.steps, start=1) if len((step.action or "").strip()) < 8]
    if short_steps:
        score -= min(12, 4 * len(short_steps))
        _add_issue(
            issues,
            "warning",
            "short_steps",
            f"步骤 {', '.join(map(str, short_steps[:5]))} 操作描述偏短。",
            "在编辑修复页补充关键动作和判断标准。",
        )

    missing_heat = [idx for idx, step in enumerate(recipe.steps, start=1) if not step.heat]
    if missing_heat and recipe.steps:
        score -= min(8, 2 * len(missing_heat))
        _add_issue(issues, "info", "missing_heat", "部分步骤缺少火候信息。", "补充大火/中火/小火等火候。")

    missing_duration = [idx for idx, step in enumerate(recipe.steps, start=1) if not step.duration]
    if missing_duration and recipe.steps:
        score -= min(8, 2 * len(missing_duration))
        _add_issue(issues, "info", "missing_duration", "部分步骤缺少时长信息。", "补充大致烹饪或等待时间。")

    if recipe.steps:
        screenshot_count = sum(1 for step in recipe.steps if step.screenshot_path)
        if screenshot_count < len(recipe.steps):
            score -= min(10, 2 * (len(recipe.steps) - screenshot_count))
            _add_issue(
                issues,
                "info",
                "missing_screenshots",
                "部分步骤缺少截图。",
                "开启截图或在编辑修复页对关键步骤重新截图。",
            )

    if "## 菜谱总结" not in note:
        score -= 12
        _add_issue(issues, "warning", "missing_summary", "note.md 缺少菜谱总结。", "重新生成或一键优化笔记。")

    meaningful_uncertain = [item for item in recipe.uncertain_points if item and item not in {"无", "未说明"}]
    if meaningful_uncertain:
        score -= min(10, 3 * len(meaningful_uncertain))
        _add_issue(issues, "info", "uncertain_points", "菜谱仍有不确定信息。", "人工确认不确定项后保存。")

    score = max(0, min(100, score))
    if score >= 85:
        summary = "质量较好，可以直接使用。"
    elif score >= 65:
        summary = "质量可用，但建议补充关键信息。"
    else:
        summary = "质量偏低，建议先编辑修复或一键优化。"
    return QualityReport(score=score, issues=issues, summary=summary)


def quality_path(output_folder: str | Path) -> Path:
    return Path(output_folder) / QUALITY_FILE_NAME


def write_quality_report(output_folder: str | Path, report: QualityReport | None = None) -> Path:
    folder = Path(output_folder)
    result = report or analyze_recipe_quality(folder)
    path = quality_path(folder)
    path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_quality_report(output_folder: str | Path) -> QualityReport | None:
    path = quality_path(output_folder)
    raw = _read_json(path)
    if not raw:
        return None
    try:
        return QualityReport(
            score=int(raw.get("score", 0)),
            summary=str(raw.get("summary", "")),
            issues=[QualityIssue(**issue) for issue in raw.get("issues", []) if isinstance(issue, dict)],
        )
    except Exception:
        return None
