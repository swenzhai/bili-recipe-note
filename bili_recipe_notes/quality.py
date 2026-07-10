from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .recipe_extractor import Recipe
from .storage import atomic_write_json

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
    else:
        missing_amounts = [item.name for item in recipe.ingredients if not item.amount]
        if missing_amounts:
            score -= min(12, len(missing_amounts) * 3)
            _add_issue(
                issues,
                "warning",
                "missing_ingredient_amounts",
                f"{len(missing_amounts)} 项主料缺少用量。",
                "对照原视频确认用量；无法确认时明确标注为估计值。",
            )

    if not _meaningful_items(recipe.seasonings):
        score -= 10
        _add_issue(issues, "warning", "missing_seasonings", "未识别到有效调料。", "补充调料和大致用量。")
    else:
        missing_seasoning_amounts = [item.name for item in recipe.seasonings if not item.amount]
        if missing_seasoning_amounts:
            score -= min(8, len(missing_seasoning_amounts) * 2)
            _add_issue(
                issues,
                "info",
                "missing_seasoning_amounts",
                f"{len(missing_seasoning_amounts)} 项调料缺少用量。",
                "补充大致范围，避免只写“适量”却没有判断标准。",
            )

    if len(recipe.steps) < 2:
        score -= 15
        _add_issue(issues, "warning", "too_few_steps", "步骤数量偏少。", "检查 transcript，必要时手动拆分步骤。")
    elif len(recipe.steps) > 12:
        score -= 12
        _add_issue(
            issues,
            "warning",
            "too_many_steps",
            f"识别出 {len(recipe.steps)} 个步骤，可能把字幕片段误当成独立操作。",
            "合并连续动作，并删除寒暄、广告、试吃和闲聊片段。",
        )

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

    long_steps = [idx for idx, step in enumerate(recipe.steps, start=1) if len((step.action or "").strip()) > 500]
    if long_steps:
        score -= min(12, 4 * len(long_steps))
        _add_issue(
            issues,
            "warning",
            "overlong_steps",
            f"步骤 {', '.join(map(str, long_steps[:5]))} 包含过多字幕，可能混入非烹饪内容。",
            "按实际动作重新拆分，并对照对应时间段确认。",
        )

    unordered_steps = [
        idx
        for idx in range(1, len(recipe.steps))
        if recipe.steps[idx].start_time < recipe.steps[idx - 1].start_time
    ]
    if unordered_steps:
        score -= 10
        _add_issue(issues, "error", "unordered_steps", "步骤时间顺序不一致。", "按视频时间重新排序步骤。")

    low_confidence = [
        idx
        for idx, step in enumerate(recipe.steps, start=1)
        if step.confidence is not None and step.confidence < 0.6
    ]
    if low_confidence:
        score -= min(12, 3 * len(low_confidence))
        _add_issue(
            issues,
            "warning",
            "low_confidence_steps",
            f"步骤 {', '.join(map(str, low_confidence[:5]))} 置信度较低。",
            "点击时间戳对照原视频后再确认。",
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
        screenshot_count = sum(
            1
            for step in recipe.steps
            if step.screenshot_path and (folder / step.screenshot_path).is_file()
        )
        expected_screenshots = min(4, len(recipe.steps))
        if screenshot_count < expected_screenshots:
            score -= min(8, 2 * (expected_screenshots - screenshot_count))
            _add_issue(
                issues,
                "info",
                "missing_screenshots",
                f"关键步骤图片不足（{screenshot_count}/{expected_screenshots}）。",
                "开启截图或在编辑修复页为关键阶段重新截图。",
            )

    summary_heading = "## 关键点速查"
    if summary_heading not in note:
        score -= 12
        _add_issue(issues, "warning", "missing_summary", "note.md 缺少关键点速查。", "重新生成或一键优化笔记。")
    elif note.count(summary_heading) > 1:
        score -= 8
        _add_issue(issues, "warning", "duplicate_summary", "note.md 存在重复的关键点速查。", "只保留一份经过确认的速查摘要。")

    if recipe.source_url and recipe.source_url not in note:
        score -= 8
        _add_issue(issues, "warning", "missing_source_attribution", "note.md 未保留原视频链接。", "恢复来源 URL、视频标题和 UP 主。")

    meaningful_uncertain = [item for item in recipe.uncertain_points if item and item not in {"无", "未说明"}]
    if meaningful_uncertain:
        score -= min(10, 3 * len(meaningful_uncertain))
        _add_issue(issues, "info", "uncertain_points", "菜谱仍有不确定信息。", "人工确认不确定项后保存。")

    score = max(0, min(100, score))
    if score >= 85:
        summary = "结构完整度较高；关键用量、火候和食品安全仍需对照原视频确认。"
    elif score >= 65:
        summary = "结构基本完整，但建议先补充并核对关键信息。"
    else:
        summary = "结构完整度偏低，建议先审校再用于实际烹饪。"
    return QualityReport(score=score, issues=issues, summary=summary)


def quality_path(output_folder: str | Path) -> Path:
    return Path(output_folder) / QUALITY_FILE_NAME


def write_quality_report(output_folder: str | Path, report: QualityReport | None = None) -> Path:
    folder = Path(output_folder)
    result = report or analyze_recipe_quality(folder)
    path = quality_path(folder)
    atomic_write_json(path, asdict(result))
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
