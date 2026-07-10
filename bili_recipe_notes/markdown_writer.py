from __future__ import annotations

from .recipe_extractor import Recipe
from .utils import sec_to_timestamp


def _timestamp_link(source_url: str, seconds: float) -> str:
    label = sec_to_timestamp(seconds)
    if not source_url:
        return label
    separator = "&" if "?" in source_url else "?"
    return f"[{label}]({source_url}{separator}t={max(0, int(seconds))})"


def _ingredient_line(item) -> str:
    line = f"- {item.name}：{item.amount or '用量待确认'}"
    if item.note and item.note != "未说明":
        line += f"（{item.note}）"
    return line


def render_markdown(recipe: Recipe) -> str:
    lines: list[str] = [f"# {recipe.title}", "", "## 来源", ""]
    if recipe.source_url:
        lines.append(f"- 原视频：[{recipe.source_url}]({recipe.source_url})")
    if recipe.video_title:
        lines.append(f"- 视频标题：{recipe.video_title}")
    if recipe.uploader:
        lines.append(f"- UP主：{recipe.uploader}")
    lines.append("")

    meta_lines = []
    if recipe.servings:
        meta_lines.append(f"- 份量：{recipe.servings}")
    if recipe.total_time:
        meta_lines.append(f"- 总耗时：{recipe.total_time}")
    if recipe.difficulty:
        meta_lines.append(f"- 难度：{recipe.difficulty}")
    if meta_lines:
        lines.extend(["## 基本信息", "", *meta_lines, ""])

    lines.extend(["## 配料信息", "", "### 主料", ""])
    if recipe.ingredients:
        for item in recipe.ingredients:
            lines.append(_ingredient_line(item))
    else:
        lines.append("- 未可靠识别，请人工确认")

    lines.extend(["", "### 调料", ""])
    if recipe.seasonings:
        for item in recipe.seasonings:
            lines.append(_ingredient_line(item))
    else:
        lines.append("- 未可靠识别，请人工确认")

    lines.extend(["", "### 工具", ""])
    if recipe.tools:
        for tool in recipe.tools:
            lines.append(f"- {tool}")
    else:
        lines.append("- 未可靠识别")

    if recipe.shopping_list:
        lines.extend(["", "## 购物清单", ""])
        lines.extend([f"- {item}" for item in recipe.shopping_list])

    lines.extend(["", "## 备菜", ""])
    if recipe.prep_items:
        lines.extend([f"- {item}" for item in recipe.prep_items])
    else:
        lines.append("- 未单独识别，请结合烹饪步骤确认")

    lines.extend(["", "## 烹饪", ""])
    for idx, step in enumerate(recipe.steps, start=1):
        lines.append(f"### {idx}. {step.title}")
        lines.append("")
        start = _timestamp_link(recipe.source_url, step.start_time)
        end = sec_to_timestamp(step.end_time) if step.end_time is not None else ""
        lines.append(f"时间：{start}{f'–{end}' if end else ''}")
        lines.append("")
        if step.screenshot_path:
            lines.append(f"![步骤 {idx}：{step.title}]({step.screenshot_path})")
            lines.append("")
        lines.append(f"操作：{step.action}")
        lines.append("")
        if step.heat:
            lines.append(f"- 火候：{step.heat}")
        if step.duration:
            lines.append(f"- 时长：{step.duration}")
        if step.tips:
            lines.append(f"- 注意：{step.tips}")
        lines.append("")

    lines.extend(["## 关键点速查", ""])
    if recipe.summary_tips:
        lines.extend([f"- {tip}" for tip in recipe.summary_tips])
    else:
        lines.append("- 无")

    return "\n".join(lines).strip() + "\n"
