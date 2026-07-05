from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from typing import Any
from urllib.request import Request, urlopen
from pathlib import PurePosixPath


IMAGE_LINK_RE = re.compile(r"(!\[[^\]]*\]\()([^)]+)(\))")
SECOND_LEVEL_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def normalize_markdown_image_paths(markdown: str) -> str:
    """Normalize image links to `images/<name>` to keep note.md portable."""

    def _replace(match: re.Match[str]) -> str:
        prefix, raw_path, suffix = match.groups()
        path = raw_path.strip()
        if not path:
            return match.group(0)
        if "://" in path:
            return match.group(0)
        image_name = PurePosixPath(path).name
        if not image_name:
            return match.group(0)
        return f"{prefix}images/{image_name}{suffix}"

    return IMAGE_LINK_RE.sub(_replace, markdown)


def markdown_has_image_links(markdown: str) -> bool:
    return bool(IMAGE_LINK_RE.search(markdown))


def extract_markdown_image_links(markdown: str) -> list[str]:
    links: list[str] = []
    for match in IMAGE_LINK_RE.finditer(markdown):
        links.append(match.group(0))
    return links


def append_missing_image_links(markdown: str, required_links: list[str]) -> str:
    if not required_links:
        return markdown
    existing = set(extract_markdown_image_links(markdown))
    missing = [link for link in required_links if link not in existing]
    if not missing:
        return markdown

    merged = markdown.rstrip() + "\n\n## 步骤配图补全\n\n"
    merged += "\n".join(missing)
    return merged.rstrip() + "\n"


def _extract_second_level_section(markdown: str, heading: str) -> str | None:
    matches = list(SECOND_LEVEL_HEADING_RE.finditer(markdown))
    for idx, match in enumerate(matches):
        if match.group(1).strip() != heading:
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        section = markdown[start:end].strip()
        return section or None
    return None


def _clean_placeholder_lines(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped in {"- 无", "- 未识别", "无", "未说明"}:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def build_recipe_summary_section(source_markdown: str) -> str:
    existing_summary = _extract_second_level_section(source_markdown, "菜谱总结")
    if existing_summary:
        return "## 菜谱总结\n\n" + existing_summary.rstrip() + "\n"

    summary = _clean_placeholder_lines(_extract_second_level_section(source_markdown, "总结要点") or "")
    uncertain = _clean_placeholder_lines(_extract_second_level_section(source_markdown, "不确定信息") or "")

    lines = ["## 菜谱总结", ""]
    if summary:
        lines.append("### 要点")
        lines.append("")
        lines.append(summary)
        lines.append("")
    if uncertain:
        lines.append("### 需要确认")
        lines.append("")
        lines.append(uncertain)
        lines.append("")
    if not summary and not uncertain:
        lines.append("- 暂无额外总结。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def ensure_recipe_summary_section(markdown: str, source_markdown: str) -> str:
    if re.search(r"^##\s+菜谱总结\s*$", markdown, flags=re.MULTILINE):
        return markdown.rstrip() + "\n"
    return markdown.rstrip() + "\n\n" + build_recipe_summary_section(source_markdown)


def build_summary_prompt(markdown_note: str) -> str:
    return (
        "请基于以下菜谱笔记，输出一份最终版 Markdown 文档。"
        "要求：\n"
        "1) 只输出一份文档，不要给多份结果，不要额外解释；\n"
        "2) 严格按顺序包含这四部分：\n"
        "   - ## 配料信息（准备哪些材料）\n"
        "   - ## 备菜（如何备菜）\n"
        "   - ## 烹饪（如何烹饪）\n"
        "   - ## 菜谱总结（总结成败关键、注意点和仍需确认的信息）\n"
        "3) 菜谱总结必须基于原文的“菜谱总结”“总结要点”和“不确定信息”，不要省略；\n"
        "4) 保留原文里已有的步骤图片 Markdown（![](...)），并放在对应步骤下；\n"
        "5) 内容要简洁、可执行，使用中文，避免杜撰。\n\n"
        "菜谱笔记如下：\n"
        f"{markdown_note}"
    )


def summarize_note(
    markdown_note: str,
    provider: str = "opencode",
    openai_model: str = "gpt-5.5",
    local_llm_command: str | None = None,
) -> str | None:
    if provider == "none":
        return None
    if provider == "openai":
        return summarize_note_with_openai(markdown_note, model=openai_model)
    if provider == "local":
        return summarize_note_with_local_command(markdown_note, local_llm_command)
    return summarize_note_with_opencode(markdown_note)


def summarize_note_with_opencode(markdown_note: str) -> str | None:
    """Rewrite note into one final markdown recipe with fixed sections.

    Returns None when opencode is unavailable or fails.
    """
    prompt = build_summary_prompt(markdown_note)
    try:
        result = subprocess.run(
            ["opencode", "run", prompt],
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        # Windows can fail with [WinError 206] when command-line args are too long.
        if os.name != "nt" or getattr(exc, "winerror", None) != 206:
            return None
        try:
            result = subprocess.run(
                ["opencode", "run"],
                check=True,
                capture_output=True,
                text=True,
                input=prompt,
            )
        except Exception:
            return None
    except Exception:
        return None

    summary = normalize_markdown_image_paths((result.stdout or "").strip())
    return summary or None


def summarize_note_with_local_command(markdown_note: str, command: str | None) -> str | None:
    if not command:
        return None
    prompt = build_summary_prompt(markdown_note)
    try:
        result = subprocess.run(
            shlex.split(command),
            check=True,
            capture_output=True,
            text=True,
            input=prompt,
        )
    except Exception:
        return None
    summary = normalize_markdown_image_paths((result.stdout or "").strip())
    return summary or None


def summarize_note_with_openai(markdown_note: str, model: str = "gpt-5.5") -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    payload = {
        "model": model,
        "input": build_summary_prompt(markdown_note),
    }
    req = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as response:
            data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return normalize_markdown_image_paths(text.strip())

    chunks: list[str] = []
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    summary = "\n".join(chunks).strip()
    return normalize_markdown_image_paths(summary) if summary else None
