from __future__ import annotations

import os
import re
import subprocess
from pathlib import PurePosixPath


IMAGE_LINK_RE = re.compile(r"(!\[[^\]]*\]\()([^)]+)(\))")


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


def summarize_note_with_opencode(markdown_note: str) -> str | None:
    """Rewrite note into one final markdown recipe with fixed sections.

    Returns None when opencode is unavailable or fails.
    """
    prompt = (
        "请基于以下菜谱笔记，输出一份最终版 Markdown 文档。"
        "要求：\n"
        "1) 只输出一份文档，不要给多份结果，不要额外解释；\n"
        "2) 严格按顺序包含这三部分：\n"
        "   - ## 配料信息（准备哪些材料）\n"
        "   - ## 备菜（如何备菜）\n"
        "   - ## 烹饪（如何烹饪）\n"
        "3) 保留原文里已有的步骤图片 Markdown（![](...)），并放在对应步骤下；\n"
        "4) 内容要简洁、可执行，使用中文，避免杜撰。\n\n"
        "菜谱笔记如下：\n"
        f"{markdown_note}"
    )
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
