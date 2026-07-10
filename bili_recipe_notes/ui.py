from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

try:
    from .batch_queue import create_batch_id, list_batch_states
    from .config import UIConfig, load_config, save_config
    from .content_analysis import ContentAnalysisOptions, analyze_video_content
    from .environment import run_environment_checks
    from .exports import export_note
    from .history import HistoryItem, scan_history
    from .knowledge_base import (
        KnowledgeExtractionOptions,
        add_practice_record,
        delete_knowledge_entry,
        due_review_entries,
        export_knowledge_base,
        extract_knowledge_from_folders,
        extract_knowledge_from_video,
        knowledge_base_path,
        load_knowledge_entries,
        merge_knowledge_entries,
        record_knowledge_review,
        related_knowledge_for_recipe,
        search_knowledge_entries,
        suggest_duplicate_groups,
        update_knowledge_entry,
        write_related_knowledge_to_note,
    )
    from .llm import apply_cli_extra_instructions
    from .optimizer import OptimizeOptions, optimize_existing_note
    from .obsidian_archive import (
        ObsidianArchiveConflict,
        archive_knowledge,
        archive_recipe,
        archive_recipe_batch,
    )
    from .pipeline import (
        BatchJobOptions,
        RecipeJobOptions,
        extract_creator_links,
        generate_recipe_note,
        recapture_step_screenshot,
        regenerate_note_from_recipe,
        regenerate_recipe_from_transcript,
        run_batch,
    )
    from .quality import analyze_recipe_quality
    from .recipe_extractor import RECIPE_CATEGORIES, RECIPE_CUISINES, Recipe, condense_recipe_steps
    from .recipe_review import (
        accept_all_pending_review_items,
        create_recipe_review,
        decide_review_item,
        load_recipe_review,
        recipe_from_completed_review,
        review_path,
    )
    from .storage import atomic_write_json, atomic_write_text
except ImportError:  # pragma: no cover - supports direct streamlit script execution
    from bili_recipe_notes.batch_queue import create_batch_id, list_batch_states
    from bili_recipe_notes.config import UIConfig, load_config, save_config
    from bili_recipe_notes.content_analysis import ContentAnalysisOptions, analyze_video_content
    from bili_recipe_notes.environment import run_environment_checks
    from bili_recipe_notes.exports import export_note
    from bili_recipe_notes.history import HistoryItem, scan_history
    from bili_recipe_notes.knowledge_base import (
        KnowledgeExtractionOptions,
        add_practice_record,
        delete_knowledge_entry,
        due_review_entries,
        export_knowledge_base,
        extract_knowledge_from_folders,
        extract_knowledge_from_video,
        knowledge_base_path,
        load_knowledge_entries,
        merge_knowledge_entries,
        record_knowledge_review,
        related_knowledge_for_recipe,
        search_knowledge_entries,
        suggest_duplicate_groups,
        update_knowledge_entry,
        write_related_knowledge_to_note,
    )
    from bili_recipe_notes.llm import apply_cli_extra_instructions
    from bili_recipe_notes.optimizer import OptimizeOptions, optimize_existing_note
    from bili_recipe_notes.obsidian_archive import (
        ObsidianArchiveConflict,
        archive_knowledge,
        archive_recipe,
        archive_recipe_batch,
    )
    from bili_recipe_notes.pipeline import (
        BatchJobOptions,
        RecipeJobOptions,
        extract_creator_links,
        generate_recipe_note,
        recapture_step_screenshot,
        regenerate_note_from_recipe,
        regenerate_recipe_from_transcript,
        run_batch,
    )
    from bili_recipe_notes.quality import analyze_recipe_quality
    from bili_recipe_notes.recipe_extractor import RECIPE_CATEGORIES, RECIPE_CUISINES, Recipe, condense_recipe_steps
    from bili_recipe_notes.recipe_review import (
        accept_all_pending_review_items,
        create_recipe_review,
        decide_review_item,
        load_recipe_review,
        recipe_from_completed_review,
        review_path,
    )
    from bili_recipe_notes.storage import atomic_write_json, atomic_write_text


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MARKDOWN_IMAGE_LINE_RE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$")
LLM_PROVIDERS = ["opencode", "codex", "openai", "local", "none"]
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
CLI_PROMPT_PRESETS = {
    "空白 / 自定义": "",
    "严格证据模式": "所有结论都要能在字幕中找到依据；无法确认就明确写入不确定项，不要根据常识补齐。",
    "家庭实用模式": "优先整理成普通家庭厨房可执行的步骤，突出用量、火候、时长和成功判断标准，语言简洁。",
    "专业厨房模式": "使用专业但易懂的烹饪术语，重点提取工艺原理、温度控制、质地判断和容易失败的节点。",
}
PAGES = [
    "单视频生成",
    "草稿与归档",
    "审核确认",
    "批量处理",
    "编辑修复",
    "知识库",
    "二次分析",
    "环境检查",
    "UP 主链接",
]
EXPORT_KINDS = ["obsidian", "pdf", "docx", "zip"]
EXPORT_MIME_TYPES = {
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip",
}
INGREDIENT_COLUMNS = ("name", "amount", "note")
STEP_COLUMNS = (
    "title",
    "start_time",
    "end_time",
    "action",
    "heat",
    "duration",
    "tips",
    "screenshot_path",
)
FLASH_STATE_KEY = "_ui_flash_message"


