from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.request import Request, urlopen
from pathlib import Path, PurePosixPath


IMAGE_LINK_RE = re.compile(r"(!\[[^\]]*\]\()([^)]+)(\))")
SECOND_LEVEL_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
MARKDOWN_FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(?P<body>.*?)(?:\n)?```\s*$", re.DOTALL | re.IGNORECASE)
MARKDOWN_FILE_OUTPUT_RE = re.compile(r"(已输出到|已写入|saved to|written to).+\.md", re.IGNORECASE)
_LAST_LLM_ERROR: str | None = None


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


def clean_llm_markdown_output(markdown: str) -> str:
    markdown = markdown.strip()
    match = MARKDOWN_FENCE_RE.match(markdown)
    if match:
        markdown = match.group("body").strip()
    return normalize_markdown_image_paths(markdown)


def _read_generated_markdown_file(folder: Path) -> str | None:
    files = sorted(folder.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files:
        try:
            content = clean_llm_markdown_output(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if content:
            return content
    return None


def _clean_opencode_output(stdout: str, work_dir: Path) -> str:
    summary = clean_llm_markdown_output(stdout)
    generated_file_summary = _read_generated_markdown_file(work_dir)
    if summary and MARKDOWN_FILE_OUTPUT_RE.search(summary):
        return generated_file_summary or ""
    if summary:
        return summary
    if generated_file_summary:
        return generated_file_summary
    return ""


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


def clear_last_llm_error() -> None:
    global _LAST_LLM_ERROR
    _LAST_LLM_ERROR = None


def get_last_llm_error() -> str | None:
    return _LAST_LLM_ERROR


def _set_last_llm_error(provider: str, message: str) -> None:
    global _LAST_LLM_ERROR
    _LAST_LLM_ERROR = f"{provider}: {message}"


def _tail(text: str | bytes | None, limit: int = 800) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def _format_process_failure(result: subprocess.CompletedProcess) -> str:
    chunks = [f"exit code {result.returncode}"]
    stderr = _tail(result.stderr)
    stdout = _tail(result.stdout)
    if stderr:
        chunks.append(f"stderr: {stderr}")
    if stdout:
        chunks.append(f"stdout: {stdout}")
    return "; ".join(chunks)


def _format_timeout(exc: subprocess.TimeoutExpired) -> str:
    chunks = [f"timed out after {exc.timeout:g}s"]
    stdout = _tail(exc.stdout)
    stderr = _tail(exc.stderr)
    if stderr:
        chunks.append(f"stderr: {stderr}")
    if stdout:
        chunks.append(f"stdout: {stdout}")
    return "; ".join(chunks)


def _format_exception(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return _format_timeout(exc)
    if isinstance(exc, subprocess.CalledProcessError):
        return _format_process_failure(exc)
    return f"{type(exc).__name__}: {exc}"


def summarize_note(
    markdown_note: str,
    provider: str = "opencode",
    openai_model: str = "gpt-5.5",
    local_llm_command: str | None = None,
    codex_model: str | None = None,
    codex_profile: str | None = None,
) -> str | None:
    clear_last_llm_error()
    if provider == "none":
        return None
    if provider == "codex":
        return summarize_note_with_codex_cli(markdown_note, model=codex_model, profile=codex_profile)
    if provider == "openai":
        return summarize_note_with_openai(markdown_note, model=openai_model)
    if provider == "local":
        return summarize_note_with_local_command(markdown_note, local_llm_command)
    return summarize_note_with_opencode(markdown_note)


def complete_markdown_prompt(
    prompt: str,
    provider: str = "opencode",
    openai_model: str = "gpt-5.5",
    local_llm_command: str | None = None,
    codex_model: str | None = None,
    codex_profile: str | None = None,
) -> str | None:
    clear_last_llm_error()
    if provider == "none":
        _set_last_llm_error("none", "LLM provider is disabled")
        return None
    if provider == "codex":
        return _complete_prompt_with_codex_cli(prompt, model=codex_model, profile=codex_profile)
    if provider == "openai":
        return _complete_prompt_with_openai(prompt, model=openai_model)
    if provider == "local":
        return _complete_prompt_with_local_command(prompt, local_llm_command)
    return _complete_prompt_with_opencode(prompt)


def _windows_launcher_command(command: str) -> str:
    if os.name == "nt":
        for name in (f"{command}.cmd", f"{command}.exe", f"{command}.bat", command):
            path = shutil.which(name)
            if path:
                return path
    return shutil.which(command) or command


def _opencode_command() -> str:
    return _windows_launcher_command("opencode")


def summarize_note_with_opencode(markdown_note: str) -> str | None:
    """Rewrite note into one final markdown recipe with fixed sections.

    Returns None when opencode is unavailable or fails.
    """
    prompt = (
        "你是一个只负责重写 Markdown 菜谱笔记的文本处理器。"
        "不要读取文件，不要写入文件，不要执行命令，只把最终 Markdown 文档输出到 stdout。\n\n"
        f"{build_summary_prompt(markdown_note)}"
    )
    return _complete_prompt_with_opencode(prompt)


def _complete_prompt_with_opencode(prompt: str) -> str | None:
    clear_last_llm_error()
    try:
        with tempfile.TemporaryDirectory(prefix="bili-recipe-opencode-") as work_dir_name:
            work_dir = Path(work_dir_name)
            result = subprocess.run(
                [_opencode_command(), "run", "--dir", str(work_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                input=prompt,
                timeout=300,
                cwd=work_dir,
            )
            summary = _clean_opencode_output(result.stdout or "", work_dir)
    except Exception as exc:
        _set_last_llm_error("opencode", _format_exception(exc))
        return None

    if result.returncode != 0:
        _set_last_llm_error("opencode", _format_process_failure(result))
        return None

    if not summary:
        stderr = _tail(result.stderr)
        detail = "empty output"
        if stderr:
            detail += f"; stderr: {stderr}"
        _set_last_llm_error("opencode", detail)
        return None
    return summary or None


def summarize_note_with_local_command(markdown_note: str, command: str | None) -> str | None:
    return _complete_prompt_with_local_command(build_summary_prompt(markdown_note), command)


def _complete_prompt_with_local_command(prompt: str, command: str | None) -> str | None:
    clear_last_llm_error()
    if not command:
        _set_last_llm_error("local", "local command is empty")
        return None
    try:
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=prompt,
            timeout=300,
        )
    except Exception as exc:
        _set_last_llm_error("local", _format_exception(exc))
        return None
    if result.returncode != 0:
        _set_last_llm_error("local", _format_process_failure(result))
        return None
    summary = clean_llm_markdown_output(result.stdout or "")
    if not summary:
        _set_last_llm_error("local", "empty output")
        return None
    return summary or None


def _codex_command() -> str:
    return _windows_launcher_command("codex")


def summarize_note_with_codex_cli(
    markdown_note: str,
    model: str | None = None,
    profile: str | None = None,
    timeout: int = 300,
) -> str | None:
    prompt = (
        "你是一个只负责重写 Markdown 菜谱笔记的文本处理器。"
        "不要读取文件，不要执行命令，不要解释过程，只输出最终 Markdown 文档。\n\n"
        f"{build_summary_prompt(markdown_note)}"
    )
    return _complete_prompt_with_codex_cli(prompt, model=model, profile=profile, timeout=timeout)


def _complete_prompt_with_codex_cli(
    prompt: str,
    model: str | None = None,
    profile: str | None = None,
    timeout: int = 300,
) -> str | None:
    clear_last_llm_error()
    output_file = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False)
    output_path = output_file.name
    output_file.close()

    cmd = [
        _codex_command(),
        "exec",
        "-c",
        'service_tier="flex"',
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--output-last-message",
        output_path,
    ]
    if model:
        cmd.extend(["--model", model])
    if profile:
        cmd.extend(["--profile", profile])
    cmd.append("-")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=prompt,
            timeout=timeout,
        )
        if result.returncode != 0:
            _set_last_llm_error("codex", _format_process_failure(result))
            return None
        with open(output_path, encoding="utf-8") as file:
            summary = clean_llm_markdown_output(file.read())
        if not summary:
            detail = "empty output file"
            stderr = _tail(result.stderr)
            if stderr:
                detail += f"; stderr: {stderr}"
            _set_last_llm_error("codex", detail)
            return None
        return summary or None
    except Exception as exc:
        _set_last_llm_error("codex", _format_exception(exc))
        return None
    finally:
        try:
            os.unlink(output_path)
        except OSError:
            pass


def summarize_note_with_openai(markdown_note: str, model: str = "gpt-5.5") -> str | None:
    return _complete_prompt_with_openai(build_summary_prompt(markdown_note), model=model)


def _complete_prompt_with_openai(prompt: str, model: str = "gpt-5.5") -> str | None:
    clear_last_llm_error()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        _set_last_llm_error("openai", "OPENAI_API_KEY is not set")
        return None
    payload = {
        "model": model,
        "input": prompt,
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
    except Exception as exc:
        _set_last_llm_error("openai", _format_exception(exc))
        return None

    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return clean_llm_markdown_output(text)

    chunks: list[str] = []
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    summary = "\n".join(chunks).strip()
    if not summary:
        _set_last_llm_error("openai", "empty response")
        return None
    return clean_llm_markdown_output(summary)
