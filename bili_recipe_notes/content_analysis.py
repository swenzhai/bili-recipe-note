from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm import complete_markdown_prompt, get_last_llm_error


DEFAULT_ANALYSIS_REQUEST = "提取视频中提到的通用烹饪技巧"


@dataclass
class ContentAnalysisOptions:
    llm_provider: str = "opencode"
    openai_model: str = "gpt-5.5"
    local_llm_command: str | None = None
    codex_model: str | None = None
    codex_profile: str | None = None
    output_filename: str = "extra_analysis.md"


@dataclass
class ContentAnalysisResult:
    output_folder: Path
    analysis_path: Path
    markdown: str
    request: str


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _transcript_to_text(path: Path) -> str:
    if not path.exists():
        return ""
    raw = _read_json(path)
    if not isinstance(raw, list):
        return ""
    lines: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = item.get("start")
        end = item.get("end")
        if isinstance(start, (int, float)):
            if isinstance(end, (int, float)):
                lines.append(f"[{start:.1f}-{end:.1f}] {text}")
            else:
                lines.append(f"[{start:.1f}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def _recipe_to_context(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        recipe = _read_json(path)
    except Exception:
        return ""
    return json.dumps(recipe, ensure_ascii=False, indent=2)


def _safe_markdown_filename(filename: str) -> str:
    name = Path(filename.strip() or "extra_analysis.md").name
    if not name.lower().endswith(".md"):
        name += ".md"
    return name


def build_content_analysis_prompt(output_folder: str | Path, request: str) -> str:
    folder = Path(output_folder)
    note = _read_text(folder / "note.md")
    transcript = _transcript_to_text(folder / "transcript.json")
    recipe = _recipe_to_context(folder / "recipe.json")
    cleaned_request = request.strip() or DEFAULT_ANALYSIS_REQUEST

    return (
        "请基于一个 B 站做菜视频的已有资料做二次分析。"
        "你只能使用下面提供的 transcript、菜谱结构和 note.md，不要杜撰视频中没有的信息。"
        "如果某个点只是推断，请明确标注“推断”。"
        "只输出 Markdown，不要输出解释过程。\n\n"
        f"用户想额外获得的内容：{cleaned_request}\n\n"
        "输出要求：\n"
        "1) 用一个一级标题概括本次分析主题；\n"
        "2) 优先提取可复用、可执行的信息；\n"
        "3) 对通用技巧类内容，按“技巧 / 适用场景 / 视频依据 / 注意点”组织；\n"
        "4) 如果资料不足，列出“未能确认的信息”；\n"
        "5) 内容使用中文，简洁但不要漏掉关键细节。\n\n"
        "=== note.md ===\n"
        f"{note or '无'}\n\n"
        "=== recipe.json ===\n"
        f"{recipe or '无'}\n\n"
        "=== transcript.json 文本 ===\n"
        f"{transcript or '无'}\n"
    )


def analyze_video_content(
    output_folder: str | Path,
    request: str = DEFAULT_ANALYSIS_REQUEST,
    options: ContentAnalysisOptions | None = None,
) -> ContentAnalysisResult:
    folder = Path(output_folder)
    if not folder.exists():
        raise FileNotFoundError(f"Output folder not found: {folder}")
    if not (folder / "note.md").exists() and not (folder / "transcript.json").exists() and not (folder / "recipe.json").exists():
        raise FileNotFoundError(f"No note, transcript, or recipe found in: {folder}")

    opts = options or ContentAnalysisOptions()
    cleaned_request = request.strip() or DEFAULT_ANALYSIS_REQUEST
    prompt = build_content_analysis_prompt(folder, cleaned_request)
    markdown = complete_markdown_prompt(
        prompt,
        provider=opts.llm_provider,
        openai_model=opts.openai_model,
        local_llm_command=opts.local_llm_command,
        codex_model=opts.codex_model,
        codex_profile=opts.codex_profile,
    )
    if not markdown:
        detail = get_last_llm_error()
        message = f"LLM content analysis failed: {opts.llm_provider}"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message)

    analysis_path = folder / _safe_markdown_filename(opts.output_filename)
    analysis_path.write_text(markdown, encoding="utf-8")
    metadata_path = analysis_path.with_suffix(".json")
    metadata_path.write_text(
        json.dumps(
            {
                "request": cleaned_request,
                "llm_provider": opts.llm_provider,
                "analysis_path": str(analysis_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ContentAnalysisResult(
        output_folder=folder,
        analysis_path=analysis_path,
        markdown=markdown,
        request=cleaned_request,
    )