def _optional_text(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _clean_error(exc: Exception) -> str:
    return ANSI_RE.sub("", str(exc)).strip()


def _render_paths(paths: list[Path]) -> str:
    return "\n".join(str(path) for path in paths)


def _local_markdown_image(base_dir: Path, raw_path: str) -> Path | None:
    cleaned = unquote(raw_path.strip().strip("<>").replace("\\", "/"))
    if not cleaned or "://" in cleaned:
        return None
    # Markdown allows an optional quoted title after the path. Generated notes do
    # not need it, but ignoring it here keeps legacy notes renderable.
    cleaned = re.sub(r'\s+["\'].*["\']\s*$', "", cleaned).strip()
    root = base_dir.resolve()
    candidate = (root / cleaned).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _render_note_preview(st, markdown: str, base_dir: Path) -> None:
    """Render Markdown while resolving portable image paths against a note folder."""

    block: list[str] = []

    def _flush() -> None:
        if block:
            st.markdown("\n".join(block))
            block.clear()

    for line in markdown.splitlines():
        match = MARKDOWN_IMAGE_LINE_RE.match(line)
        image_path = _local_markdown_image(base_dir, match.group(2)) if match else None
        if match and image_path is not None:
            _flush()
            if image_path.is_file():
                st.image(
                    str(image_path),
                    caption=match.group(1).strip() or image_path.stem,
                    width=360,
                )
            else:
                st.warning(f"图片不存在：{match.group(2)}")
            continue
        block.append(line)
    _flush()


def _read_text(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _recipe_to_data(recipe_path: Path) -> dict:
    data = json.loads(recipe_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("recipe.json 顶层必须是 JSON 对象")
    return data


def _safe_recipe_to_data(recipe_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return _recipe_to_data(recipe_path), None
    except Exception as exc:  # noqa: BLE001 - malformed user data must stay local to one record
        return None, _clean_error(exc) or exc.__class__.__name__


def _record_key(path: str | Path) -> str:
    """Return a stable, Streamlit-safe identity for record-scoped widgets."""

    return hashlib.sha256(str(Path(path).absolute()).encode("utf-8")).hexdigest()[:16]


def _normalize_cell(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _nonnegative_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return max(0.0, number)


def _editor_table(rows: Any, columns: Iterable[str]) -> dict[str, list[Any]]:
    """Build a dict-of-columns so an empty data editor still has a fixed schema."""

    column_names = tuple(columns)
    normalized_rows = [row for row in (rows or []) if isinstance(row, dict)] if isinstance(rows, list) else []
    return {
        column: [_normalize_cell(row.get(column)) for row in normalized_rows]
        for column in column_names
    }


def _editor_rows(value: Any, columns: Iterable[str]) -> list[dict[str, Any]]:
    """Convert Streamlit's editable-table return value back into JSON rows."""

    column_names = tuple(columns)
    if hasattr(value, "to_dict"):
        candidates = value.to_dict("records")
    elif isinstance(value, dict):
        lengths = [len(items) for items in value.values() if isinstance(items, list)]
        row_count = max(lengths, default=0)
        candidates = [
            {
                column: value.get(column, [])[index]
                if isinstance(value.get(column), list) and index < len(value[column])
                else None
                for column in column_names
            }
            for index in range(row_count)
        ]
    elif isinstance(value, list):
        candidates = [row for row in value if isinstance(row, dict)]
    else:
        candidates = []

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        row = {column: _normalize_cell(candidate.get(column)) for column in column_names}
        if any(cell is not None and (not isinstance(cell, str) or cell.strip()) for cell in row.values()):
            rows.append(row)
    return rows


def _merge_editor_rows(value: Any, columns: Iterable[str], originals: Any) -> list[dict[str, Any]]:
    """Keep non-visible audit fields when editing the user-facing columns."""

    visible_rows = _editor_rows(value, columns)
    source_rows = originals if isinstance(originals, list) else []
    merged: list[dict[str, Any]] = []
    for index, visible in enumerate(visible_rows):
        base = dict(source_rows[index]) if index < len(source_rows) and isinstance(source_rows[index], dict) else {}
        base.update(visible)
        merged.append(base)
    return merged


def _load_batch_urls(links_text: str, links_file: str) -> list[str]:
    urls = [line.strip() for line in links_text.splitlines() if line.strip()]
    if links_file.strip():
        path = Path(links_file.strip()).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"链接文件不存在：{path}")
        if not path.is_file():
            raise IsADirectoryError(f"链接文件路径不是文件：{path}")
        urls.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return list(dict.fromkeys(urls))


def _backup_files(paths: Iterable[Path | None], action: str) -> list[Path]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_action = re.sub(r"[^a-zA-Z0-9_-]+", "-", action).strip("-") or "change"
    backups: list[Path] = []
    for path in paths:
        if not path or not path.is_file():
            continue
        backup_path = path.with_name(f"{path.stem}.before-{safe_action}-{timestamp}{path.suffix}")
        shutil.copy2(path, backup_path)
        backups.append(backup_path)
    return backups


def _backup_summary(backups: Iterable[Path]) -> str:
    names = [path.name for path in backups]
    return f"；备份：{', '.join(names)}" if names else ""


def _clear_state_prefix(st, prefix: str) -> None:
    for key in list(st.session_state):
        if str(key).startswith(prefix):
            del st.session_state[key]


def _rerun_with_notice(st, message: str, *, level: str = "success", clear_prefix: str | None = None) -> None:
    if clear_prefix:
        _clear_state_prefix(st, clear_prefix)
    st.session_state[FLASH_STATE_KEY] = {"level": level, "message": message}
    st.rerun()


def _show_pending_notice(st) -> None:
    notice = st.session_state.pop(FLASH_STATE_KEY, None)
    if not isinstance(notice, dict):
        return
    renderer = getattr(st, str(notice.get("level") or "success"), st.info)
    renderer(str(notice.get("message") or ""))


def _transcript_duration(transcript_path: Path | None) -> float | None:
    if not transcript_path or not transcript_path.is_file():
        return None
    try:
        raw = json.loads(transcript_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    segments = raw.get("segments", []) if isinstance(raw, dict) else raw
    if not isinstance(segments, list):
        return None
    ends = [
        float(item["end"])
        for item in segments
        if isinstance(item, dict) and isinstance(item.get("end"), (int, float))
    ]
    return max(ends, default=None)


def _validate_recipe(data: dict) -> Recipe:
    if hasattr(Recipe, "model_validate"):
        return Recipe.model_validate(data)
    return Recipe(**data)


def _dump_model(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.__dict__


def _open_folder(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _history_options(items: list[HistoryItem]) -> dict[str, HistoryItem]:
    return {f"{item.title} | {item.output_folder.name}": item for item in items}


def _select_history_item(st, label: str, options: dict[str, HistoryItem], key: str) -> HistoryItem:
    focus = st.session_state.get("_focus_output_folder")
    if focus:
        focused_label = next(
            (name for name, item in options.items() if str(item.output_folder.resolve()) == str(Path(focus).resolve())),
            None,
        )
        if focused_label:
            st.session_state[key] = focused_label
            st.session_state.pop("_focus_output_folder", None)
    return options[st.selectbox(label, list(options), key=key)]


def _navigate_to_record(st, page: str, output_folder: Path) -> None:
    st.session_state["_next_page"] = page
    st.session_state["_focus_output_folder"] = str(output_folder.resolve())
    st.rerun()


def _review_item_label(item: dict[str, Any]) -> str:
    value = item.get("value") or item.get("original") or {}
    section_names = {"ingredients": "主料", "seasonings": "调料", "steps": "步骤"}
    section = str(item.get("section") or "")
    if section == "steps":
        name = value.get("title") or str(value.get("action") or "")[:24]
    else:
        name = value.get("name")
    decision = {"pending": "待处理", "accepted": "已采用", "edited": "已修改", "skipped": "已跳过"}.get(
        item.get("decision"), "未知"
    )
    return f"[{decision}] {section_names.get(section, section)} · {name or item.get('id')}"


def _job_options(url: str, config: UIConfig) -> RecipeJobOptions:
    return RecipeJobOptions(
        url=url.strip(),
        cookies=_optional_text(config.cookies),
        out=config.out_dir,
        no_screenshot=not config.enable_screenshot,
        whisper_model=config.whisper_model,
        language=config.language,
        keep_media=config.keep_media,
        no_llm_summary=not config.enable_llm_summary or config.llm_provider == "none",
        llm_provider=config.llm_provider,
        openai_model=config.openai_model,
        local_llm_command=config.local_llm_command,
        codex_model=config.codex_model,
        codex_profile=config.codex_profile,
        llm_cli_extra_instructions=config.llm_cli_extra_instructions,
        max_recipe_steps=config.max_recipe_steps,
        max_step_images=config.max_step_images,
        enable_recipe_review=config.enable_recipe_review,
    )


def _vault_path(config: UIConfig) -> Path:
    path = Path(config.obsidian_vault_dir).expanduser()
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def _archive_output(output_folder: Path, config: UIConfig, *, overwrite: bool = False):
    result = archive_recipe(
        output_folder,
        _vault_path(config),
        conflict="overwrite" if overwrite else "update",
    )
    knowledge_results = ()
    knowledge_error: Exception | None = None
    if config.archive_knowledge_with_recipe:
        knowledge_results, knowledge_error = _archive_approved_knowledge(config, overwrite=overwrite)
    return result, knowledge_results, knowledge_error


def _archive_approved_knowledge(config: UIConfig, *, overwrite: bool = False):
    approved = [entry for entry in load_knowledge_entries() if entry.review_status == "approved"]
    if not approved:
        return (), None
    try:
        return (
            archive_knowledge(
                approved,
                _vault_path(config),
                conflict="overwrite" if overwrite else "update",
            ),
            None,
        )
    except Exception as exc:  # recipe archive remains successful
        return (), exc


def _optimize_options(config: UIConfig) -> OptimizeOptions:
    return OptimizeOptions(
        llm_provider=config.llm_provider,
        openai_model=config.openai_model,
        local_llm_command=config.local_llm_command,
        codex_model=config.codex_model,
        codex_profile=config.codex_profile,
        llm_cli_extra_instructions=config.llm_cli_extra_instructions,
        no_llm_summary=not config.enable_llm_summary or config.llm_provider == "none",
    )


def _regenerate_note_kwargs(config: UIConfig) -> dict:
    return {
        "no_llm_summary": not config.enable_llm_summary or config.llm_provider == "none",
        "llm_provider": config.llm_provider,
        "openai_model": config.openai_model,
        "local_llm_command": config.local_llm_command,
        "codex_model": config.codex_model,
        "codex_profile": config.codex_profile,
        "llm_cli_extra_instructions": config.llm_cli_extra_instructions,
    }


def _content_analysis_options(config: UIConfig, output_filename: str) -> ContentAnalysisOptions:
    return ContentAnalysisOptions(
        llm_provider=config.llm_provider,
        openai_model=config.openai_model,
        local_llm_command=config.local_llm_command,
        codex_model=config.codex_model,
        codex_profile=config.codex_profile,
        llm_cli_extra_instructions=config.llm_cli_extra_instructions,
        output_filename=_markdown_filename(output_filename),
    )


def _knowledge_extraction_options(config: UIConfig) -> KnowledgeExtractionOptions:
    return KnowledgeExtractionOptions(
        llm_provider=config.llm_provider,
        openai_model=config.openai_model,
        local_llm_command=config.local_llm_command,
        codex_model=config.codex_model,
        codex_profile=config.codex_profile,
        llm_cli_extra_instructions=config.llm_cli_extra_instructions,
    )


def _markdown_filename(filename: str) -> str:
    name = Path(filename.strip() or "extra_analysis.md").name
    if not name.lower().endswith(".md"):
        name += ".md"
    return name


def _display_quality(st, output_folder: Path) -> None:
    report = analyze_recipe_quality(output_folder)
    st.metric("结构完整度", report.score)
    st.caption(report.summary)
    st.caption("该评分仅反映字段完整度，不是食品安全或事实准确性结论；烹饪前请对照原视频确认。")
    if report.issues:
        st.dataframe(
            [
                {
                    "级别": issue.severity,
                    "问题": issue.message,
                    "建议": issue.suggestion,
                }
                for issue in report.issues
            ],
            width="stretch",
        )


def _render_sidebar(st, config: UIConfig) -> UIConfig:
    st.sidebar.header("默认配置")
    config.out_dir = st.sidebar.text_input("输出目录", value=config.out_dir)
    config.cookies = _optional_text(st.sidebar.text_input("cookies 文件路径", value=config.cookies or ""))
    config.language = st.sidebar.text_input("字幕/转写语言", value=config.language)
    config.whisper_model = st.sidebar.selectbox(
        "Whisper 模型",
        WHISPER_MODELS,
        index=WHISPER_MODELS.index(config.whisper_model)
        if config.whisper_model in WHISPER_MODELS
        else 2,
    )
    config.enable_screenshot = st.sidebar.checkbox("生成步骤截图", value=config.enable_screenshot)
    config.enable_llm_summary = st.sidebar.checkbox("使用 LLM 结构化抽取 / 重写", value=config.enable_llm_summary)
    config.keep_media = st.sidebar.checkbox("保留临时媒体文件", value=config.keep_media)
    with st.sidebar.expander("成品精简与审核", expanded=False):
        config.max_recipe_steps = int(
            st.number_input("最终版最多步骤", min_value=4, max_value=12, value=config.max_recipe_steps, step=1)
        )
        config.max_step_images = int(
            st.number_input("最多关键图片", min_value=1, max_value=6, value=config.max_step_images, step=1)
        )
        config.enable_recipe_review = st.checkbox(
            "同时生成逐项审核版",
            value=config.enable_recipe_review,
            help="保存证据和置信度，之后可在“审核确认”中逐项采用、修改或跳过。",
        )
    with st.sidebar.expander("Obsidian 归档", expanded=False):
        config.obsidian_vault_dir = st.text_input(
            "笔记本目录",
            value=config.obsidian_vault_dir,
            help="可以是现有 Obsidian vault，也可以是将要创建的新目录。",
        )
        config.auto_archive_after_generation = st.checkbox(
            "生成后直接归档",
            value=config.auto_archive_after_generation,
            help="关闭时先进入草稿箱；开启后仍可继续编辑并再次归档更新。",
        )
        config.archive_knowledge_with_recipe = st.checkbox(
            "归档时同步通用技巧",
            value=config.archive_knowledge_with_recipe,
            help="把已经提炼的通用烹饪技巧同步到笔记本的技巧目录。",
        )
    config.llm_provider = st.sidebar.selectbox(
        "LLM provider",
        LLM_PROVIDERS,
        index=LLM_PROVIDERS.index(config.llm_provider)
        if config.llm_provider in LLM_PROVIDERS
        else 0,
    )
    if config.llm_provider == "codex":
        config.codex_model = _optional_text(st.sidebar.text_input("Codex 模型", value=config.codex_model or ""))
        config.codex_profile = _optional_text(st.sidebar.text_input("Codex profile", value=config.codex_profile or ""))
    if config.llm_provider == "openai":
        config.openai_model = st.sidebar.text_input("OpenAI 模型", value=config.openai_model)
    if config.llm_provider == "local":
        config.local_llm_command = _optional_text(
            st.sidebar.text_input("本地 LLM 命令", value=config.local_llm_command or "")
        )
    if config.llm_provider in {"opencode", "codex", "local"}:
        with st.sidebar.expander("LLM CLI 高级提示词", expanded=False):
            st.caption("附加到结构化抽取、笔记优化、二次分析和知识提取；不会替换内置格式与安全约束。")
            preset_name = st.selectbox(
                "提示词预设",
                list(CLI_PROMPT_PRESETS),
                key="llm_cli_prompt_preset",
            )
            editor_key = "llm_cli_extra_instructions_editor"
            if editor_key not in st.session_state:
                st.session_state[editor_key] = config.llm_cli_extra_instructions or ""
            if st.button("载入所选预设", key="load_llm_cli_prompt_preset"):
                st.session_state[editor_key] = CLI_PROMPT_PRESETS[preset_name]
            config.llm_cli_extra_instructions = _optional_text(
                st.text_area(
                    "附加指令",
                    height=180,
                    key=editor_key,
                    placeholder="例如：优先保留原话中的克数、温度和成熟判断标准。",
                )
            )
            if st.checkbox("显示合成预览", key="show_llm_cli_prompt_preview"):
                st.code(
                    apply_cli_extra_instructions(
                        "【应用内置任务提示词】",
                        config.llm_cli_extra_instructions,
                    ),
                    language="text",
                )
    if st.sidebar.button("保存默认配置"):
        path = save_config(config)
        st.sidebar.success(f"已保存：{path}")
    return config


def _log_box(st, height: int = 220):
    log_lines: list[str] = []
    box = st.empty()

    def log(message: str) -> None:
        log_lines.append(message)
        box.text_area("运行日志", value="\n".join(log_lines), height=height)

    return log


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Bili Recipe Notes", layout="wide")
    st.title("Bili Recipe Notes")
    _show_pending_notice(st)

    next_page = st.session_state.pop("_next_page", None)
    if next_page in PAGES:
        st.session_state["main_page"] = next_page
    st.sidebar.header("功能导航")
    active_page = st.sidebar.selectbox("当前功能", PAGES, key="main_page")
    config = _render_sidebar(st, load_config())
    st.caption(f"当前功能：{active_page}")

    if active_page == "单视频生成":
        st.subheader("生成菜谱笔记")
        url = st.text_input("视频 URL", placeholder="https://www.bilibili.com/video/BV...")
        if st.button("开始生成", type="primary", disabled=not url.strip()):
            save_config(config)
            log = _log_box(st)
            try:
                with st.spinner("正在生成菜谱笔记..."):
                    result = generate_recipe_note(_job_options(url, config), log=log)
            except Exception as exc:  # noqa: BLE001
                error_message = _clean_error(exc)
                log(error_message)
                st.error(f"生成失败：{error_message}")
            else:
                st.session_state["last_generated_output_folder"] = str(result.output_folder.resolve())
                st.success(f"生成完成：{result.output_folder}")
                if config.auto_archive_after_generation:
                    try:
                        archived, archived_knowledge, knowledge_error = _archive_output(result.output_folder, config)
                    except Exception as exc:  # generation remains successful
                        st.warning(f"菜谱已生成，但自动归档失败：{_clean_error(exc)}")
                    else:
                        st.success(f"已自动归档：{archived.note_path}")
                        if archived_knowledge:
                            st.caption(f"同时同步 {len(archived_knowledge)} 条已确认通用技巧。")
                        if knowledge_error:
                            st.warning(f"菜谱归档成功，但技巧同步失败：{_clean_error(knowledge_error)}")
                if result.stage_errors:
                    st.warning("\n".join(result.stage_errors))
                st.markdown("#### 笔记预览")
                _render_note_preview(st, result.final_note, result.output_folder)
                st.markdown("#### 输出文件")
                st.code(
                    _render_paths([result.output_folder, result.note_path, result.recipe_path, result.transcript_path]),
                    language="text",
                )
                st.download_button("下载 note.md", data=result.final_note, file_name="note.md", mime="text/markdown")
        last_generated_value = st.session_state.get("last_generated_output_folder")
        last_generated = Path(last_generated_value) if isinstance(last_generated_value, str) else None
        if last_generated and (last_generated / "recipe.json").is_file():
            st.markdown("#### 下一步")
            st.caption("当前结果已进入草稿箱；可以先完整编辑、逐项审核，也可以不修改直接归档。")
            col_edit, col_review, col_drafts = st.columns(3)
            with col_edit:
                if st.button("编辑完整菜谱", key="generated_go_edit"):
                    _navigate_to_record(st, "编辑修复", last_generated)
            with col_review:
                if st.button("逐项审核 AI 结果", key="generated_go_review"):
                    _navigate_to_record(st, "审核确认", last_generated)
            with col_drafts:
                if st.button("查看草稿与归档", key="generated_go_drafts"):
                    _navigate_to_record(st, "草稿与归档", last_generated)

    if active_page == "草稿与归档":
        st.subheader("草稿与归档")
        items = scan_history(config.out_dir)
        query = st.text_input("搜索", placeholder="标题、UP 主、URL")
        filtered = [
            item
            for item in items
            if not query.strip()
            or query.lower() in item.title.lower()
            or query.lower() in (item.uploader or "").lower()
            or query.lower() in item.source_url.lower()
            or query.lower() in item.category.lower()
            or query.lower() in item.cuisine.lower()
            or any(query.lower() in tag.lower() for tag in (item.tags or []))
        ]
        st.caption(f"共 {len(filtered)} 条")
        if filtered:
            st.dataframe(
                [
                    {
                        "标题": item.title,
                        "UP主": item.uploader or "",
                        "分类": item.category,
                        "标签": ", ".join(item.tags or []),
                        "状态": item.status,
                        "工作流": {
                            "archived": "已归档",
                            "stale": "归档后有修改",
                            "archive_error": "归档异常",
                        }.get(item.workflow_status, "待整理"),
                        "结构完整度": item.quality_score if item.quality_score is not None else "",
                        "完成时间": item.finished_at or "",
                        "目录": str(item.output_folder),
                    }
                    for item in filtered
                ],
                width="stretch",
            )
            options = _history_options(filtered)
            selected = _select_history_item(st, "选择记录", options, "history_select")
            history_key = _record_key(selected.output_folder)
            if selected.workflow_status == "archived":
                st.success(f"已归档：{selected.archive_note_path or 'Obsidian 笔记本'}")
                if selected.archived_at:
                    st.caption(f"最近归档时间：{selected.archived_at}；再次归档会更新同一篇笔记。")
            elif selected.workflow_status == "stale":
                st.warning("当前草稿在上次归档后又有修改，需要重新归档才能同步最新版本。")
            elif selected.workflow_status == "archive_error":
                st.error("归档清单或目标笔记异常，请检查路径后重新归档。")
            else:
                st.info("当前是待整理草稿：可直接归档，也可以先到“编辑修复”或“审核确认”处理。")
            confirm_overwrite = st.checkbox(
                "确认允许覆盖这条记录的已有文件",
                key=f"history_{history_key}_confirm_overwrite",
            )
            st.caption("完整重新生成、重写和优化会覆盖已有文件；执行前会创建带时间戳的备份。")
            col_a, col_b, col_c, col_d = st.columns(4)
            with col_a:
                if st.button("打开输出目录", key=f"history_{history_key}_open"):
                    _open_folder(selected.output_folder)
            with col_b:
                if st.button(
                    "完整重新生成",
                    disabled=not selected.source_url or not confirm_overwrite,
                    key=f"history_{history_key}_regenerate",
                ):
                    log = _log_box(st)
                    try:
                        backups = _backup_files(
                            [selected.recipe_path, selected.note_path, selected.transcript_path, selected.job_path],
                            "regenerate",
                        )
                        result = generate_recipe_note(_job_options(selected.source_url, config), log=log)
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"重新生成失败：{_clean_error(exc)}")
                    else:
                        _rerun_with_notice(
                            st,
                            f"重新生成完成：{result.output_folder}{_backup_summary(backups)}",
                            clear_prefix=f"history_{history_key}_confirm_",
                        )
            with col_c:
                if st.button(
                    "仅重写 note.md",
                    disabled=not selected.recipe_path or not confirm_overwrite,
                    key=f"history_{history_key}_rewrite",
                ):
                    try:
                        backups = _backup_files([selected.note_path], "rewrite")
                        result = regenerate_note_from_recipe(selected.output_folder, **_regenerate_note_kwargs(config))
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"重写失败：{_clean_error(exc)}")
                    else:
                        message = f"已重写：{result.note_path}{_backup_summary(backups)}"
                        if result.stage_errors:
                            message += f"；提示：{'；'.join(result.stage_errors)}"
                        _rerun_with_notice(
                            st,
                            message,
                            clear_prefix=f"history_{history_key}_confirm_",
                        )
            with col_d:
                export_kind = st.selectbox(
                    "导出格式",
                    EXPORT_KINDS,
                    key=f"history_{history_key}_export_kind",
                )
                if st.button("导出", disabled=not selected.note_path, key=f"history_{history_key}_export"):
                    try:
                        exported = export_note(selected.note_path, export_kind)  # type: ignore[arg-type]
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"导出失败：{_clean_error(exc)}")
                    else:
                        st.session_state[f"history_{history_key}_last_export"] = str(exported)
                        st.success(f"已导出：{exported}")
                last_export_value = st.session_state.get(f"history_{history_key}_last_export")
                last_export = Path(last_export_value) if isinstance(last_export_value, str) else None
                if last_export and last_export.is_file():
                    try:
                        export_data = last_export.read_bytes()
                    except OSError as exc:
                        st.warning(f"导出文件暂时无法读取：{_clean_error(exc)}")
                    else:
                        st.download_button(
                            "下载导出文件",
                            data=export_data,
                            file_name=last_export.name,
                            mime=EXPORT_MIME_TYPES.get(last_export.suffix.lower(), "application/octet-stream"),
                            key=f"history_{history_key}_download_export",
                        )
            st.markdown("#### 草稿后处理")
            force_vault_overwrite = st.checkbox(
                "如果 Vault 中这篇笔记被手动改过，确认用当前草稿覆盖",
                key=f"history_{history_key}_force_archive",
            )
            col_edit, col_review, col_archive, col_tips = st.columns(4)
            with col_edit:
                if st.button("编辑完整菜谱", key=f"history_{history_key}_go_edit"):
                    _navigate_to_record(st, "编辑修复", selected.output_folder)
            with col_review:
                if st.button("逐项审核", key=f"history_{history_key}_go_review"):
                    _navigate_to_record(st, "审核确认", selected.output_folder)
            with col_archive:
                archive_label = "重新归档当前版本" if selected.workflow_status in {"archived", "stale"} else "无需修改，直接归档"
                if st.button(archive_label, type="primary", key=f"history_{history_key}_archive"):
                    try:
                        archived, archived_knowledge, knowledge_error = _archive_output(
                            selected.output_folder,
                            config,
                            overwrite=force_vault_overwrite,
                        )
                    except ObsidianArchiveConflict as exc:
                        st.error(f"Vault 中的笔记有手动修改，未覆盖：{_clean_error(exc)}")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"归档失败：{_clean_error(exc)}")
                    else:
                        message = f"已归档：{archived.note_path}"
                        if archived_knowledge:
                            message += f"；同步 {len(archived_knowledge)} 条已确认技巧"
                        if knowledge_error:
                            message += f"；技巧同步失败：{_clean_error(knowledge_error)}"
                        _rerun_with_notice(st, message)
            with col_tips:
                if st.button(
                    "AI 提炼通用技巧",
                    disabled=config.llm_provider == "none",
                    key=f"history_{history_key}_extract_tips",
                ):
                    try:
                        with st.spinner("正在提炼通用技巧候选..."):
                            extracted = extract_knowledge_from_video(
                                selected.output_folder,
                                options=_knowledge_extraction_options(config),
                            )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"技巧提炼失败：{_clean_error(exc)}")
                    else:
                        st.session_state["_next_page"] = "知识库"
                        _rerun_with_notice(
                            st,
                            f"已生成技巧候选：新增 {extracted.added_count} 条、更新 {extracted.updated_count} 条；"
                            "请在知识库确认后再同步到笔记本。",
                        )
            st.markdown("#### 质量报告")
            _display_quality(st, selected.output_folder)
            if st.button(
                "一键优化笔记",
                disabled=not selected.note_path or not selected.recipe_path or not confirm_overwrite,
                key=f"history_{history_key}_optimize",
            ):
                try:
                    backups = _backup_files([selected.note_path], "optimize")
                    optimized = optimize_existing_note(selected.output_folder, _optimize_options(config))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"优化失败：{_clean_error(exc)}")
                else:
                    _rerun_with_notice(
                        st,
                        f"优化完成：{optimized.quality_before.score} -> {optimized.quality_after.score}"
                        f"{_backup_summary(backups)}",
                        clear_prefix=f"history_{history_key}_confirm_",
                    )
            note = _read_text(selected.note_path)
            if note:
                st.markdown("#### note.md 预览")
                _render_note_preview(st, note, selected.output_folder)
        else:
            st.info("还没有历史记录，或搜索没有匹配结果。")

    if active_page == "审核确认":
        st.subheader("菜谱逐项审核")
        st.caption("证据、置信度和不确定信息只在这里展示；全部解决后再应用为简洁的最终菜谱。")
        reviewable = [item for item in scan_history(config.out_dir) if item.recipe_path]
        if not reviewable:
            st.info("还没有可以审核的菜谱。")
        else:
            options = _history_options(reviewable)
            selected = _select_history_item(st, "选择菜谱", options, "review_select")
            record_key = _record_key(selected.output_folder)
            current_review_path = review_path(selected.output_folder)
            if not current_review_path.exists():
                st.info("这份菜谱还没有审核版。可以先按当前精简设置创建，原 recipe.json 不会立即改变。")
                if st.button("创建逐项审核版", type="primary", key=f"review_{record_key}_create"):
                    try:
                        data = _recipe_to_data(selected.recipe_path)  # type: ignore[arg-type]
                        recipe = condense_recipe_steps(_validate_recipe(data), config.max_recipe_steps)
                        path = create_recipe_review(recipe, selected.output_folder)
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"创建审核版失败：{_clean_error(exc)}")
                    else:
                        _rerun_with_notice(st, f"已创建审核版：{path}")
            else:
                try:
                    review = load_recipe_review(selected.output_folder)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"审核文件无法读取：{_clean_error(exc)}")
                else:
                    items = review["items"]
                    pending_items = [item for item in items if item.get("decision") == "pending"]
                    resolved = len(items) - len(pending_items)
                    st.progress(resolved / len(items) if items else 1.0, text=f"已解决 {resolved}/{len(items)} 项")
                    col_reset, col_accept_all = st.columns(2)
                    with col_reset:
                        if st.button("重新创建审核版", key=f"review_{record_key}_reset"):
                            recipe = condense_recipe_steps(
                                _validate_recipe(_recipe_to_data(selected.recipe_path)),  # type: ignore[arg-type]
                                config.max_recipe_steps,
                            )
                            create_recipe_review(recipe, selected.output_folder)
                            _rerun_with_notice(st, "已重新创建审核版，所有决定已重置。")
                    with col_accept_all:
                        if st.button(
                            "全部采用剩余项",
                            disabled=not pending_items,
                            key=f"review_{record_key}_accept_all",
                        ):
                            accept_all_pending_review_items(selected.output_folder)
                            _rerun_with_notice(st, "已采用全部剩余审核项。")

                    if items:
                        ordered = [*pending_items, *[item for item in items if item.get("decision") != "pending"]]
                        labels = {_review_item_label(item): item for item in ordered}
                        selected_label = st.selectbox(
                            "当前审核项（待处理项优先）",
                            list(labels),
                            key=f"review_{record_key}_item",
                        )
                        current = labels[selected_label]
                        original = current.get("original") or {}
                        confidence = original.get("confidence")
                        evidence = original.get("evidence")
                        source_time = original.get("source_time", original.get("start_time"))
                        meta = []
                        if confidence is not None:
                            meta.append(f"置信度 {round(float(confidence) * 100)}%")
                        if source_time is not None:
                            meta.append(f"视频位置约 {float(source_time):.1f} 秒")
                        if meta:
                            st.caption(" · ".join(meta))
                        if evidence:
                            st.markdown("**字幕证据**")
                            st.code(str(evidence), language="text")
                        editor_key = f"review_{record_key}_{current['id']}_json"
                        edited_json = st.text_area(
                            "采用内容（需要时可直接修改 JSON）",
                            value=json.dumps(current.get("value") or original, ensure_ascii=False, indent=2),
                            height=260,
                            key=editor_key,
                        )
                        comment = st.text_input(
                            "审核备注（可选）",
                            value=str(current.get("comment") or ""),
                            key=f"review_{record_key}_{current['id']}_comment",
                        )
                        col_accept, col_edit, col_skip = st.columns(3)
                        decision: str | None = None
                        with col_accept:
                            if st.button("采用并下一项", type="primary", key=f"review_{record_key}_accept"):
                                decision = "accepted"
                        with col_edit:
                            if st.button("修改后采用", key=f"review_{record_key}_edit"):
                                decision = "edited"
                        with col_skip:
                            if st.button("跳过此项", key=f"review_{record_key}_skip"):
                                decision = "skipped"
                        if decision:
                            try:
                                edited_value = json.loads(edited_json) if decision == "edited" else None
                                decide_review_item(
                                    selected.output_folder,
                                    str(current["id"]),
                                    decision,
                                    value=edited_value,
                                    comment=comment,
                                )
                            except Exception as exc:  # noqa: BLE001
                                st.error(f"保存审核决定失败：{_clean_error(exc)}")
                            else:
                                _rerun_with_notice(st, "审核决定已保存，已切换到下一项。")

                    if not pending_items:
                        st.success("所有审核项已解决，可以应用到最终菜谱。")
                        if st.button("应用审核结果并生成最终版", type="primary", key=f"review_{record_key}_apply"):
                            try:
                                backups = _backup_files(
                                    [selected.recipe_path, selected.note_path, current_review_path],
                                    "review-apply",
                                )
                                recipe = recipe_from_completed_review(selected.output_folder)
                                atomic_write_json(selected.recipe_path, _dump_model(recipe))  # type: ignore[arg-type]
                                result = regenerate_note_from_recipe(
                                    selected.output_folder,
                                    **_regenerate_note_kwargs(config),
                                )
                            except Exception as exc:  # noqa: BLE001
                                st.error(f"应用审核结果失败：{_clean_error(exc)}")
                            else:
                                _rerun_with_notice(
                                    st,
                                    f"审核结果已应用：{result.note_path}{_backup_summary(backups)}",
                                )

    if active_page == "批量处理":
        st.subheader("批量处理")
        links_text = st.text_area("视频 URL，每行一个", height=180)
        links_file = st.text_input("或读取链接文件路径", placeholder="outputs/creator_video_links.txt")
        skip_existing = st.checkbox("已生成则跳过", value=True)
        use_queue = st.checkbox("保存为可续跑批次", value=True)
        try:
            batch_states = list_batch_states()
        except Exception as exc:  # noqa: BLE001
            batch_states = []
            st.error(f"读取批次状态失败：{_clean_error(exc)}")
        batch_labels = [f"{state.batch_id} | {state.updated_at} | {len(state.items)} 条" for state in batch_states]
        selected_batch_label = st.selectbox("已有批次", [""] + batch_labels)
        selected_batch_id = selected_batch_label.split(" | ", 1)[0] if selected_batch_label else None

        col_new, col_resume, col_retry = st.columns(3)
        run_mode = None
        with col_new:
            if st.button("创建新批次并运行", type="primary"):
                run_mode = "new-queue"
        with col_resume:
            if st.button("继续未完成", disabled=not selected_batch_id):
                run_mode = "resume-unfinished"
        with col_retry:
            if st.button("只重试失败", disabled=not selected_batch_id):
                run_mode = "retry-failed"

        if st.button("开始批量生成", type="primary"):
            run_mode = "new-direct"

        if run_mode:
            try:
                if run_mode in {"resume-unfinished", "retry-failed"} and selected_batch_id:
                    urls = []
                else:
                    urls = _load_batch_urls(links_text, links_file)
                if not urls and run_mode not in {"resume-unfinished", "retry-failed"}:
                    raise ValueError("请先输入 URL 或提供有效的链接文件。")

                save_config(config)
                log = _log_box(st, height=260)
                batch_id = None
                resume_mode = "new"
                if run_mode == "new-queue":
                    batch_id = create_batch_id()
                elif run_mode in {"resume-unfinished", "retry-failed"}:
                    batch_id = selected_batch_id
                    resume_mode = run_mode
                elif use_queue:
                    batch_id = create_batch_id()
                result = run_batch(
                    BatchJobOptions(
                        urls=urls,
                        cookies=_optional_text(config.cookies),
                        out=config.out_dir,
                        no_screenshot=not config.enable_screenshot,
                        whisper_model=config.whisper_model,
                        language=config.language,
                        keep_media=config.keep_media,
                        no_llm_summary=not config.enable_llm_summary or config.llm_provider == "none",
                        llm_provider=config.llm_provider,
                        openai_model=config.openai_model,
                        local_llm_command=config.local_llm_command,
                        codex_model=config.codex_model,
                        codex_profile=config.codex_profile,
                        llm_cli_extra_instructions=config.llm_cli_extra_instructions,
                        max_recipe_steps=config.max_recipe_steps,
                        max_step_images=config.max_step_images,
                        enable_recipe_review=config.enable_recipe_review,
                        skip_existing=skip_existing,
                        batch_id=batch_id,
                        resume_mode=resume_mode,
                    ),
                    log=log,
                )
            except Exception as exc:  # noqa: BLE001 - batch failures must not take down the UI
                st.error(f"批量处理失败：{_clean_error(exc)}")
            else:
                st.dataframe([item.__dict__ for item in result.items], width="stretch")
                if config.auto_archive_after_generation:
                    completed_folders = [item.output_folder for item in result.items if item.output_folder and item.status == "done"]
                    if completed_folders:
                        archived_batch = archive_recipe_batch(completed_folders, _vault_path(config))
                        knowledge_results, knowledge_error = (
                            _archive_approved_knowledge(config)
                            if config.archive_knowledge_with_recipe
                            else ((), None)
                        )
                        st.success(
                            f"自动归档完成：成功 {archived_batch.archived_count}，"
                            f"跳过 {archived_batch.skipped_count}，失败 {archived_batch.failed_count}。"
                        )
                        if knowledge_results:
                            st.caption(f"同步 {len(knowledge_results)} 条已确认通用技巧。")
                        if knowledge_error:
                            st.warning(f"技巧同步失败：{_clean_error(knowledge_error)}")
                if batch_id:
                    st.success(f"批次状态已保存：{batch_id}")

        if selected_batch_id:
            selected_state = next((state for state in batch_states if state.batch_id == selected_batch_id), None)
            if selected_state:
                st.markdown("#### 批次状态")
                st.dataframe([item.__dict__ for item in selected_state.items], width="stretch")
                completed_batch_items = [
                    item
                    for item in selected_state.items
                    if item.status in {"done", "skipped"}
                    and item.output_folder
                    and (Path(item.output_folder) / "recipe.json").is_file()
                ]
                if completed_batch_items:
                    st.markdown("#### 批量结果后处理")
                    st.caption("每条结果都是独立草稿，可以逐条编辑、审核或归档；批量生成不会锁死后续修改。")
                    batch_item_labels = {
                        f"{Path(item.output_folder).name} | {item.status}": item for item in completed_batch_items
                    }
                    batch_item = batch_item_labels[
                        st.selectbox("选择一条结果", list(batch_item_labels), key=f"batch_post_{selected_state.batch_id}")
                    ]
                    batch_folder = Path(str(batch_item.output_folder))
                    force_batch_overwrite = st.checkbox(
                        "确认覆盖 Vault 中这条笔记的手写修改",
                        key=f"batch_force_archive_{selected_state.batch_id}",
                    )
                    col_edit, col_review, col_archive = st.columns(3)
                    with col_edit:
                        if st.button("编辑这条", key=f"batch_edit_{selected_state.batch_id}"):
                            _navigate_to_record(st, "编辑修复", batch_folder)
                    with col_review:
                        if st.button("审核这条", key=f"batch_review_{selected_state.batch_id}"):
                            _navigate_to_record(st, "审核确认", batch_folder)
                    with col_archive:
                        if st.button("直接归档这条", type="primary", key=f"batch_archive_{selected_state.batch_id}"):
                            try:
                                archived, archived_knowledge, knowledge_error = _archive_output(
                                    batch_folder,
                                    config,
                                    overwrite=force_batch_overwrite,
                                )
                            except Exception as exc:  # noqa: BLE001
                                st.error(f"归档失败：{_clean_error(exc)}")
                            else:
                                message = f"已归档：{archived.note_path}"
                                if knowledge_error:
                                    message += f"；技巧同步失败：{_clean_error(knowledge_error)}"
                                _rerun_with_notice(st, message)
                    if st.button("归档本批次全部已完成草稿", key=f"batch_archive_all_{selected_state.batch_id}"):
                        folders = [Path(str(item.output_folder)) for item in completed_batch_items]
                        archived_batch = archive_recipe_batch(
                            folders,
                            _vault_path(config),
                            conflict="overwrite" if force_batch_overwrite else "update",
                        )
                        knowledge_results, knowledge_error = (
                            _archive_approved_knowledge(config, overwrite=force_batch_overwrite)
                            if config.archive_knowledge_with_recipe
                            else ((), None)
                        )
                        knowledge_message = f"；同步 {len(knowledge_results)} 条技巧" if knowledge_results else ""
                        if knowledge_error:
                            knowledge_message += f"；技巧同步失败：{_clean_error(knowledge_error)}"
                        _rerun_with_notice(
                            st,
                            f"批量归档完成：成功 {archived_batch.archived_count}，"
                            f"跳过 {archived_batch.skipped_count}，失败 {archived_batch.failed_count}"
                            f"{knowledge_message}。",
                        )

    if active_page == "编辑修复":
        st.subheader("编辑与修复")
        items = scan_history(config.out_dir)
        editable = [item for item in items if item.recipe_path]
        if not editable:
            st.info("没有可编辑的菜谱。")
        else:
            edit_options = _history_options(editable)
            selected = _select_history_item(st, "选择菜谱", edit_options, "edit_select")
            record_key = _record_key(selected.output_folder)
            state_prefix = f"edit_{record_key}_"
            st.markdown("#### 质量报告")
            _display_quality(st, selected.output_folder)
            confirm_overwrite = st.checkbox(
                "确认覆盖当前记录的已有文件",
                key=f"{state_prefix}confirm_overwrite",
            )
            st.caption("优化、重写、保存、重新抽取和截图都会覆盖已有文件；每次操作前会创建带时间戳的备份。")
            if st.button(
                "一键优化当前笔记",
                disabled=not selected.note_path or not confirm_overwrite,
                key=f"{state_prefix}optimize",
            ):
                try:
                    backups = _backup_files([selected.note_path], "optimize")
                    optimized = optimize_existing_note(selected.output_folder, _optimize_options(config))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"优化失败：{_clean_error(exc)}")
                else:
                    _rerun_with_notice(
                        st,
                        f"优化完成：{optimized.quality_before.score} -> {optimized.quality_after.score}"
                        f"{_backup_summary(backups)}",
                        clear_prefix=f"{state_prefix}confirm_",
                    )
            if st.button(
                "仅重写当前 note.md（不下载视频）",
                disabled=not selected.recipe_path or not confirm_overwrite,
                key=f"{state_prefix}rewrite_note",
            ):
                try:
                    backups = _backup_files([selected.note_path], "rewrite")
                    result = regenerate_note_from_recipe(selected.output_folder, **_regenerate_note_kwargs(config))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"重写失败：{_clean_error(exc)}")
                else:
                    message = f"已重写：{result.note_path}{_backup_summary(backups)}"
                    if result.stage_errors:
                        message += f"；提示：{'；'.join(result.stage_errors)}"
                    _rerun_with_notice(st, message, clear_prefix=f"{state_prefix}confirm_")

            recipe_data, recipe_error = _safe_recipe_to_data(selected.recipe_path)  # type: ignore[arg-type]
            if recipe_error or recipe_data is None:
                st.error(f"当前 recipe.json 已损坏，已跳过结构编辑：{recipe_error}")
                st.caption(f"文件：{selected.recipe_path}。可先从备份恢复，其他功能页仍可继续使用。")
                with st.expander("查看损坏的 recipe.json"):
                    st.code(_read_text(selected.recipe_path), language="json")
            else:
                st.markdown("#### 菜谱结构")
                recipe_data["title"] = st.text_input(
                    "标题",
                    value=recipe_data.get("title", ""),
                    key=f"{state_prefix}title",
                )
                recipe_data["servings"] = st.text_input(
                    "份量",
                    value=recipe_data.get("servings") or "",
                    key=f"{state_prefix}servings",
                )
                recipe_data["total_time"] = st.text_input(
                    "总耗时",
                    value=recipe_data.get("total_time") or "",
                    key=f"{state_prefix}total_time",
                )
                recipe_data["difficulty"] = st.text_input(
                    "难度",
                    value=recipe_data.get("difficulty") or "",
                    key=f"{state_prefix}difficulty",
                )
                category_options = list(dict.fromkeys([recipe_data.get("category") or "未分类", *RECIPE_CATEGORIES]))
                recipe_data["category"] = st.selectbox(
                    "归档分类",
                    category_options,
                    key=f"{state_prefix}category",
                    help="用于 Obsidian 菜谱目录，例如中餐、汤羹、西餐、糕点。",
                )
                cuisine_options = list(dict.fromkeys([recipe_data.get("cuisine") or "未分类", *RECIPE_CUISINES]))
                recipe_data["cuisine"] = st.selectbox(
                    "菜系",
                    cuisine_options,
                    key=f"{state_prefix}cuisine",
                )
                recipe_data["tags"] = [
                    tag.strip().lstrip("#")
                    for tag in st.text_input(
                        "检索标签，用逗号分隔",
                        value=", ".join(recipe_data.get("tags") or []),
                        key=f"{state_prefix}tags",
                    ).split(",")
                    if tag.strip().lstrip("#")
                ]
                original_ingredients = recipe_data.get("ingredients")
                ingredient_editor = st.data_editor(
                    _editor_table(original_ingredients, INGREDIENT_COLUMNS),
                    num_rows="dynamic",
                    column_order=list(INGREDIENT_COLUMNS),
                    column_config={"name": "名称", "amount": "用量", "note": "备注"},
                    key=f"{state_prefix}ingredients",
                )
                recipe_data["ingredients"] = _merge_editor_rows(
                    ingredient_editor, INGREDIENT_COLUMNS, original_ingredients
                )
                original_seasonings = recipe_data.get("seasonings")
                seasoning_editor = st.data_editor(
                    _editor_table(original_seasonings, INGREDIENT_COLUMNS),
                    num_rows="dynamic",
                    column_order=list(INGREDIENT_COLUMNS),
                    column_config={"name": "名称", "amount": "用量", "note": "备注"},
                    key=f"{state_prefix}seasonings",
                )
                recipe_data["seasonings"] = _merge_editor_rows(
                    seasoning_editor, INGREDIENT_COLUMNS, original_seasonings
                )
                recipe_data["tools"] = [
                    item.strip()
                    for item in st.text_area(
                        "工具，每行一个",
                        value="\n".join(recipe_data.get("tools") or []),
                        key=f"{state_prefix}tools",
                    ).splitlines()
                    if item.strip()
                ]
                recipe_data["shopping_list"] = [
                    item.strip()
                    for item in st.text_area(
                        "购物清单，每行一个",
                        value="\n".join(recipe_data.get("shopping_list") or []),
                        key=f"{state_prefix}shopping_list",
                    ).splitlines()
                    if item.strip()
                ]
                recipe_data["prep_items"] = [
                    item.strip()
                    for item in st.text_area(
                        "备菜清单，每行一个",
                        value="\n".join(recipe_data.get("prep_items") or []),
                        key=f"{state_prefix}prep_items",
                    ).splitlines()
                    if item.strip()
                ]
                recipe_data["summary_tips"] = [
                    item.strip()
                    for item in st.text_area(
                        "关键点速查，每行一个",
                        value="\n".join(recipe_data.get("summary_tips") or []),
                        key=f"{state_prefix}summary_tips",
                    ).splitlines()
                    if item.strip()
                ]
                original_steps = recipe_data.get("steps")
                steps_editor = st.data_editor(
                    _editor_table(original_steps, STEP_COLUMNS),
                    num_rows="dynamic",
                    column_order=list(STEP_COLUMNS),
                    column_config={
                        "title": "步骤标题",
                        "start_time": st.column_config.NumberColumn("开始（秒）", min_value=0.0),
                        "end_time": st.column_config.NumberColumn("结束（秒）", min_value=0.0),
                        "action": "操作",
                        "heat": "火候",
                        "duration": "时长",
                        "tips": "提示",
                        "screenshot_path": "截图路径",
                    },
                    key=f"{state_prefix}steps",
                )
                recipe_data["steps"] = _merge_editor_rows(steps_editor, STEP_COLUMNS, original_steps)
                if st.button(
                    "保存菜谱并重新生成 note.md",
                    disabled=not confirm_overwrite,
                    key=f"{state_prefix}save_recipe",
                ):
                    try:
                        recipe = _validate_recipe(recipe_data)
                        backups = _backup_files([selected.recipe_path, selected.note_path], "recipe-edit")
                        atomic_write_json(selected.recipe_path, _dump_model(recipe))  # type: ignore[arg-type]
                        result = regenerate_note_from_recipe(
                            selected.output_folder,
                            **_regenerate_note_kwargs(config),
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"保存失败：{_clean_error(exc)}")
                    else:
                        _rerun_with_notice(
                            st,
                            f"已保存：{result.note_path}{_backup_summary(backups)}",
                            clear_prefix=state_prefix,
                        )

            st.markdown("#### 最终 Markdown 手动编辑")
            st.caption("这里保存的是归档时使用的最终正文；以后点击“从结构重新生成”会覆盖这些手写修改。")
            final_markdown = st.text_area(
                "note.md",
                value=_read_text(selected.note_path),
                height=420,
                key=f"{state_prefix}final_markdown",
            )
            force_edit_archive = st.checkbox(
                "归档时覆盖 Vault 中这篇笔记的手写修改",
                key=f"{state_prefix}force_archive",
            )
            col_save_markdown, col_save_archive = st.columns(2)
            with col_save_markdown:
                save_final_markdown = st.button(
                    "保存最终 Markdown",
                    disabled=not selected.note_path or not confirm_overwrite or not final_markdown.strip(),
                    key=f"{state_prefix}save_final_markdown",
                )
            with col_save_archive:
                save_and_archive = st.button(
                    "保存并归档到 Obsidian",
                    type="primary",
                    disabled=not selected.note_path or not confirm_overwrite or not final_markdown.strip(),
                    key=f"{state_prefix}save_and_archive",
                )
            if save_final_markdown or save_and_archive:
                try:
                    backups = _backup_files([selected.note_path], "markdown-edit")
                    atomic_write_text(selected.note_path, final_markdown.strip() + "\n")  # type: ignore[arg-type]
                    archived = None
                    knowledge_results = ()
                    knowledge_error = None
                    if save_and_archive:
                        archived, knowledge_results, knowledge_error = _archive_output(
                            selected.output_folder,
                            config,
                            overwrite=force_edit_archive,
                        )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"保存或归档失败：{_clean_error(exc)}")
                else:
                    message = f"最终 Markdown 已保存{_backup_summary(backups)}"
                    if archived:
                        message += f"；已归档：{archived.note_path}"
                    if knowledge_results:
                        message += f"；同步 {len(knowledge_results)} 条技巧"
                    if knowledge_error:
                        message += f"；技巧同步失败：{_clean_error(knowledge_error)}"
                    _rerun_with_notice(st, message)

            st.markdown("#### transcript 修正")
            transcript_text = _read_text(selected.transcript_path)
            edited_transcript = st.text_area(
                "transcript.json",
                value=transcript_text,
                height=240,
                key=f"{state_prefix}transcript",
            )
            if st.button(
                "保存 transcript 并重新抽取菜谱",
                disabled=not selected.transcript_path or not confirm_overwrite,
                key=f"{state_prefix}save_transcript",
            ):
                try:
                    json.loads(edited_transcript)
                    backups = _backup_files(
                        [selected.transcript_path, selected.recipe_path, selected.note_path],
                        "transcript-edit",
                    )
                    atomic_write_text(selected.transcript_path, edited_transcript)  # type: ignore[arg-type]
                    result = regenerate_recipe_from_transcript(selected.output_folder)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"重新抽取失败：{_clean_error(exc)}")
                else:
                    _rerun_with_notice(
                        st,
                        f"已重新生成：{result.note_path}{_backup_summary(backups)}",
                        clear_prefix=state_prefix,
                    )

            st.markdown("#### 截图重截")
            steps = recipe_data.get("steps", []) if recipe_data else []
            if not isinstance(steps, list) or not steps:
                st.info("当前菜谱没有可重截的步骤。")
            else:
                step_indexes = list(range(1, len(steps) + 1))
                step_index = st.selectbox(
                    "选择步骤",
                    step_indexes,
                    format_func=lambda index: f"{index}. {steps[index - 1].get('title') or '未命名步骤'}",
                    key=f"{state_prefix}screenshot_step",
                )
                current_step = steps[int(step_index) - 1]
                initial_timestamp = _nonnegative_float(current_step.get("start_time"))
                duration = _transcript_duration(selected.transcript_path)
                known_times = [
                    _nonnegative_float(value)
                    for step in steps
                    if isinstance(step, dict)
                    for value in (step.get("start_time"), step.get("end_time"))
                    if isinstance(value, (int, float))
                ]
                max_timestamp = max([initial_timestamp, duration or 0.0, *known_times], default=initial_timestamp)
                col_time, col_video = st.columns([1, 3])
                with col_time:
                    timestamp = st.number_input(
                        "时间点（秒）",
                        min_value=0.0,
                        max_value=max_timestamp if max_timestamp > 0 else None,
                        value=initial_timestamp,
                        step=0.5,
                        key=f"{state_prefix}screenshot_time_{step_index}",
                    )
                with col_video:
                    video_path = st.text_input(
                        "视频文件路径（可留空自动下载/复用 media）",
                        key=f"{state_prefix}screenshot_video",
                    )
                end_time = current_step.get("end_time")
                available_range = (
                    f"视频可用范围 0–{max_timestamp:.1f} 秒"
                    if max_timestamp > 0
                    else "未能读取视频总时长，请输入非负时间点"
                )
                if isinstance(end_time, (int, float)):
                    st.caption(
                        f"当前步骤约为 {initial_timestamp:.1f}–{_nonnegative_float(end_time):.1f} 秒；"
                        f"{available_range}。"
                    )
                else:
                    st.caption(f"当前步骤从 {initial_timestamp:.1f} 秒开始；{available_range}。")
                if st.button(
                    "重新截图",
                    disabled=not confirm_overwrite,
                    key=f"{state_prefix}recapture",
                ):
                    try:
                        target_image = selected.output_folder / "images" / f"step_{int(step_index):02d}.jpg"
                        backups = _backup_files(
                            [target_image, selected.recipe_path, selected.note_path],
                            "recapture",
                        )
                        image_path = recapture_step_screenshot(
                            selected.output_folder,
                            int(step_index),
                            float(timestamp),
                            cookies=_optional_text(config.cookies),
                            video_path=_optional_text(video_path),
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"截图失败：{_clean_error(exc)}")
                    else:
                        _rerun_with_notice(
                            st,
                            f"已更新：{image_path}{_backup_summary(backups)}",
                            clear_prefix=state_prefix,
                        )

    if active_page == "知识库":
        st.subheader("个人厨艺知识库")
        kb_path = knowledge_base_path()
        all_entries = load_knowledge_entries()
        categories = sorted({entry.category for entry in all_entries})
        col_query, col_category = st.columns([3, 1])
        with col_query:
            knowledge_query = st.text_input("搜索知识", placeholder="火候、去腥、乳化、焯水...", key="kb_query")
        with col_category:
            knowledge_category = st.selectbox("分类", [""] + categories, key="kb_category")
        entries = search_knowledge_entries(knowledge_query, knowledge_category)
        st.caption(f"共 {len(entries)} 条，存储位置：{kb_path}")
        if entries:
            st.dataframe(
                [
                    {
                        "标题": entry.title,
                        "分类": entry.category,
                        "状态": "已确认干货" if entry.review_status == "approved" else "AI 候选",
                        "标签": ", ".join(entry.tags),
                        "来源": entry.source_title,
                        "更新时间": entry.updated_at,
                    }
                    for entry in entries
                ],
                width="stretch",
            )
            entry_options = {
                f"{entry.title} | {entry.category} | {entry.id[:8]}": entry
                for entry in entries
            }
            selected_entry = entry_options[st.selectbox("查看知识", list(entry_options), key="kb_entry")]
            entry_state_prefix = f"kb_edit_{selected_entry.id}_"
            col_view, col_review = st.columns([2, 1])
            with col_view:
                st.markdown(f"#### {selected_entry.title}")
                st.markdown(selected_entry.content)
                if selected_entry.rationale:
                    st.markdown("#### 原理")
                    st.markdown(selected_entry.rationale)
                if selected_entry.applicable_to:
                    st.markdown("#### 适用场景")
                    st.markdown("\n".join(f"- {item}" for item in selected_entry.applicable_to))
                if selected_entry.evidence:
                    st.markdown("#### 视频依据")
                    st.markdown(selected_entry.evidence)
                st.code(
                    "\n".join(
                        item
                        for item in [
                            f"来源标题：{selected_entry.source_title}",
                            f"来源 URL：{selected_entry.source_url}",
                            f"输出目录：{selected_entry.source_output_folder}",
                            f"掌握状态：{selected_entry.mastery}",
                            f"下次复习：{selected_entry.next_review_at}",
                        ]
                        if item.split("：", 1)[1]
                    ),
                    language="text",
                )
            with col_review:
                st.markdown("#### 收录状态")
                if selected_entry.review_status == "approved":
                    st.success("已确认，可同步到 Obsidian 笔记本")
                    if st.button("退回候选", key=f"kb_unapprove_{selected_entry.id}"):
                        update_knowledge_entry(selected_entry.id, {"review_status": "draft"})
                        _rerun_with_notice(st, "已退回 AI 候选。")
                else:
                    st.warning("AI 候选，确认内容有价值后再收录")
                    if st.button("确认并收录为干货", type="primary", key=f"kb_approve_{selected_entry.id}"):
                        update_knowledge_entry(selected_entry.id, {"review_status": "approved"})
                        _rerun_with_notice(st, "已确认该技巧为干货。")
                st.markdown("#### 快速复习")
                for label in ["已掌握", "还模糊", "需要实践"]:
                    if st.button(label, key=f"kb_review_{label}_{selected_entry.id}"):
                        try:
                            reviewed = record_knowledge_review(selected_entry.id, label)
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"复习记录失败：{_clean_error(exc)}")
                        else:
                            _rerun_with_notice(st, f"已记录复习状态：{reviewed.mastery}")

            with st.expander("编辑当前知识"):
                edit_title = st.text_input("标题", value=selected_entry.title, key=f"{entry_state_prefix}title")
                edit_category = st.text_input("分类", value=selected_entry.category, key=f"{entry_state_prefix}category")
                edit_content = st.text_area(
                    "内容",
                    value=selected_entry.content,
                    height=100,
                    key=f"{entry_state_prefix}content",
                )
                edit_rationale = st.text_area(
                    "原理",
                    value=selected_entry.rationale,
                    height=80,
                    key=f"{entry_state_prefix}rationale",
                )
                edit_applicable = st.text_area(
                    "适用场景，每行一个",
                    value="\n".join(selected_entry.applicable_to),
                    height=80,
                    key=f"{entry_state_prefix}applicable",
                )
                edit_tags = st.text_input(
                    "标签，用逗号分隔",
                    value=", ".join(selected_entry.tags),
                    key=f"{entry_state_prefix}tags",
                )
                edit_evidence = st.text_area(
                    "视频依据",
                    value=selected_entry.evidence,
                    height=80,
                    key=f"{entry_state_prefix}evidence",
                )
                col_save, col_delete = st.columns(2)
                with col_save:
                    if st.button("保存知识", key=f"kb_save_{selected_entry.id}"):
                        try:
                            backups = _backup_files([kb_path], "knowledge-edit")
                            update_knowledge_entry(
                                selected_entry.id,
                                {
                                    "title": edit_title,
                                    "category": edit_category,
                                    "content": edit_content,
                                    "rationale": edit_rationale,
                                    "applicable_to": [
                                        line.strip()
                                        for line in edit_applicable.splitlines()
                                        if line.strip()
                                    ],
                                    "tags": [item.strip() for item in edit_tags.split(",") if item.strip()],
                                    "evidence": edit_evidence,
                                },
                            )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"保存失败：{_clean_error(exc)}")
                        else:
                            _rerun_with_notice(
                                st,
                                f"已保存知识条目{_backup_summary(backups)}",
                                clear_prefix=entry_state_prefix,
                            )
                with col_delete:
                    confirm_delete = st.checkbox(
                        "确认删除",
                        key=f"kb_confirm_delete_{selected_entry.id}",
                    )
                    if st.button(
                        "删除知识",
                        disabled=not confirm_delete,
                        key=f"kb_delete_{selected_entry.id}",
                    ):
                        try:
                            backups = _backup_files([kb_path], "knowledge-delete")
                            delete_knowledge_entry(selected_entry.id)
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"删除失败：{_clean_error(exc)}")
                        else:
                            _rerun_with_notice(st, f"已删除知识条目{_backup_summary(backups)}")

            with st.expander("实践记录"):
                if selected_entry.practice_records:
                    st.dataframe(selected_entry.practice_records, width="stretch")
                practice_dish = st.text_input("实践菜品", key=f"kb_practice_dish_{selected_entry.id}")
                practice_outcome = st.selectbox(
                    "结果",
                    ["成功", "一般", "翻车", "待观察"],
                    key=f"kb_practice_outcome_{selected_entry.id}",
                )
                practice_photo = st.text_input("成品照片路径", key=f"kb_practice_photo_{selected_entry.id}")
                practice_notes = st.text_area("实践记录", height=90, key=f"kb_practice_notes_{selected_entry.id}")
                if st.button("添加实践记录", key=f"kb_add_practice_{selected_entry.id}"):
                    try:
                        add_practice_record(
                            selected_entry.id,
                            dish=practice_dish,
                            outcome=practice_outcome,
                            notes=practice_notes,
                            photo_path=practice_photo,
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"实践记录失败：{_clean_error(exc)}")
                    else:
                        _rerun_with_notice(st, "已添加实践记录")
        else:
            st.info("知识库还没有匹配内容。")

        st.markdown("#### 复习队列")
        due_entries = due_review_entries(limit=8)
        if due_entries:
            st.dataframe(
                [
                    {
                        "标题": entry.title,
                        "分类": entry.category,
                        "掌握状态": entry.mastery,
                        "复习次数": entry.review_count,
                        "下次复习": entry.next_review_at or "现在",
                    }
                    for entry in due_entries
                ],
                width="stretch",
            )
        else:
            st.caption("暂时没有需要复习的知识。")

        st.markdown("#### 去重合并")
        duplicate_groups = suggest_duplicate_groups()
        if duplicate_groups:
            group_options = {
                f"{' / '.join(entry.title for entry in group[:3])} | {group[0].id[:8]}": group
                for group in duplicate_groups
            }
            selected_group = group_options[st.selectbox("相似条目组", list(group_options), key="kb_duplicate_group")]
            merge_group_key = hashlib.sha256(
                "|".join(entry.id for entry in selected_group).encode("utf-8")
            ).hexdigest()[:12]
            st.dataframe(
                [
                    {
                        "ID": entry.id,
                        "标题": entry.title,
                        "分类": entry.category,
                        "内容": entry.content,
                        "来源数": len(entry.source_refs),
                    }
                    for entry in selected_group
                ],
                width="stretch",
            )
            confirm_merge = st.checkbox(
                "确认合并并删除其余条目",
                key=f"kb_confirm_merge_{merge_group_key}",
            )
            st.caption("合并会保留第一条并删除组内其余条目；执行前会备份知识库。")
            if st.button(
                "合并到第一条",
                disabled=not confirm_merge,
                key=f"kb_merge_duplicates_{merge_group_key}",
            ):
                try:
                    backups = _backup_files([kb_path], "knowledge-merge")
                    merged = merge_knowledge_entries(selected_group[0].id, [entry.id for entry in selected_group[1:]])
                except Exception as exc:  # noqa: BLE001
                    st.error(f"合并失败：{_clean_error(exc)}")
                else:
                    _rerun_with_notice(st, f"已合并：{merged.title}{_backup_summary(backups)}")
        else:
            st.caption("没有发现明显重复条目。")

        st.markdown("#### 导出")
        export_kind = st.selectbox("知识库导出格式", ["markdown", "csv", "anki"], key="kb_export_kind")
        export_current_category = st.checkbox("仅导出当前分类", value=bool(knowledge_category), key="kb_export_category")
        if st.button("导出知识库", key="kb_export"):
            try:
                exported = export_knowledge_base(
                    export_kind,
                    category=knowledge_category if export_current_category else "",
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"导出失败：{_clean_error(exc)}")
            else:
                st.success(f"已导出：{exported}")

        st.markdown("#### 同步干货到 Obsidian")
        approved_entries = [entry for entry in all_entries if entry.review_status == "approved"]
        draft_entries = [entry for entry in all_entries if entry.review_status != "approved"]
        st.caption(
            f"已确认干货 {len(approved_entries)} 条，AI 候选 {len(draft_entries)} 条；"
            "只同步已确认内容，证据和置信度不会写入最终笔记。"
        )
        force_knowledge_overwrite = st.checkbox(
            "确认覆盖 Vault 中技巧笔记的手写修改",
            key="kb_force_obsidian_overwrite",
        )
        if st.button("同步已确认干货", disabled=not approved_entries, key="kb_sync_obsidian"):
            try:
                synced = archive_knowledge(
                    approved_entries,
                    _vault_path(config),
                    conflict="overwrite" if force_knowledge_overwrite else "update",
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"同步失败：{_clean_error(exc)}")
            else:
                st.success(f"已同步 {len(synced)} 条技巧到 {_vault_path(config) / '烹饪技巧'}")

        st.markdown("#### 从历史视频提取")
        items = scan_history(config.out_dir)
        analyzable = [item for item in items if item.note_path or item.transcript_path or item.recipe_path]
        if not analyzable:
            st.info("没有可提取知识的视频记录。")
        else:
            video_options = _history_options(analyzable)
            selected_video = video_options[st.selectbox("选择视频", list(video_options), key="kb_video_select")]
            col_one, col_all = st.columns(2)
            with col_one:
                run_single_extract = st.button("提取并写入知识库", type="primary", disabled=config.llm_provider == "none")
            with col_all:
                run_batch_extract = st.button("批量提取全部历史视频", disabled=config.llm_provider == "none")
            if run_single_extract:
                try:
                    with st.spinner("正在提取通用烹饪知识..."):
                        result = extract_knowledge_from_video(
                            selected_video.output_folder,
                            options=_knowledge_extraction_options(config),
                        )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"知识提取失败：{_clean_error(exc)}")
                else:
                    _rerun_with_notice(
                        st,
                        f"已写入：{result.knowledge_path}，新增 {result.added_count} 条，更新 {result.updated_count} 条"
                    )
            if run_batch_extract:
                try:
                    with st.spinner("正在批量沉淀知识..."):
                        batch_result = extract_knowledge_from_folders(
                            [item.output_folder for item in analyzable],
                            options=_knowledge_extraction_options(config),
                            skip_existing=True,
                        )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"批量提取失败：{_clean_error(exc)}")
                else:
                    _rerun_with_notice(
                        st,
                        f"批量完成：新增 {batch_result.added_count} 条，更新 {batch_result.updated_count} 条，"
                        f"跳过 {batch_result.skipped_count} 个，失败 {batch_result.failed_count} 个"
                    )

            st.markdown("#### 关联到菜谱")
            try:
                related = related_knowledge_for_recipe(selected_video.output_folder)
            except Exception as exc:  # noqa: BLE001 - one damaged recipe must not break the knowledge page
                related = []
                st.error(f"读取关联知识失败：{_clean_error(exc)}")
            if related:
                st.dataframe(
                    [
                        {
                            "标题": entry.title,
                            "分类": entry.category,
                            "内容": entry.content,
                        }
                        for entry in related
                    ],
                    width="stretch",
                )
            else:
                st.caption("没有找到明显相关的知识条目。")
            confirm_related_write = st.checkbox(
                "确认覆盖所选视频的 note.md",
                key=f"kb_confirm_related_{_record_key(selected_video.output_folder)}",
            )
            st.caption("写入会修改 note.md；执行前会创建带时间戳的备份。")
            if st.button(
                "写入相关知识到 note.md",
                disabled=not confirm_related_write,
                key=f"kb_write_related_{_record_key(selected_video.output_folder)}",
            ):
                try:
                    backups = _backup_files([selected_video.note_path], "related-knowledge")
                    note_path = write_related_knowledge_to_note(selected_video.output_folder)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"写入失败：{_clean_error(exc)}")
                else:
                    _rerun_with_notice(
                        st,
                        f"已更新：{note_path}{_backup_summary(backups)}",
                        clear_prefix=f"kb_confirm_related_{_record_key(selected_video.output_folder)}",
                    )

    if active_page == "二次分析":
        st.subheader("LLM 二次分析")
        items = scan_history(config.out_dir)
        analyzable = [item for item in items if item.note_path or item.transcript_path or item.recipe_path]
        if not analyzable:
            st.info("没有可分析的视频记录。")
        else:
            options = _history_options(analyzable)
            selected = options[st.selectbox("选择视频", list(options), key="analysis_select")]
            request = st.text_area(
                "想额外分析的内容",
                value="提取视频中提到的通用烹饪技巧",
                height=100,
                key="analysis_request",
            )
            output_filename = st.text_input("输出文件名", value="extra_analysis.md")
            existing_analysis = selected.output_folder / _markdown_filename(output_filename)
            if existing_analysis.exists():
                st.markdown("#### 已有分析")
                st.markdown(existing_analysis.read_text(encoding="utf-8"))
            if st.button("开始二次分析", type="primary", disabled=config.llm_provider == "none"):
                try:
                    with st.spinner("正在分析视频内容..."):
                        result = analyze_video_content(
                            selected.output_folder,
                            request=request,
                            options=_content_analysis_options(config, output_filename),
                        )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"二次分析失败：{_clean_error(exc)}")
                else:
                    st.success(f"已生成：{result.analysis_path}")
                    st.markdown("#### 分析结果")
                    st.markdown(result.markdown)
                    st.download_button(
                        "下载分析 Markdown",
                        data=result.markdown,
                        file_name=result.analysis_path.name,
                        mime="text/markdown",
                    )

    if active_page == "环境检查":
        st.subheader("环境检查")
        st.caption("环境探测可能较慢，仅在点击按钮时执行；其他页面输入不会触发检查。")
        if st.button("运行环境检查", type="primary", key="run_environment_checks"):
            try:
                st.session_state["environment_check_results"] = run_environment_checks()
            except Exception as exc:  # noqa: BLE001
                st.error(f"环境检查失败：{_clean_error(exc)}")
        checks = st.session_state.get("environment_check_results")
        if checks:
            st.dataframe(
                [
                    {
                        "项目": check.name,
                        "状态": "OK" if check.ok else "需要处理",
                        "详情": check.detail,
                        "建议": check.suggestion or "",
                    }
                    for check in checks
                ],
                width="stretch",
            )
        else:
            st.info("点击“运行环境检查”开始探测。")

    if active_page == "UP 主链接":
        st.subheader("提取 UP 主视频链接")
        home_url = st.text_input("UP 主主页 URL", placeholder="https://space.bilibili.com/123456/video")
        filename = st.text_input("链接文件名", value="creator_video_links.txt")
        if st.button("开始提取", type="primary", disabled=not home_url.strip()):
            log = _log_box(st, height=180)
            try:
                with st.spinner("正在提取视频链接..."):
                    links_path = extract_creator_links(
                        url=home_url.strip(),
                        cookies=_optional_text(config.cookies),
                        out=config.out_dir,
                        filename=filename.strip() or "creator_video_links.txt",
                        log=log,
                    )
            except Exception as exc:  # noqa: BLE001
                error_message = _clean_error(exc)
                log(error_message)
                st.error(f"提取失败：{error_message}")
            else:
                try:
                    if not links_path.is_file():
                        raise FileNotFoundError(f"提取器未生成链接文件：{links_path}")
                    content = links_path.read_text(encoding="utf-8")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"读取链接文件失败：{_clean_error(exc)}")
                else:
                    st.success(f"已写入：{links_path}")
                    st.text_area("链接预览", value=content, height=240)
                    st.download_button("下载链接文件", data=content, file_name=links_path.name, mime="text/plain")


if __name__ == "__main__":
    main()
