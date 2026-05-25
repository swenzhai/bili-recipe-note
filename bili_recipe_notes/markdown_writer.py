from __future__ import annotations

from .recipe_extractor import Recipe
from .utils import sec_to_timestamp


def render_markdown(recipe: Recipe) -> str:
    lines: list[str] = [f"# {recipe.title}", "", f"原视频：{recipe.source_url}", f"视频标题：{recipe.video_title or ''}", f"UP主：{recipe.uploader or ''}", ""]

    lines.extend(["## 食材", ""])
    if recipe.ingredients:
        for item in recipe.ingredients:
            lines.append(f"- {item.name}：{item.amount or '未说明'}")
    else:
        lines.append("- 未识别")

    lines.extend(["", "## 调料", ""])
    if recipe.seasonings:
        for item in recipe.seasonings:
            lines.append(f"- {item.name}：{item.amount or '未说明'}")
    else:
        lines.append("- 未识别")

    lines.extend(["", "## 工具", ""])
    for tool in recipe.tools:
        lines.append(f"- {tool}")

    lines.extend(["", "## 步骤", ""])
    for idx, step in enumerate(recipe.steps, start=1):
        lines.append(f"### {idx}. {step.title}")
        lines.append("")
        lines.append(f"时间：{sec_to_timestamp(step.start_time)}")
        lines.append("")
        if step.screenshot_path:
            lines.append(f"![]({step.screenshot_path})")
            lines.append("")
        lines.append("操作：")
        lines.append(step.action)
        lines.append("")
        lines.append(f"火候：{step.heat or '未说明'}")
        lines.append("")
        lines.append(f"时长：{step.duration or '未说明'}")
        lines.append("")
        lines.append(f"注意：{step.tips or '未说明'}")
        lines.append("")

    lines.extend(["## 总结要点", ""])
    if recipe.summary_tips:
        lines.extend([f"- {tip}" for tip in recipe.summary_tips])
    else:
        lines.append("- 无")

    lines.extend(["", "## 不确定信息", ""])
    if recipe.uncertain_points:
        lines.extend([f"- {item}" for item in recipe.uncertain_points])
    else:
        lines.append("- 无")

    return "\n".join(lines).strip() + "\n"
