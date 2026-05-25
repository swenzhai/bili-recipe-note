from __future__ import annotations

import subprocess


def summarize_note_with_opencode(markdown_note: str) -> str | None:
    """Summarize generated note with opencode CLI.

    Returns None when opencode is unavailable or fails.
    """
    prompt = (
        "请基于以下菜谱笔记，输出简洁、可执行的归纳总结。"
        "要求：\n"
        "1) 先给3-5条核心要点；\n"
        "2) 再给按顺序的关键步骤提醒；\n"
        "3) 最后给常见翻车点与规避建议；\n"
        "4) 使用中文，避免杜撰。\n\n"
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
    except Exception:
        return None

    summary = (result.stdout or "").strip()
    return summary or None
