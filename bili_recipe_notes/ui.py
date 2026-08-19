from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote
from urllib.request import urlopen

try:
    from .batch_queue import create_batch_id, create_batch_state, list_batch_states
    from .batch_runner import get_background_batch_status, read_batch_log, start_background_batch
    from .config import UIConfig, load_config, save_config
    from .content_analysis import ContentAnalysisOptions, analyze_video_content
    from .curation import (
        CURATION_DECISION_VALUES,
        DEFAULT_CURATION_REVIEW_DIR,
        build_curation_review,
        curation_decision_conflicts,
        load_curation_decisions,
        load_curation_review,
        save_curation_decision,
        save_curation_decisions,
        suggested_curation_decision,
    )
    from .cooking_mode import (
        build_shopping_list,
        parse_servings,
        serving_scale,
        shopping_list_markdown,
    )
    from .environment import run_environment_checks
    from .downloader import import_edge_cookies, remove_imported_cookies, validate_bilibili_cookie_file
    from .exports import export_note
    from .history import HistoryItem, scan_history
    from .handoff import export_batch_handoff, import_handoff_bundle
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
    from .mobile_sync import MobileSyncStore
    from .meal_order_component import render_meal_order_component
    from .meal_plans import (
        MealCandidate,
        MealPlanItem,
        delete_meal_plan,
        load_meal_plans,
        meal_candidate_kind,
        record_meal_plan_practice,
        save_meal_plan,
    )
    from .markdown_writer import upsert_rating_block
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
        crawl_and_archive_creator,
        generate_recipe_note,
        recapture_step_screenshot,
        regenerate_note_from_recipe,
        regenerate_recipe_from_transcript,
        save_step_screenshot_candidate,
        save_uploaded_step_screenshot,
        clear_step_screenshot,
        suggest_step_screenshots,
    )
    from .quality import analyze_recipe_quality
    from .recipe_extractor import (
        RECIPE_CATEGORIES,
        RECIPE_CUISINES,
        Recipe,
        condense_recipe_steps,
        normalize_recipe_taxonomy,
        rating_stars,
    )
    from .recipe_review import (
        accept_all_pending_review_items,
        create_recipe_review,
        decide_review_item,
        load_recipe_review,
        recipe_from_completed_review,
        review_path,
    )
    from .storage import atomic_write_json, atomic_write_text
    from .web_export import build_web_library_payload, web_library_bytes
except ImportError:  # pragma: no cover - supports direct streamlit script execution
    from bili_recipe_notes.batch_queue import create_batch_id, create_batch_state, list_batch_states
    from bili_recipe_notes.batch_runner import get_background_batch_status, read_batch_log, start_background_batch
    from bili_recipe_notes.config import UIConfig, load_config, save_config
    from bili_recipe_notes.content_analysis import ContentAnalysisOptions, analyze_video_content
    from bili_recipe_notes.curation import (
        CURATION_DECISION_VALUES,
        DEFAULT_CURATION_REVIEW_DIR,
        build_curation_review,
        curation_decision_conflicts,
        load_curation_decisions,
        load_curation_review,
        save_curation_decision,
        save_curation_decisions,
        suggested_curation_decision,
    )
    from bili_recipe_notes.cooking_mode import (
        build_shopping_list,
        parse_servings,
        serving_scale,
        shopping_list_markdown,
    )
    from bili_recipe_notes.environment import run_environment_checks
    from bili_recipe_notes.downloader import import_edge_cookies, remove_imported_cookies, validate_bilibili_cookie_file
    from bili_recipe_notes.exports import export_note
    from bili_recipe_notes.history import HistoryItem, scan_history
    from bili_recipe_notes.handoff import export_batch_handoff, import_handoff_bundle
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
    from bili_recipe_notes.mobile_sync import MobileSyncStore
    from bili_recipe_notes.meal_order_component import render_meal_order_component
    from bili_recipe_notes.meal_plans import (
        MealCandidate,
        MealPlanItem,
        delete_meal_plan,
        load_meal_plans,
        meal_candidate_kind,
        record_meal_plan_practice,
        save_meal_plan,
    )
    from bili_recipe_notes.markdown_writer import upsert_rating_block
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
        crawl_and_archive_creator,
        generate_recipe_note,
        recapture_step_screenshot,
        regenerate_note_from_recipe,
        regenerate_recipe_from_transcript,
        save_step_screenshot_candidate,
        save_uploaded_step_screenshot,
        clear_step_screenshot,
        suggest_step_screenshots,
    )
    from bili_recipe_notes.quality import analyze_recipe_quality
    from bili_recipe_notes.recipe_extractor import (
        RECIPE_CATEGORIES,
        RECIPE_CUISINES,
        Recipe,
        condense_recipe_steps,
        normalize_recipe_taxonomy,
        rating_stars,
    )
    from bili_recipe_notes.recipe_review import (
        accept_all_pending_review_items,
        create_recipe_review,
        decide_review_item,
        load_recipe_review,
        recipe_from_completed_review,
        review_path,
    )
    from bili_recipe_notes.storage import atomic_write_json, atomic_write_text
    from bili_recipe_notes.web_export import build_web_library_payload, web_library_bytes


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
    "任务仪表盘",
    "菜谱库全览",
    "菜谱详情",
    "本餐点菜",
    "烹饪模式",
    "草稿与归档",
    "审核确认",
    "最终菜谱整理",
    "批量处理",
    "工作交接",
    "编辑修复",
    "知识库",
    "二次分析",
    "环境检查",
    "手机客户端",
    "UP 主链接",
]
PAGE_GROUPS = {
    "采集与生成": ["任务仪表盘", "单视频生成", "批量处理", "UP 主链接"],
    "审阅与成稿": ["草稿与归档", "审核确认", "编辑修复", "最终菜谱整理"],
    "使用与知识": ["菜谱库全览", "菜谱详情", "本餐点菜", "烹饪模式", "知识库", "二次分析", "手机客户端"],
    "系统与迁移": ["工作交接", "环境检查"],
}
PAGE_GROUP_BY_PAGE = {
    page: group
    for group, pages in PAGE_GROUPS.items()
    for page in pages
}
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
LARGE_TABLE_PAGE_SIZE = 50
CURATION_DECISION_LABELS = {
    "pending": "未决定",
    "keep_primary": "保留为主版本",
    "keep_variant": "保留为不同做法",
    "merge_clip": "并入长版，不单独保留",
    "exclude": "排除（推广/非菜谱）",
    "review": "仍需进一步核对",
}
CURATION_ROLE_LABELS = {
    "primary_candidate": "建议主版本",
    "variant_candidate": "建议保留变体",
    "short_clip_candidate": "疑似短剪",
    "exclude_candidate": "建议排除",
    "name_review_candidate": "名称待确认",
}


def _optional_text(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _clean_error(exc: Exception) -> str:
    return ANSI_RE.sub("", str(exc)).strip()


def _render_paths(paths: list[Path]) -> str:
    return "\n".join(str(path) for path in paths)


def _paged_values(st, values: list[Any], *, key: str, page_size: int = LARGE_TABLE_PAGE_SIZE) -> tuple[list[Any], int]:
    """Limit each Arrow table conversion to a small, predictable page."""

    if len(values) <= page_size:
        return values, 0
    page_count = math.ceil(len(values) / page_size)
    page = st.selectbox(
        "表格页码",
        list(range(page_count)),
        format_func=lambda index: f"第 {index + 1}/{page_count} 页",
        key=key,
    )
    start = int(page) * page_size
    st.caption(f"当前显示第 {start + 1}–{min(start + page_size, len(values))} 条，共 {len(values)} 条。")
    return values[start : start + page_size], start


def _stabilize_arrow_memory_pool() -> None:
    """Avoid the mimalloc path implicated in native Arrow crashes on macOS/Python 3.14."""

    try:
        import pyarrow as pa

        pa.set_memory_pool(pa.system_memory_pool())
    except Exception:
        pass


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


def _saved_creator_link_documents(out_dir: str | Path) -> list[Path]:
    root = Path(out_dir).expanduser()
    documents = [path for path in (root / "creators").glob("*/video_links.txt") if path.is_file()]
    return sorted(documents, key=lambda path: path.stat().st_mtime, reverse=True)


def _creator_link_document_label(path_value: str | Path | None) -> str:
    if not path_value:
        return "不使用已保存清单"
    path = Path(path_value)
    uploader = path.parent.name
    count = 0
    manifest_path = path.parent / "creator.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        manifest = {}
    if isinstance(manifest, dict):
        uploader = str(manifest.get("uploader") or uploader)
        count = int(manifest.get("video_count") or 0)
    if count <= 0:
        try:
            count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            count = 0
    return f"{uploader} | {count} 条 | {path}"


def _load_batch_urls(
    links_text: str,
    links_file: str,
    saved_creator_links: str | Path | None = None,
) -> list[str]:
    urls = [line.strip() for line in links_text.splitlines() if line.strip()]
    file_values = [str(saved_creator_links or "").strip(), links_file.strip()]
    for file_value in file_values:
        if not file_value:
            continue
        path = Path(file_value).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"链接文件不存在：{path}")
        if not path.is_file():
            raise IsADirectoryError(f"链接文件路径不是文件：{path}")
        urls.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return list(dict.fromkeys(urls))


BATCH_STATUS_LABELS = {
    "pending": "待执行",
    "running": "处理中",
    "raw_running": "原始版处理中",
    "raw_ready": "原始版就绪",
    "recipe_running": "菜谱处理中",
    "done": "已完成",
    "skipped": "已跳过",
    "failed": "失败",
}


def _batch_item_row(item: Any) -> dict[str, Any]:
    stages = getattr(item, "stages", {}) or {}

    def stage_status(name: str) -> str:
        stage = stages.get(name)
        raw = getattr(stage, "status", "pending") if stage else "pending"
        return BATCH_STATUS_LABELS.get(raw, raw)

    status = str(getattr(item, "status", "pending"))
    return {
        "URL": getattr(item, "url", ""),
        "状态": BATCH_STATUS_LABELS.get(status, status),
        "原始版": stage_status("raw"),
        "菜谱版": stage_status("recipe"),
        "输出目录": getattr(item, "output_folder", None) or "",
        "错误": getattr(item, "error", None) or "",
    }


def _batch_progress_summary(state: Any) -> dict[str, int]:
    completed = failed = running = 0
    for item in state.items:
        status = str(getattr(item, "status", "pending") or "pending")
        if status in {"done", "skipped"}:
            completed += 1
        elif status == "failed":
            failed += 1
        elif status.endswith("_running") or status == "running":
            running += 1
    total = len(state.items)
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "running": running,
        "pending": max(0, total - completed - failed - running),
    }


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "暂无法估算"
    total_minutes = max(0, math.ceil(float(seconds) / 60))
    if total_minutes < 1:
        return "不到 1 分钟"
    hours, minutes = divmod(total_minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days} 天 {hours} 小时" if hours else f"{days} 天"
    if hours:
        return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"
    return f"{minutes} 分钟"


def _batch_dashboard_summary(
    state: Any,
    runtime: Any | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    counts = _batch_progress_summary(state)
    processed = counts["completed"] + counts["failed"]
    remaining = counts["total"] - processed
    status = str(getattr(runtime, "status", "history") or "history")
    started_at = _parse_timestamp(getattr(runtime, "started_at", None))
    finished_at = _parse_timestamp(getattr(runtime, "finished_at", None))
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    elapsed_end = finished_at or current_time
    elapsed_seconds = (
        max(0.0, (elapsed_end - started_at).total_seconds())
        if started_at is not None
        else None
    )
    speed_per_hour = (
        processed / elapsed_seconds * 3600
        if elapsed_seconds and processed
        else None
    )
    eta_seconds = None
    estimated_finish = None
    if status == "running" and elapsed_seconds and processed and remaining > 0:
        eta_seconds = elapsed_seconds / processed * remaining
        estimated_finish = current_time + timedelta(seconds=eta_seconds)
    return {
        **counts,
        "processed": processed,
        "remaining": remaining,
        "progress": processed / counts["total"] if counts["total"] else 1.0,
        "status": status,
        "elapsed_seconds": elapsed_seconds,
        "speed_per_hour": speed_per_hour,
        "eta_seconds": eta_seconds,
        "estimated_finish": estimated_finish,
    }


def _preferred_batch_id(
    batch_states: list[Any],
    runtime_statuses: dict[str, Any],
    requested: str | None,
    current: str | None,
) -> str | None:
    available = {str(state.batch_id) for state in batch_states}
    for value in (requested, current):
        normalized = str(value or "").split(" | ", 1)[0]
        if normalized in available:
            return normalized
    running = sorted(
        (
            str(state.batch_id)
            for state in batch_states
            if runtime_statuses.get(str(state.batch_id))
            and runtime_statuses[str(state.batch_id)].status == "running"
        ),
        key=lambda batch_id: str(getattr(runtime_statuses[batch_id], "started_at", "")),
        reverse=True,
    )
    return running[0] if running else (str(batch_states[0].batch_id) if batch_states else None)


def _running_batch_overlaps(
    urls: list[str],
    running_batch_ids: list[str],
    batch_by_id: dict[str, Any],
) -> list[tuple[str, int]]:
    requested = {url.strip() for url in urls if url.strip()}
    overlaps = []
    for batch_id in running_batch_ids:
        state = batch_by_id.get(batch_id)
        if not state:
            continue
        overlap_count = len(requested.intersection(item.url for item in state.items))
        if overlap_count:
            overlaps.append((batch_id, overlap_count))
    return overlaps


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


def _rating_option_label(value: int | None) -> str:
    return "未评分" if value is None else rating_stars(value)


def _recipe_with_inferred_ratings(recipe_path: Path) -> Recipe:
    recipe = _validate_recipe(_recipe_to_data(recipe_path))
    return normalize_recipe_taxonomy(recipe)


def _render_rating_controls(
    st,
    recipe_path: Path,
    *,
    key_prefix: str,
) -> dict[str, int | None] | None:
    try:
        recipe = _recipe_with_inferred_ratings(recipe_path)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"评级暂不可用：{_clean_error(exc)}")
        return None

    st.markdown("##### 归档评级")
    st.caption("喜爱度由你填写；难度和时间由系统先评估，1 星最低、5 星最高，均可修改。")
    col_taste, col_difficulty, col_time = st.columns(3)
    with col_taste:
        taste = st.selectbox(
            "个人喜爱度",
            [None, 1, 2, 3, 4, 5],
            index=recipe.taste_rating or 0,
            format_func=_rating_option_label,
            key=f"{key_prefix}taste_rating",
            help="按自己的喜欢程度评分；可以暂时不评分。",
        )
    with col_difficulty:
        difficulty = st.selectbox(
            "烹饪难度评级",
            [1, 2, 3, 4, 5],
            index=max(0, int(recipe.difficulty_rating or 1) - 1),
            format_func=_rating_option_label,
            key=f"{key_prefix}difficulty_rating",
            help="1 星很简单，5 星很难。系统根据步骤和技法自动给出初值。",
        )
    with col_time:
        time_rating = st.selectbox(
            "时间投入评级",
            [1, 2, 3, 4, 5],
            index=max(0, int(recipe.time_rating or 1) - 1),
            format_func=_rating_option_label,
            key=f"{key_prefix}time_rating",
            help="1 星约 15 分钟内，5 星通常超过 2 小时。系统根据总耗时自动给出初值。",
        )
    return {
        "taste_rating": taste,
        "difficulty_rating": int(difficulty),
        "time_rating": int(time_rating),
    }


def _inject_workspace_styles(st) -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] [data-testid="stExpander"] {
          border-color: color-mix(in srgb, currentColor 18%, transparent);
        }
        .brn-action-rail-marker { display: none; }
        @media (min-width: 900px) {
          div[data-testid="stColumn"]:has(.brn-action-rail-marker) {
            position: sticky;
            top: 3.75rem;
            align-self: flex-start;
            max-height: calc(100vh - 4.5rem);
            overflow-y: auto;
            padding: 0.2rem 0.75rem 0.75rem;
            border-left: 1px solid color-mix(in srgb, currentColor 16%, transparent);
            scrollbar-width: thin;
          }
        }
        @media (max-width: 899px) {
          div[data-testid="stColumn"]:has(.brn-action-rail-marker) {
            border-top: 1px solid color-mix(in srgb, currentColor 16%, transparent);
            padding-top: 1rem;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _action_rail_marker(st) -> None:
    st.markdown('<span class="brn-action-rail-marker"></span>', unsafe_allow_html=True)


def _render_navigation(st) -> str:
    if st.session_state.get("main_page") not in PAGES:
        st.session_state["main_page"] = PAGES[0]

    st.sidebar.header("工作区")
    st.sidebar.caption("按实际流程分区；当前页面所在分区会自动展开。")
    current_page = str(st.session_state["main_page"])

    def select_page(page: str) -> None:
        st.session_state["main_page"] = page

    for group, pages in PAGE_GROUPS.items():
        with st.sidebar.expander(group, expanded=current_page in pages):
            for page in pages:
                st.button(
                    page,
                    key=f"nav_{page}",
                    type="primary" if page == current_page else "secondary",
                    use_container_width=True,
                    on_click=select_page,
                    args=(page,),
                )

    st.sidebar.divider()
    with st.sidebar.expander("全部功能快速切换", expanded=False):
        return st.selectbox(
            "当前功能",
            PAGES,
            key="main_page",
            format_func=lambda page: f"{PAGE_GROUP_BY_PAGE[page]} · {page}",
        )


def _save_recipe_ratings(output_folder: Path, ratings: dict[str, int | None] | None = None) -> Recipe:
    recipe_path = output_folder / "recipe.json"
    note_path = output_folder / "note.md"
    original_data = _recipe_to_data(recipe_path)
    recipe = normalize_recipe_taxonomy(_validate_recipe(original_data))
    if ratings is not None:
        recipe.taste_rating = ratings.get("taste_rating")
        recipe.difficulty_rating = ratings.get("difficulty_rating")
        recipe.time_rating = ratings.get("time_rating")
        recipe = normalize_recipe_taxonomy(recipe)
    updated_data = _dump_model(recipe)
    if updated_data != original_data:
        atomic_write_json(recipe_path, updated_data)
    if note_path.is_file():
        current = note_path.read_text(encoding="utf-8")
        updated = upsert_rating_block(current, recipe)
        if updated != current:
            atomic_write_text(note_path, updated)
    return recipe


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


def _select_detail_history_item(st, items: list[HistoryItem]) -> HistoryItem:
    options = _history_options(items)
    focus = st.session_state.get("_focus_output_folder")
    if focus:
        focused_label = next(
            (
                name
                for name, item in options.items()
                if str(item.output_folder.resolve()) == str(Path(focus).resolve())
            ),
            None,
        )
        if focused_label:
            st.session_state["detail_select"] = focused_label
            st.session_state.pop("_focus_output_folder", None)

    current_label = str(st.session_state.get("detail_select") or "")
    if current_label not in options:
        current_label = next(iter(options))
        st.session_state["detail_select"] = current_label

    def choose(label: str) -> None:
        st.session_state["detail_select"] = label

    with st.container(border=True):
        st.markdown("### 选择要查看的菜谱")
        st.caption("可搜索菜名、分类、UP 主或标签；常用菜谱可直接点击下方按钮。")
        query = st.text_input(
            "搜索菜谱",
            placeholder="例如：番茄、川菜、UP 主名称……",
            key="detail_search",
        ).strip().lower()
        matched_labels = [
            label
            for label, item in options.items()
            if not query
            or query
            in " ".join(
                [
                    item.title,
                    item.uploader or "",
                    item.category,
                    item.cuisine,
                    *(item.tags or []),
                    item.output_folder.name,
                ]
            ).lower()
        ]
        if not matched_labels:
            st.warning("没有找到匹配的菜谱，已保留当前菜谱。")
            matched_labels = [current_label]
        elif current_label not in matched_labels:
            current_label = matched_labels[0]
            st.session_state["detail_select"] = current_label

        st.caption(f"找到 {len(matched_labels)} 道；下面显示前 {min(6, len(matched_labels))} 道快捷入口。")
        quick_columns = st.columns(2)
        for index, label in enumerate(matched_labels[:6]):
            item = options[label]
            metadata = " · ".join(
                value for value in (item.category, item.cuisine) if value and value != "未分类"
            )
            button_label = item.title if not metadata else f"{item.title} · {metadata}"
            quick_columns[index % 2].button(
                button_label,
                key=f"detail_pick_{_record_key(item.output_folder)}",
                type="primary" if label == current_label else "secondary",
                use_container_width=True,
                on_click=choose,
                args=(label,),
            )

        selected_label = st.selectbox(
            "当前查看的菜谱",
            matched_labels,
            key="detail_select",
            format_func=lambda label: (
                f"{options[label].title} · {options[label].output_folder.name}"
            ),
            help="候选较多时可直接输入文字继续筛选。",
        )
        selected_index = matched_labels.index(selected_label)
        previous_column, position_column, next_column = st.columns([1, 1.2, 1])
        previous_column.button(
            "← 上一道",
            disabled=selected_index == 0,
            key="detail_previous",
            use_container_width=True,
            on_click=choose,
            args=(matched_labels[max(0, selected_index - 1)],),
        )
        position_column.markdown(
            f"<p style='text-align:center;margin:.55rem 0'>第 {selected_index + 1} / {len(matched_labels)} 道</p>",
            unsafe_allow_html=True,
        )
        next_column.button(
            "下一道 →",
            disabled=selected_index == len(matched_labels) - 1,
            key="detail_next",
            use_container_width=True,
            on_click=choose,
            args=(matched_labels[min(len(matched_labels) - 1, selected_index + 1)],),
        )
    return options[selected_label]


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


def _archive_output(
    output_folder: Path,
    config: UIConfig,
    *,
    overwrite: bool = False,
    ratings: dict[str, int | None] | None = None,
):
    _save_recipe_ratings(output_folder, ratings)
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
    st.sidebar.subheader("运行配置")
    with st.sidebar.expander("文件与登录", expanded=False):
        config.out_dir = st.text_input("输出目录", value=config.out_dir)
        st.markdown("##### Bilibili 登录")
        config.cookies = _optional_text(st.text_input("cookies 文件路径", value=config.cookies or ""))
        st.caption("可从已登录的 Edge 导入，仅在本机保存 Bilibili 域 Cookie。")
        cookie_import, cookie_validate, cookie_remove = st.columns(3)
        with cookie_import:
            import_clicked = st.button("从 Edge 导入/刷新", use_container_width=True)
        with cookie_validate:
            validate_clicked = st.button("验证登录", disabled=not bool(config.cookies), use_container_width=True)
        with cookie_remove:
            remove_clicked = st.button("删除登录文件", use_container_width=True)
        if import_clicked:
            try:
                with st.spinner("正在从 Edge 导入并验证登录..."):
                    cookie_path = import_edge_cookies()
                config.cookies = str(cookie_path)
                save_config(config)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Cookie 导入失败：{_clean_error(exc)}")
            else:
                _rerun_with_notice(st, f"已从 Edge 导入并验证登录：{cookie_path}")
        if validate_clicked and config.cookies:
            try:
                valid = validate_bilibili_cookie_file(config.cookies)
            except Exception as exc:  # noqa: BLE001
                st.error(f"登录验证失败：{_clean_error(exc)}")
            else:
                (st.success if valid else st.error)("Bilibili 登录有效。" if valid else "登录已失效，请刷新 Cookie。")
        if remove_clicked:
            remove_imported_cookies()
            config.cookies = None
            save_config(config)
            _rerun_with_notice(st, "已删除本地导入的 Bilibili 登录文件。", level="info")
    with st.sidebar.expander("生成与转写", expanded=False):
        config.language = st.text_input("字幕/转写语言", value=config.language)
        config.whisper_model = st.selectbox(
            "Whisper 模型",
            WHISPER_MODELS,
            index=WHISPER_MODELS.index(config.whisper_model)
            if config.whisper_model in WHISPER_MODELS
            else 2,
        )
        config.enable_screenshot = st.checkbox("生成步骤截图", value=config.enable_screenshot)
        config.enable_llm_summary = st.checkbox("使用 LLM 结构化抽取 / 重写", value=config.enable_llm_summary)
        config.keep_media = st.checkbox("保留临时媒体文件", value=config.keep_media)
    with st.sidebar.expander("成品精简与审核", expanded=False):
        config.max_recipe_steps = int(
            st.number_input("最终版最多步骤", min_value=4, max_value=12, value=config.max_recipe_steps, step=1)
        )
        config.max_step_images = int(
            st.number_input(
                "最多关键图片",
                min_value=1,
                max_value=4,
                value=min(4, max(1, config.max_step_images)),
                step=1,
                help="默认 3 张，硬上限 4 张；候选图不会长期保存。",
            )
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
    with st.sidebar.expander("AI 模型", expanded=False):
        config.llm_provider = st.selectbox(
            "LLM provider",
            LLM_PROVIDERS,
            index=LLM_PROVIDERS.index(config.llm_provider)
            if config.llm_provider in LLM_PROVIDERS
            else 0,
        )
        if config.llm_provider == "codex":
            config.codex_model = _optional_text(st.text_input("Codex 模型", value=config.codex_model or ""))
            config.codex_profile = _optional_text(st.text_input("Codex profile", value=config.codex_profile or ""))
        if config.llm_provider == "openai":
            config.openai_model = st.text_input("OpenAI 模型", value=config.openai_model)
        if config.llm_provider == "local":
            config.local_llm_command = _optional_text(
                st.text_input("本地 LLM 命令", value=config.local_llm_command or "")
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


def _render_recipe_text_list(st, values: Iterable[Any], empty_message: str) -> None:
    items = [str(value).strip() for value in values if str(value).strip()]
    if not items:
        st.caption(empty_message)
        return
    for item in items:
        st.markdown(f"- {item}")


def _render_recipe_ingredients(st, values: Iterable[Any], empty_message: str) -> None:
    ingredients = list(values)
    if not ingredients:
        st.caption(empty_message)
        return
    for ingredient in ingredients:
        name = str(getattr(ingredient, "name", "") or "未命名").strip()
        amount = str(getattr(ingredient, "amount", "") or "未注明").strip()
        note = str(getattr(ingredient, "note", "") or "").strip()
        suffix = f"（{note}）" if note else ""
        st.markdown(f"- **{name}**：{amount}{suffix}")


def _render_background_dashboard(st) -> None:
    st.subheader("后台任务仪表盘")
    st.caption("集中查看所有批处理任务；预计时间会根据本次运行的实际平均速度持续调整。")
    refresh_seconds = st.selectbox(
        "自动刷新频率",
        [10, 30, 60, 0],
        format_func=lambda value: "关闭自动刷新" if value == 0 else f"每 {value} 秒",
        key="dashboard_refresh_seconds",
    )

    @st.fragment(run_every=f"{refresh_seconds}s" if refresh_seconds else None)
    def render_live_dashboard() -> None:
        st.button("立即刷新", key="dashboard_refresh_now")
        st.caption(f"最近刷新：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            states = list_batch_states()
        except Exception as exc:  # noqa: BLE001
            cached_states = st.session_state.get("_dashboard_batch_states_cache")
            states = cached_states if isinstance(cached_states, list) else []
            if states:
                st.warning(f"本次读取失败，暂时显示上次数据：{_clean_error(exc)}")
            else:
                st.error(f"读取批次状态失败：{_clean_error(exc)}")
                return
        else:
            st.session_state["_dashboard_batch_states_cache"] = states
        if not states:
            st.info("还没有后台批处理任务。可在“批量处理”中创建任务。")
            return

        runtime_by_id = {
            state.batch_id: get_background_batch_status(state.batch_id)
            for state in states
        }
        runtime_labels = {
            "running": "运行中",
            "done": "已完成",
            "done_with_errors": "完成但有失败",
            "failed": "后台失败",
            "stopped": "已停止（可继续）",
            "history": "历史任务",
        }
        running_states = [
            state
            for state in states
            if runtime_by_id.get(state.batch_id)
            and runtime_by_id[state.batch_id].status == "running"
        ]

        if running_states:
            st.success(f"当前有 {len(running_states)} 个后台任务正在运行。")
            for state in running_states:
                runtime = runtime_by_id[state.batch_id]
                summary = _batch_dashboard_summary(state, runtime)
                with st.container(border=True):
                    st.markdown(f"### {state.batch_id}")
                    output_roots = sorted(
                        {
                            str(Path(item.output_folder).parent)
                            for item in state.items
                            if item.output_folder
                        }
                    )
                    if output_roots:
                        st.caption("输出目录：" + "、".join(output_roots))
                    metric_columns = st.columns(5)
                    metric_columns[0].metric("总数", summary["total"])
                    metric_columns[1].metric("已处理", summary["processed"])
                    metric_columns[2].metric("成功", summary["completed"])
                    metric_columns[3].metric("失败", summary["failed"])
                    metric_columns[4].metric("剩余", summary["remaining"])
                    st.progress(
                        summary["progress"],
                        text=f"任务进度 {summary['processed']}/{summary['total']}（包含失败项）",
                    )
                    timing_columns = st.columns(4)
                    timing_columns[0].metric("已运行", _format_duration(summary["elapsed_seconds"]))
                    timing_columns[1].metric(
                        "平均速度",
                        f"{summary['speed_per_hour']:.1f} 条/小时"
                        if summary["speed_per_hour"] is not None
                        else "正在收集数据",
                    )
                    timing_columns[2].metric("预计剩余", _format_duration(summary["eta_seconds"]))
                    estimated_finish = summary["estimated_finish"]
                    timing_columns[3].metric(
                        "预计完成",
                        estimated_finish.astimezone().strftime("%m-%d %H:%M")
                        if estimated_finish is not None
                        else "暂无法估算",
                    )
                    active_items = [
                        item
                        for item in state.items
                        if str(getattr(item, "status", "")) == "running"
                        or str(getattr(item, "status", "")).endswith("_running")
                    ]
                    for item in active_items:
                        raw_status = str(getattr(item, "status", "running"))
                        st.info(
                            f"当前处理：{BATCH_STATUS_LABELS.get(raw_status, raw_status)} · {item.url}"
                        )
                    if runtime.error:
                        st.warning(f"后台提示：{runtime.error}")
                    if st.button("查看批次详情", key=f"dashboard_open_{state.batch_id}"):
                        st.session_state["_next_batch_select"] = state.batch_id
                        st.session_state["_next_page"] = "批量处理"
                        st.rerun()
        else:
            st.info("当前没有正在运行的后台任务。")

        st.markdown("### 全部任务")
        dashboard_rows = []
        for state in states:
            runtime = runtime_by_id.get(state.batch_id)
            summary = _batch_dashboard_summary(state, runtime)
            estimated_finish = summary["estimated_finish"]
            dashboard_rows.append(
                {
                    "批次": state.batch_id,
                    "状态": runtime_labels.get(summary["status"], summary["status"]),
                    "进度": f"{summary['processed']}/{summary['total']}",
                    "成功": summary["completed"],
                    "失败": summary["failed"],
                    "剩余": summary["remaining"],
                    "平均速度": (
                        f"{summary['speed_per_hour']:.1f} 条/小时"
                        if summary["speed_per_hour"] is not None
                        else "-"
                    ),
                    "预计剩余": _format_duration(summary["eta_seconds"]),
                    "预计完成": (
                        estimated_finish.astimezone().strftime("%Y-%m-%d %H:%M")
                        if estimated_finish is not None
                        else "-"
                    ),
                    "最近更新": state.updated_at,
                }
            )
        st.dataframe(dashboard_rows, width="stretch", hide_index=True)
        st.caption("预计时间基于本次运行的平均处理速度；视频长度、字幕质量和 LLM 速度会使估算发生变化。")

    render_live_dashboard()


def _library_count_rows(
    values: Iterable[Any],
    column_name: str,
    *,
    recipe_total: int,
) -> list[dict[str, Any]]:
    normalized = [str(value or "未分类").strip() or "未分类" for value in values]
    counts = Counter(normalized)
    return [
        {
            column_name: name,
            "数量": count,
            "占菜谱": f"{count / recipe_total:.1%}" if recipe_total else "0.0%",
        }
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _render_library_overview(st, config: UIConfig) -> None:
    st.subheader("菜谱库全览")
    st.caption("查看当前菜谱库的规模、分类、菜系和标签分布；统计只读取现有菜谱，不会修改文件。")
    items = [item for item in scan_history(config.out_dir) if item.recipe_path]
    if not items:
        st.info("菜谱库还是空的。生成或导入菜谱后，这里会自动形成统计图表。")
        return

    total = len(items)
    category_rows = _library_count_rows(
        (item.category for item in items),
        "分类",
        recipe_total=total,
    )
    cuisine_rows = _library_count_rows(
        (item.cuisine for item in items),
        "菜系",
        recipe_total=total,
    )
    tag_rows = _library_count_rows(
        (
            tag
            for item in items
            for tag in dict.fromkeys(item.tags or [])
            if str(tag).strip()
        ),
        "标签",
        recipe_total=total,
    )
    quality_values = [item.quality_score for item in items if item.quality_score is not None]
    overview_columns = st.columns(5)
    overview_columns[0].metric("菜谱总数", total)
    overview_columns[1].metric("分类数", len(category_rows))
    overview_columns[2].metric("菜系数", len(cuisine_rows))
    overview_columns[3].metric("标签数", len(tag_rows))
    overview_columns[4].metric(
        "平均完整度",
        f"{sum(quality_values) / len(quality_values):.0f}" if quality_values else "未统计",
    )
    archived_count = sum(item.workflow_status == "archived" for item in items)
    st.caption(f"已归档 {archived_count} 道；待整理或归档后有修改 {total - archived_count} 道。")

    category_column, cuisine_column = st.columns(2, gap="large")
    with category_column:
        st.markdown("### 归档分类分布")
        st.bar_chart(
            category_rows,
            x="分类",
            y="数量",
            x_label="分类",
            y_label="菜谱数量",
            horizontal=True,
            height=max(280, min(520, len(category_rows) * 38 + 100)),
        )
        st.dataframe(category_rows, width="stretch", hide_index=True)
    with cuisine_column:
        st.markdown("### 菜系分布")
        st.bar_chart(
            cuisine_rows,
            x="菜系",
            y="数量",
            x_label="菜系",
            y_label="菜谱数量",
            horizontal=True,
            height=max(280, min(520, len(cuisine_rows) * 38 + 100)),
        )
        st.dataframe(cuisine_rows, width="stretch", hide_index=True)

    st.markdown("### 热门标签")
    if tag_rows:
        visible_tag_rows = tag_rows[:15]
        st.bar_chart(
            visible_tag_rows,
            x="标签",
            y="数量",
            x_label="标签（前 15 项）",
            y_label="菜谱数量",
            height=320,
        )
        with st.expander("查看全部标签计数"):
            st.dataframe(tag_rows, width="stretch", hide_index=True)
    else:
        st.info("当前菜谱还没有标签。可在“编辑修复”中补充，之后会自动纳入统计。")

    st.markdown("### 菜谱明细")
    filter_query = st.text_input(
        "筛选菜谱明细",
        placeholder="输入菜名、UP 主或标签",
        key="overview_filter_query",
    ).strip().lower()
    filter_column, cuisine_filter_column = st.columns(2)
    with filter_column:
        selected_category = st.selectbox(
            "按分类筛选",
            ["全部分类", *[str(row["分类"]) for row in category_rows]],
            key="overview_category_filter",
        )
    with cuisine_filter_column:
        selected_cuisine = st.selectbox(
            "按菜系筛选",
            ["全部菜系", *[str(row["菜系"]) for row in cuisine_rows]],
            key="overview_cuisine_filter",
        )
    filtered_items = [
        item
        for item in items
        if (selected_category == "全部分类" or item.category == selected_category)
        and (selected_cuisine == "全部菜系" or item.cuisine == selected_cuisine)
        and (
            not filter_query
            or filter_query
            in " ".join(
                [item.title, item.uploader or "", item.category, item.cuisine, *(item.tags or [])]
            ).lower()
        )
    ]
    st.caption(f"当前显示 {len(filtered_items)} / {total} 道菜谱。")
    visible_items, _ = _paged_values(st, filtered_items, key="overview_table_page")
    st.dataframe(
        [
            {
                "菜名": item.title,
                "分类": item.category,
                "菜系": item.cuisine,
                "标签": "、".join(item.tags or []),
                "UP 主": item.uploader or "",
                "完整度": item.quality_score if item.quality_score is not None else "",
                "状态": {
                    "archived": "已归档",
                    "stale": "归档后有修改",
                    "archive_error": "归档异常",
                }.get(item.workflow_status, "待整理"),
            }
            for item in visible_items
        ],
        width="stretch",
        hide_index=True,
    )

    with st.expander("选择并打开具体菜谱"):
        selected = _select_detail_history_item(st, items)
        if st.button("打开选中的完整菜谱", type="primary", key="overview_open_detail"):
            _navigate_to_record(st, "菜谱详情", selected.output_folder)


def _render_recipe_detail(st, config: UIConfig) -> None:
    st.subheader("菜谱详情")
    st.caption("完整只读查看一条菜谱；用料、备菜、全部步骤和关键提示集中在同一页。")
    available = [item for item in scan_history(config.out_dir) if item.recipe_path]
    if not available:
        st.info("还没有可查看的菜谱。请先生成或导入一条菜谱。")
        return

    selected = _select_detail_history_item(st, available)
    recipe_data, recipe_error = _safe_recipe_to_data(selected.recipe_path)  # type: ignore[arg-type]
    if recipe_error or recipe_data is None:
        st.error(f"当前 recipe.json 已损坏：{recipe_error}")
        return
    try:
        recipe = normalize_recipe_taxonomy(_validate_recipe(recipe_data))
    except Exception as exc:  # noqa: BLE001
        st.error(f"当前菜谱结构不可用：{_clean_error(exc)}")
        return

    record_key = _record_key(selected.output_folder)
    content_column, action_column = st.columns([2.2, 1], gap="large")
    with action_column:
        _action_rail_marker(st)
        st.markdown("#### 快捷操作")
        st.caption("阅读位置保留在左侧，可随时切换到同一道菜的其他工作区。")
        if st.button(
            "加入本餐",
            key=f"detail_{record_key}_meal",
            use_container_width=True,
        ):
            recipe_ids = list(st.session_state.get("meal_recipe_ids", []))
            if selected.output_folder.name not in recipe_ids:
                recipe_ids.append(selected.output_folder.name)
            st.session_state["meal_recipe_ids"] = recipe_ids
            _open_meal_mode(st, config)
        if st.button(
            "进入烹饪模式",
            type="primary",
            key=f"detail_{record_key}_cook",
            use_container_width=True,
        ):
            _navigate_to_record(st, "烹饪模式", selected.output_folder)
        if st.button(
            "编辑完整菜谱",
            key=f"detail_{record_key}_edit",
            use_container_width=True,
        ):
            _navigate_to_record(st, "编辑修复", selected.output_folder)
        if st.button(
            "逐项审核",
            key=f"detail_{record_key}_review",
            use_container_width=True,
        ):
            _navigate_to_record(st, "审核确认", selected.output_folder)
        if st.button(
            "返回草稿与归档",
            key=f"detail_{record_key}_draft",
            use_container_width=True,
        ):
            _navigate_to_record(st, "草稿与归档", selected.output_folder)
        if recipe.source_url:
            st.link_button("打开原视频", recipe.source_url, use_container_width=True)

    with content_column:
        st.markdown(f"## {recipe.title}")
        metadata = [recipe.category, recipe.cuisine, *(recipe.tags or [])]
        st.caption(" · ".join(value for value in metadata if value and value != "未分类"))
        overview_columns = st.columns(4)
        overview_columns[0].metric("份量", recipe.servings or "未注明")
        overview_columns[1].metric("总耗时", recipe.total_time or "未注明")
        overview_columns[2].metric("难度", recipe.difficulty or "未注明")
        overview_columns[3].metric("步骤", len(recipe.steps))
        if recipe.video_title or recipe.uploader:
            st.caption(
                "来源："
                + " · ".join(value for value in (recipe.uploader, recipe.video_title) if value)
            )

        st.markdown("### 用料")
        ingredient_column, seasoning_column = st.columns(2, gap="large")
        with ingredient_column:
            st.markdown("#### 主料")
            _render_recipe_ingredients(st, recipe.ingredients, "未记录主料。")
        with seasoning_column:
            st.markdown("#### 调料")
            _render_recipe_ingredients(st, recipe.seasonings, "未记录调料。")

        preparation_column, tools_column = st.columns(2, gap="large")
        with preparation_column:
            st.markdown("### 开始前备菜")
            _render_recipe_text_list(st, recipe.prep_items, "未记录单独的备菜事项。")
        with tools_column:
            st.markdown("### 工具")
            _render_recipe_text_list(st, recipe.tools, "未记录特殊工具。")

        if recipe.shopping_list:
            with st.expander("查看原始购物清单"):
                _render_recipe_text_list(st, recipe.shopping_list, "未记录购物清单。")

        st.markdown("### 完整步骤")
        if not recipe.steps:
            st.info("这条菜谱还没有烹饪步骤。")
        for index, step in enumerate(recipe.steps, start=1):
            with st.container(border=True):
                st.markdown(f"#### {index}. {step.title}")
                image_path = _local_markdown_image(selected.output_folder, step.screenshot_path or "")
                if image_path and image_path.is_file():
                    st.image(str(image_path), caption=step.title, width=360)
                st.markdown(step.action)
                details = " · ".join(
                    value
                    for value in (
                        f"火候：{step.heat}" if step.heat else "",
                        f"时长：{step.duration}" if step.duration else "",
                    )
                    if value
                )
                if details:
                    st.caption(details)
                if step.tips:
                    st.warning(f"提示：{step.tips}")
                if recipe.source_url:
                    separator = "&" if "?" in recipe.source_url else "?"
                    st.link_button(
                        "从本步骤时间点打开原视频",
                        f"{recipe.source_url}{separator}t={max(0, int(step.start_time))}",
                        key=f"detail_{record_key}_source_{index}",
                    )

        if recipe.summary_tips:
            st.markdown("### 关键点速查")
            for tip in recipe.summary_tips:
                st.info(str(tip))
        if recipe.uncertain_points:
            st.markdown("### 烹饪前请确认")
            for point in recipe.uncertain_points:
                st.warning(str(point))


MEAL_OCCASIONS = ["日常家宴", "朋友聚餐", "带小孩", "清淡家宴", "节日聚餐", "自定义"]


def _meal_mode_requested(st) -> bool:
    query_mode = st.query_params.get("mode")
    if isinstance(query_mode, list):
        query_mode = query_mode[-1] if query_mode else None
    return query_mode == "meal"


def _open_meal_mode(st, config: UIConfig) -> None:
    st.session_state["_meal_out_dir"] = str(config.out_dir)
    st.query_params["mode"] = "meal"
    st.rerun()


def _close_meal_mode(st) -> None:
    if "mode" in st.query_params:
        del st.query_params["mode"]
    st.rerun()


@lru_cache(maxsize=512)
def _cached_meal_card_image_data_uri(path_value: str, modified_ns: int) -> str | None:
    path = Path(path_value)
    if not path.is_file():
        return None
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.thumbnail((240, 165))
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, "#f7f1e7")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image)
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=52, optimize=True)
    except Exception:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _meal_card_image_data_uri(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    return _cached_meal_card_image_data_uri(str(path.resolve()), modified_ns)


def _render_meal_planner(st, config: UIConfig) -> None:
    st.subheader("餐厅式点餐")
    st.caption("点餐在独立界面中完成，主工作区不会再混入菜单、采购和套餐表单。")
    selected_count = len(st.session_state.get("meal_recipe_ids", []))
    try:
        saved_count = len(load_meal_plans())
    except Exception:
        saved_count = 0
    with st.container(border=True):
        st.markdown("### 🍽️ 打开点餐台")
        st.write("像手机 App 一样浏览菜谱并直接加菜，再统一调整份量、备注和采购清单。")
        summary_columns = st.columns(3)
        summary_columns[0].metric("本餐已点", f"{selected_count} 道")
        summary_columns[1].metric("已存套餐", f"{saved_count} 个")
        summary_columns[2].metric("界面", "独立点餐页")
        if st.button(
            "进入餐厅点餐界面 →",
            type="primary",
            key="meal_open_restaurant",
            use_container_width=True,
        ):
            _open_meal_mode(st, config)


def _render_restaurant_meal_ui(st, config: UIConfig) -> None:
    st.markdown(
        """
        <style>
        [data-testid="stMainBlockContainer"] { max-width: 1440px; padding-top: 1.25rem; }
        .brn-meal-checkout-marker { display: none; }
        div[data-testid="stVerticalBlockBorderWrapper"]:has(.brn-meal-checkout-marker) {
          background: color-mix(in srgb, var(--background-color) 96%, #c85c43 4%);
          border-radius: 1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    if st.button("← 返回管理界面", key="meal_return_main"):
        _close_meal_mode(st)

    available = [item for item in scan_history(config.out_dir) if item.recipe_path]
    if not available:
        st.info("菜谱库还是空的。请先生成或导入菜谱。")
        return
    history_by_id = {item.output_folder.name: item for item in available}
    recipe_cache: dict[str, Recipe | None] = {}

    def recipe_for(recipe_id: str) -> Recipe | None:
        if recipe_id in recipe_cache:
            return recipe_cache[recipe_id]
        item = history_by_id.get(recipe_id)
        if item is None or item.recipe_path is None:
            recipe_cache[recipe_id] = None
            return None
        recipe_data, error = _safe_recipe_to_data(item.recipe_path)
        if error or recipe_data is None:
            recipe_cache[recipe_id] = None
            return None
        try:
            recipe_cache[recipe_id] = normalize_recipe_taxonomy(_validate_recipe(recipe_data))
        except Exception:
            recipe_cache[recipe_id] = None
        return recipe_cache[recipe_id]

    def suggested_multiplier(recipe_id: str, guest_count: int) -> float:
        recipe = recipe_for(recipe_id)
        baseline = parse_servings(recipe.servings) if recipe else None
        if baseline is None:
            return 1.0
        return min(10.0, max(0.25, round(guest_count / baseline * 4) / 4))

    if "meal_recipe_ids" not in st.session_state:
        st.session_state["meal_recipe_ids"] = []
    if "meal_multipliers" not in st.session_state:
        st.session_state["meal_multipliers"] = {}
    if "meal_item_notes" not in st.session_state:
        st.session_state["meal_item_notes"] = {}
    if "meal_guest_count" not in st.session_state:
        st.session_state["meal_guest_count"] = 4
    if "meal_child_count" not in st.session_state:
        st.session_state["meal_child_count"] = 0

    def apply_component_order(raw_order: Any) -> None:
        if not isinstance(raw_order, dict):
            return
        selected_ids = [
            str(recipe_id)
            for recipe_id in raw_order.get("selected_ids", [])
            if str(recipe_id) in history_by_id
        ]
        raw_multipliers = raw_order.get("multipliers", {})
        raw_notes = raw_order.get("notes", {})
        if not isinstance(raw_multipliers, dict):
            raw_multipliers = {}
        if not isinstance(raw_notes, dict):
            raw_notes = {}
        guest_count = min(50, max(1, int(raw_order.get("guest_count", 4))))
        child_count = min(guest_count, max(0, int(raw_order.get("child_count", 0))))
        occasion = str(raw_order.get("occasion") or MEAL_OCCASIONS[0])
        st.session_state["meal_recipe_ids"] = selected_ids
        st.session_state["meal_multipliers"] = {
            recipe_id: min(10.0, max(0.25, float(raw_multipliers.get(recipe_id, 1.0))))
            for recipe_id in selected_ids
        }
        st.session_state["meal_item_notes"] = {
            recipe_id: str(raw_notes.get(recipe_id, ""))[:200]
            for recipe_id in selected_ids
        }
        st.session_state["meal_guest_count"] = guest_count
        st.session_state["meal_child_count"] = child_count
        st.session_state["meal_occasion"] = occasion

    component_state = st.session_state.get("meal_order_component")
    if isinstance(component_state, dict):
        apply_component_order(component_state.get("order"))

    def card_image(recipe_id: str) -> Path | None:
        recipe = recipe_for(recipe_id)
        if recipe is None:
            return None
        for step in recipe.steps:
            path = _local_markdown_image(
                history_by_id[recipe_id].output_folder,
                step.screenshot_path or "",
            )
            if path and path.is_file():
                return path
        return None

    def load_saved_plan(plan: Any) -> None:
        valid_items = [item for item in plan.items if item.recipe_id in history_by_id]
        st.session_state["meal_recipe_ids"] = [item.recipe_id for item in valid_items]
        st.session_state["meal_multipliers"] = {
            item.recipe_id: item.servings_multiplier for item in valid_items
        }
        st.session_state["meal_item_notes"] = {item.recipe_id: item.note for item in valid_items}
        for item in valid_items:
            key = f"meal_factor_{_record_key(history_by_id[item.recipe_id].output_folder)}"
            st.session_state[key] = item.servings_multiplier
            st.session_state[f"meal_note_{_record_key(history_by_id[item.recipe_id].output_folder)}"] = item.note
        st.session_state["meal_guest_count"] = plan.guest_count
        st.session_state["meal_child_count"] = plan.child_count
        st.session_state["meal_occasion"] = (
            plan.occasion if plan.occasion in MEAL_OCCASIONS else "自定义"
        )
        st.session_state["meal_plan_name"] = plan.name
        st.session_state["meal_plan_notes"] = plan.notes
        st.session_state["meal_loaded_plan_id"] = plan.id
        loaded_order = {
            "selected_ids": [item.recipe_id for item in valid_items],
            "multipliers": {
                item.recipe_id: item.servings_multiplier for item in valid_items
            },
            "notes": {item.recipe_id: item.note for item in valid_items},
            "guest_count": plan.guest_count,
            "child_count": plan.child_count,
            "occasion": plan.occasion if plan.occasion in MEAL_OCCASIONS else "自定义",
        }
        current_component_state = st.session_state.get("meal_order_component")
        if isinstance(current_component_state, dict):
            current_component_state["order"] = loaded_order

    try:
        saved_plans = load_meal_plans()
    except Exception as exc:  # noqa: BLE001
        saved_plans = []
        st.error(f"读取套餐库失败：{_clean_error(exc)}")

    order_tab, saved_tab = st.tabs(["🍽️ 菜单与本餐", f"📚 套餐库（{len(saved_plans)}）"])
    with order_tab:
        valid_current_ids = [
            recipe_id
            for recipe_id in st.session_state.get("meal_recipe_ids", [])
            if recipe_id in history_by_id
        ]
        if valid_current_ids != st.session_state.get("meal_recipe_ids"):
            st.session_state["meal_recipe_ids"] = valid_current_ids
        default_order = {
            "selected_ids": valid_current_ids,
            "multipliers": dict(st.session_state.get("meal_multipliers", {})),
            "notes": dict(st.session_state.get("meal_item_notes", {})),
            "guest_count": int(st.session_state.get("meal_guest_count", 4)),
            "child_count": int(st.session_state.get("meal_child_count", 0)),
            "occasion": str(st.session_state.get("meal_occasion", MEAL_OCCASIONS[0])),
        }
        component_recipes = []
        for item in available:
            recipe_id = item.output_folder.name
            recipe = recipe_for(recipe_id)
            if recipe is None:
                continue
            candidate = MealCandidate(
                recipe_id=recipe_id,
                title=item.title,
                category=item.category,
                cuisine=item.cuisine,
                tags=tuple(item.tags or []),
                quality_score=item.quality_score,
            )
            component_recipes.append(
                {
                    "id": recipe_id,
                    "title": item.title,
                    "category": item.category or "未分类",
                    "cuisine": item.cuisine or "",
                    "tags": list(item.tags or []),
                    "servings": recipe.servings or "",
                    "quality_score": item.quality_score,
                    "kind": meal_candidate_kind(candidate),
                    "image": _meal_card_image_data_uri(card_image(recipe_id)),
                }
            )
        component_result = render_meal_order_component(
            st,
            data={
                "recipes": component_recipes,
                "occasions": MEAL_OCCASIONS,
                "order": default_order,
                "revision": hashlib.sha256(
                    json.dumps(default_order, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:16],
            },
            default_order=default_order,
        )
        component_order = getattr(component_result, "order", None)
        if isinstance(component_order, dict):
            apply_component_order(component_order)
        selected_ids = list(st.session_state["meal_recipe_ids"])
        multipliers = dict(st.session_state["meal_multipliers"])
        item_notes = dict(st.session_state["meal_item_notes"])
        guest_count = int(st.session_state["meal_guest_count"])
        child_count = int(st.session_state["meal_child_count"])
        occasion = str(st.session_state["meal_occasion"])

        selected_recipes: dict[str, Recipe] = {}
        for recipe_id in selected_ids:
            item = history_by_id[recipe_id]
            recipe = recipe_for(recipe_id)
            if recipe is None:
                st.warning(f"无法读取“{item.title}”的 recipe.json，已跳过份量和采购统计。")
                continue
            selected_recipes[recipe_id] = recipe

        if selected_recipes:
            with st.container(border=True):
                st.markdown('<span class="brn-meal-checkout-marker"></span>', unsafe_allow_html=True)
                st.markdown("### 🧺 采购清单")
                unit_label = st.segmented_control(
                    "采购单位",
                    ["保留原单位", "换算为公制"],
                    key="meal_unit_system",
                    default="保留原单位",
                )
                unit_system = "metric" if unit_label == "换算为公制" else "original"
                shopping_rows: dict[str, dict[str, Any]] = {}
                for recipe_id, recipe in selected_recipes.items():
                    factor = float(multipliers.get(recipe_id, 1.0))
                    for shopping_item in build_shopping_list(recipe, factor=factor, unit_system=unit_system):
                        row = shopping_rows.setdefault(
                            shopping_item.name.casefold(),
                            {"食材": shopping_item.name, "需求明细": [], "用于菜品": []},
                        )
                        row["需求明细"].append(f"{recipe.title}：{shopping_item.amount}")
                        row["用于菜品"].append(recipe.title)
                rendered_shopping_rows = [
                    {
                        "食材": row["食材"],
                        "需求明细": "；".join(row["需求明细"]),
                        "用于菜品": "、".join(dict.fromkeys(row["用于菜品"])),
                    }
                    for row in sorted(shopping_rows.values(), key=lambda value: value["食材"])
                ]
                st.dataframe(rendered_shopping_rows, width="stretch", hide_index=True)
                shopping_markdown = "\n".join(
                    [
                        f"# {guest_count} 人本餐采购清单",
                        "",
                        *[
                            f"- [ ] {row['食材']}：{row['需求明细']}"
                            for row in rendered_shopping_rows
                        ],
                    ]
                ) + "\n"
                st.download_button(
                    "下载本餐采购清单",
                    data=shopping_markdown,
                    file_name=f"{guest_count}人本餐采购清单.md",
                    mime="text/markdown",
                    key="meal_download_shopping",
                )

        st.markdown("### ⭐ 把这桌保存为套餐")
        if "meal_plan_name" not in st.session_state:
            st.session_state["meal_plan_name"] = f"{occasion}{guest_count}人套餐"
        with st.form("meal_save_form"):
            plan_name = st.text_input("套餐名称", key="meal_plan_name")
            plan_notes = st.text_area(
                "组合说明",
                placeholder="例如：适合周末午餐；提前炖汤；孩子不吃辣。",
                key="meal_plan_notes",
            )
            practiced = st.checkbox("这是已经实践过、值得保留的组合", key="meal_save_practiced")
            practice_rating = st.selectbox(
                "本次组合评分",
                [5, 4, 3, 2, 1],
                key="meal_save_rating",
                disabled=not practiced,
            )
            practice_notes = st.text_area(
                "本次实践经验",
                placeholder="例如：5 人份量刚好；汤可以减半；两道菜同时用烤箱会冲突。",
                key="meal_save_practice_notes",
                disabled=not practiced,
            )
            save_plan = st.form_submit_button(
                "更新当前套餐" if st.session_state.get("meal_loaded_plan_id") else "保存新套餐",
                type="primary",
                disabled=not bool(selected_ids),
            )
        if save_plan:
            try:
                saved = save_meal_plan(
                    name=plan_name,
                    guest_count=guest_count,
                    child_count=child_count,
                    occasion=occasion,
                    notes=plan_notes,
                    items=[
                        MealPlanItem(
                            recipe_id=recipe_id,
                            title=history_by_id[recipe_id].title,
                            servings_multiplier=float(multipliers.get(recipe_id, 1.0)),
                            note=str(item_notes.get(recipe_id, "")),
                        )
                        for recipe_id in selected_ids
                    ],
                    plan_id=st.session_state.get("meal_loaded_plan_id"),
                )
                if practiced:
                    saved = record_meal_plan_practice(
                        saved.id,
                        rating=int(practice_rating),
                        notes=practice_notes,
                    )
            except Exception as exc:  # noqa: BLE001
                st.error(f"保存套餐失败：{_clean_error(exc)}")
            else:
                st.session_state["meal_loaded_plan_id"] = saved.id
                _rerun_with_notice(st, f"套餐“{saved.name}”已保存。")

    with saved_tab:
        if not saved_plans:
            st.info("还没有保存套餐。先在“本餐点菜”中组合并保存。")
        for plan in saved_plans:
            ratings = [
                int(record["rating"])
                for record in plan.practice_records
                if record.get("rating") in {1, 2, 3, 4, 5}
            ]
            with st.expander(
                f"{plan.name} · {plan.guest_count} 人 · {len(plan.items)} 道菜",
                expanded=plan.id == st.session_state.get("meal_loaded_plan_id"),
            ):
                st.caption(
                    f"{plan.occasion}；儿童 {plan.child_count} 人；"
                    f"实践 {len(plan.practice_records)} 次；"
                    + (f"平均 {sum(ratings) / len(ratings):.1f} 星" if ratings else "尚未评分")
                )
                st.markdown("、".join(item.title for item in plan.items))
                if plan.notes:
                    st.info(plan.notes)
                missing = [item.title for item in plan.items if item.recipe_id not in history_by_id]
                if missing:
                    st.warning("当前菜谱库缺少：" + "、".join(missing))
                load_column, detail_column = st.columns(2)
                with load_column:
                    st.button(
                        "载入为本餐",
                        type="primary",
                        key=f"meal_load_{plan.id}",
                        use_container_width=True,
                        on_click=load_saved_plan,
                        args=(plan,),
                    )
                with detail_column:
                    first_available = next(
                        (item for item in plan.items if item.recipe_id in history_by_id),
                        None,
                    )
                    if first_available and st.button(
                        "查看第一道菜",
                        key=f"meal_view_{plan.id}",
                        use_container_width=True,
                    ):
                        _navigate_to_record(
                            st,
                            "菜谱详情",
                            history_by_id[first_available.recipe_id].output_folder,
                        )
                st.markdown("#### 记录一次套餐实践")
                practice_date = st.date_input("实践日期", key=f"meal_practice_date_{plan.id}")
                rating = st.selectbox(
                    "组合评分",
                    [5, 4, 3, 2, 1],
                    key=f"meal_practice_rating_{plan.id}",
                )
                notes = st.text_area(
                    "实践记录",
                    placeholder="份量、搭配、出菜顺序、孩子接受度或下次调整建议。",
                    key=f"meal_practice_notes_{plan.id}",
                )
                if st.button("保存实践记录", key=f"meal_practice_save_{plan.id}"):
                    try:
                        record_meal_plan_practice(
                            plan.id,
                            rating=int(rating),
                            notes=notes,
                            practiced_on=practice_date.isoformat(),
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"保存实践记录失败：{_clean_error(exc)}")
                    else:
                        _rerun_with_notice(st, f"已记录“{plan.name}”的本次实践。")
                if plan.practice_records:
                    st.dataframe(
                        [
                            {
                                "日期": record.get("practiced_on") or "",
                                "评分": "★" * int(record.get("rating") or 0),
                                "经验": record.get("notes") or "",
                            }
                            for record in reversed(plan.practice_records)
                        ],
                        width="stretch",
                        hide_index=True,
                    )
                confirm_delete = st.checkbox(
                    "确认删除这个套餐（不会删除菜谱）",
                    key=f"meal_delete_confirm_{plan.id}",
                )
                if st.button(
                    "删除套餐",
                    disabled=not confirm_delete,
                    key=f"meal_delete_{plan.id}",
                ):
                    try:
                        delete_meal_plan(plan.id)
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"删除套餐失败：{_clean_error(exc)}")
                    else:
                        if st.session_state.get("meal_loaded_plan_id") == plan.id:
                            st.session_state.pop("meal_loaded_plan_id", None)
                        _rerun_with_notice(st, f"套餐“{plan.name}”已删除。")


def _render_cooking_mode(st, config: UIConfig) -> None:
    st.subheader("移动烹饪模式")
    st.caption("按目标份量生成临时用量与购物清单，并逐步显示操作；不会改写 recipe.json。")
    st.markdown(
        """
        <style>
        @media (max-width: 700px) {
          [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; }
          div[data-testid="stButton"] > button { min-height: 3rem; font-size: 1.05rem; }
          div[data-testid="stCheckbox"] label { min-height: 2.5rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    available = [item for item in scan_history(config.out_dir) if item.recipe_path]
    if not available:
        st.info("还没有可用于烹饪模式的菜谱。请先生成或导入一条菜谱。")
        return

    options = _history_options(available)
    selected = _select_history_item(st, "选择菜谱", options, "cook_select")
    recipe_data, recipe_error = _safe_recipe_to_data(selected.recipe_path)  # type: ignore[arg-type]
    if recipe_error or recipe_data is None:
        st.error(f"当前 recipe.json 已损坏：{recipe_error}")
        return
    try:
        recipe = normalize_recipe_taxonomy(_validate_recipe(recipe_data))
    except Exception as exc:  # noqa: BLE001
        st.error(f"当前菜谱结构不可用：{_clean_error(exc)}")
        return

    record_key = _record_key(selected.output_folder)
    title_column, detail_column = st.columns([3, 1])
    with title_column:
        st.markdown(f"## {recipe.title}")
    with detail_column:
        if st.button(
            "查看完整菜谱",
            key=f"cook_{record_key}_detail",
            use_container_width=True,
        ):
            _navigate_to_record(st, "菜谱详情", selected.output_folder)
    info_columns = st.columns(3)
    info_columns[0].metric("原份量", recipe.servings or "未注明")
    info_columns[1].metric("总耗时", recipe.total_time or "未注明")
    info_columns[2].metric("步骤", len(recipe.steps))

    baseline = parse_servings(recipe.servings)
    scale_col, unit_col = st.columns(2)
    with scale_col:
        if baseline is not None:
            target_servings = st.number_input(
                "目标份量",
                min_value=0.25,
                max_value=100.0,
                value=float(baseline),
                step=0.5,
                key=f"cook_{record_key}_target_servings",
            )
            factor = serving_scale(recipe.servings, float(target_servings))
        else:
            factor = float(
                st.number_input(
                    "用量倍率",
                    min_value=0.1,
                    max_value=20.0,
                    value=1.0,
                    step=0.25,
                    key=f"cook_{record_key}_factor",
                    help="原菜谱未注明可识别的份量，因此直接按倍率缩放。",
                )
            )
    with unit_col:
        unit_label = st.selectbox(
            "单位显示",
            ["保留原单位", "换算为公制"],
            key=f"cook_{record_key}_unit_system",
            help="公制模式会把斤、两、杯、汤匙等换算为克或毫升。",
        )
    unit_system = "metric" if unit_label == "换算为公制" else "original"
    st.caption(f"当前用量倍率：{factor:.2f}×")

    shopping_items = build_shopping_list(recipe, factor=factor, unit_system=unit_system)
    shopping_markdown = shopping_list_markdown(recipe, shopping_items, factor)
    st.markdown("### 配料与购物清单")
    scale_signature = hashlib.sha1(f"{factor:.6f}:{unit_system}".encode("utf-8")).hexdigest()[:8]
    current_category = ""
    for index, item in enumerate(shopping_items):
        if item.category != current_category:
            current_category = item.category
            st.markdown(f"#### {current_category}")
        st.checkbox(
            item.label,
            key=f"cook_{record_key}_shop_{scale_signature}_{index}",
        )
    if not shopping_items:
        st.info("这条菜谱还没有结构化配料。可先到“编辑修复”页补充。")
    elif factor != 1 and any(not item.converted for item in shopping_items):
        st.warning("“适量、少许”或复杂写法无法安全缩放，已保留原文，请烹饪时人工确认。")
    st.download_button(
        "下载购物清单",
        data=shopping_markdown,
        file_name=f"{recipe.title}-购物清单.md",
        mime="text/markdown",
        key=f"cook_{record_key}_download_shopping",
    )

    if recipe.prep_items:
        st.markdown("### 开始前备菜")
        for index, prep_item in enumerate(recipe.prep_items):
            st.checkbox(str(prep_item), key=f"cook_{record_key}_prep_{index}")

    st.markdown("### 分步烹饪")
    if not recipe.steps:
        st.info("这条菜谱还没有烹饪步骤。")
        return

    step_state_key = f"cook_{record_key}_step_index"
    raw_step_index = st.session_state.get(step_state_key, 0)
    step_index = int(raw_step_index) if isinstance(raw_step_index, (int, float)) else 0
    step_index = min(max(step_index, 0), len(recipe.steps) - 1)
    st.session_state[step_state_key] = step_index
    st.progress((step_index + 1) / len(recipe.steps), text=f"第 {step_index + 1} / {len(recipe.steps)} 步")

    nav_previous, nav_restart, nav_next = st.columns(3)
    if nav_previous.button(
        "← 上一步",
        disabled=step_index == 0,
        key=f"cook_{record_key}_previous",
        width="stretch",
    ):
        st.session_state[step_state_key] = step_index - 1
        st.session_state.pop(f"cook_{record_key}_completed", None)
        st.rerun()
    if nav_restart.button("从头开始", key=f"cook_{record_key}_restart", width="stretch"):
        st.session_state[step_state_key] = 0
        st.session_state.pop(f"cook_{record_key}_completed", None)
        st.rerun()
    next_label = "完成烹饪" if step_index == len(recipe.steps) - 1 else "下一步 →"
    if nav_next.button(
        next_label,
        type="primary",
        key=f"cook_{record_key}_next",
        width="stretch",
    ):
        if step_index < len(recipe.steps) - 1:
            st.session_state[step_state_key] = step_index + 1
        else:
            st.session_state[f"cook_{record_key}_completed"] = True
        st.rerun()

    if st.session_state.get(f"cook_{record_key}_completed"):
        st.success("全部步骤已完成，可以出锅了。")

    step = recipe.steps[step_index]
    with st.container(border=True):
        st.markdown(f"## {step_index + 1}. {step.title}")
        image_path = _local_markdown_image(selected.output_folder, step.screenshot_path or "")
        if image_path and image_path.is_file():
            st.image(str(image_path), caption=step.title, width="stretch")
        st.markdown(step.action)
        detail_columns = st.columns(2)
        detail_columns[0].metric("火候", step.heat or "按步骤判断")
        detail_columns[1].metric("时长", step.duration or "未注明")
        if step.tips:
            st.warning(step.tips)
        if recipe.source_url:
            separator = "&" if "?" in recipe.source_url else "?"
            st.link_button(
                "从本步骤时间点打开原视频",
                f"{recipe.source_url}{separator}t={max(0, int(step.start_time))}",
                width="stretch",
            )

    with st.expander("查看全部步骤"):
        for index, candidate in enumerate(recipe.steps, start=1):
            marker = "→" if index - 1 == step_index else ""
            st.markdown(f"**{marker} {index}. {candidate.title}**  \n{candidate.action}")


def _lan_ip_address() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return str(probe.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()


def _render_mobile_client_admin(st, config: UIConfig) -> None:
    st.subheader("手机客户端")
    try:
        store = MobileSyncStore(Path.cwd(), out_dir=config.out_dir)
        index_result = store.index_recipes()
    except Exception as exc:  # noqa: BLE001
        st.error(f"同步服务数据初始化失败：{_clean_error(exc)}")
        return

    st.markdown("#### 网页离线版（推荐个人使用）")
    st.caption("不需要 Apple 开发者账号。先在 iPhone 的 Safari 打开网页版并添加到主屏幕，再把下面的菜谱包导入一次。")
    image_mode_labels = {
        "all": "全部步骤图",
        "first": "每道菜仅一张（推荐）",
        "none": "只导出文字",
    }
    image_mode = st.radio(
        "图片导出方式",
        ["first", "none", "all"],
        format_func=image_mode_labels.get,
        horizontal=True,
        key="web_library_image_mode",
        help="“每道菜仅一张”会保留该菜第一张有效步骤图；“只导出文字”会移除图片数据和图片引用。",
    )
    try:
        web_payload = build_web_library_payload(store, image_mode=image_mode)
        web_content = web_library_bytes(web_payload)
        st.download_button(
            "下载网页版菜谱包",
            data=web_content,
            file_name="bili-recipe-web-library.json",
            mime="application/json",
            type="primary",
            width="stretch",
        )
        st.caption(
            f"包含 {len(web_payload['recipes'])} 道菜谱、{len(web_payload['assets'])} 张步骤图；"
            f"文件约 {len(web_content) / (1024 * 1024):.1f} MB。"
            "菜谱包只在电脑与手机之间传递，不会上传到网页版服务器。"
        )
    except Exception as exc:  # noqa: BLE001
        st.warning(f"网页版菜谱包生成失败：{_clean_error(exc)}")

    st.divider()
    st.markdown("#### 原生 App 局域网同步（保留）")
    st.warning("当前使用 HTTP，仅适合可信家庭局域网。不要在公共 Wi-Fi 中启动或配对。")

    base_url = st.text_input("手机同步地址", value=f"http://{_lan_ip_address()}:8765")
    try:
        with urlopen("http://127.0.0.1:8765/api/v1/health", timeout=0.8) as response:  # noqa: S310
            health = json.load(response)
        st.success(f"同步 API 正常 · 协议 v{health.get('protocol_version', '?')}")
    except Exception:  # noqa: BLE001
        st.warning(
            "同步 API 当前不可访问；请使用对应系统的一键启动器（Linux: start-ui-linux.sh；"
            "macOS: start-ui-mac.command）启动管理页和同步服务。"
        )
    metrics = st.columns(4)
    metrics[0].metric("菜谱", index_result["indexed"])
    metrics[1].metric("服务修订", store.current_revision())
    metrics[2].metric("已配对设备", len([item for item in store.list_devices() if not item["revoked_at"]]))
    metrics[3].metric("待解决冲突", len(store.list_conflicts()))
    if index_result["duplicates"]:
        st.warning("发现重复菜谱身份，手机客户端只发布最近修改的一份。")
        st.json(index_result["duplicates"])

    if st.button("生成 10 分钟配对二维码", type="primary"):
        credential = store.issue_pairing_credential(base_url)
        st.session_state["mobile_pairing_payload"] = credential.qr_payload()
        st.session_state["mobile_pairing_expires"] = credential.expires_at
    pairing_payload = st.session_state.get("mobile_pairing_payload")
    if isinstance(pairing_payload, str):
        try:
            import qrcode

            st.image(qrcode.make(pairing_payload), caption="在手机客户端中扫码", width=280)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"二维码生成失败，可复制下方配对数据：{_clean_error(exc)}")
        st.caption(f"有效期至：{st.session_state.get('mobile_pairing_expires', '')}")
        with st.expander("查看配对数据"):
            st.code(pairing_payload, language="json")

    st.markdown("#### 已配对设备")
    devices = store.list_devices()
    if not devices:
        st.info("还没有配对设备。")
    for device in devices:
        columns = st.columns([3, 3, 1])
        columns[0].write(f"**{device['name']}**")
        columns[1].caption(f"最近连接：{device['last_seen_at']}")
        if device["revoked_at"]:
            columns[2].caption("已撤销")
        elif columns[2].button("撤销", key=f"mobile_revoke_{device['id']}"):
            store.revoke_device(str(device["id"]))
            st.rerun()

    st.markdown("#### 实践日志")
    recipes = store.list_recipes()
    recipe_titles = {str(item["id"]): str(item.get("title") or item["id"]) for item in recipes}
    logs = store.list_practice_logs()
    if not logs:
        st.info("手机客户端同步心得后会显示在这里。")
    else:
        labels = {
            str(item["id"]): f"{recipe_titles.get(str(item['recipe_id']), '未知菜谱')} · {item['cooked_on']} · {item['notes'][:24]}"
            for item in logs
        }
        selected_id = st.selectbox("选择日志", list(labels), format_func=labels.get)
        selected = next(item for item in logs if item["id"] == selected_id)
        if selected.get("photo_sha256"):
            found = store.asset_path(str(selected["photo_sha256"]))
            if found:
                st.image(str(found[0]), caption="实践照片", width=320)
        outcome_values = ["", "success", "partial", "failed"]
        outcome_labels = {"": "未填写", "success": "成功", "partial": "部分成功", "failed": "失败"}
        with st.form(f"mobile_log_{selected_id}"):
            cooked_on = st.date_input("实践日期", value=datetime.strptime(selected["cooked_on"], "%Y-%m-%d").date())
            outcome = st.selectbox(
                "结果", outcome_values, index=outcome_values.index(selected.get("outcome") or ""), format_func=outcome_labels.get
            )
            rating = st.selectbox("评分", [None, 1, 2, 3, 4, 5], index=int(selected.get("rating") or 0))
            notes = st.text_area("心得", value=selected["notes"], height=140, max_chars=5000)
            save_log = st.form_submit_button("保存并同步", type="primary", disabled=not notes.strip())
        if save_log:
            store.admin_save_practice_log(
                {
                    **selected,
                    "cooked_on": cooked_on.isoformat(),
                    "outcome": outcome or None,
                    "rating": rating,
                    "notes": notes.strip(),
                }
            )
            st.success("已保存，手机客户端下次同步时会收到更新。")
            st.rerun()
        confirm_delete = st.checkbox("确认软删除这条日志", key=f"mobile_delete_confirm_{selected_id}")
        if st.button("软删除日志", disabled=not confirm_delete, key=f"mobile_delete_{selected_id}"):
            store.admin_delete_practice_log(selected_id)
            st.rerun()

    conflicts = store.list_conflicts()
    st.markdown("#### 同步冲突")
    if not conflicts:
        st.success("没有待解决冲突。")
    for conflict in conflicts:
        with st.expander(f"日志 {conflict['entity_id']} · {conflict['created_at']}"):
            left, right = st.columns(2)
            left.markdown("**服务器版本**")
            left.json(conflict["server"])
            right.markdown("**离线传入版本**")
            right.json(conflict["incoming"])
            merged_text = st.text_area(
                "手工合并 JSON",
                value=json.dumps(conflict["incoming"], ensure_ascii=False, indent=2),
                key=f"conflict_merged_payload_{conflict['id']}",
            )
            keep_server, keep_incoming, use_merged = st.columns(3)
            if keep_server.button("保留服务器版本", key=f"conflict_server_{conflict['id']}"):
                store.resolve_conflict(str(conflict["id"]), "server")
                st.rerun()
            if keep_incoming.button("采用离线版本", key=f"conflict_incoming_{conflict['id']}"):
                store.resolve_conflict(str(conflict["id"]), "incoming")
                st.rerun()
            if use_merged.button("应用手工合并", key=f"conflict_merged_{conflict['id']}"):
                try:
                    merged = json.loads(merged_text)
                    if not isinstance(merged, dict):
                        raise ValueError("合并结果必须是 JSON 对象")
                    store.resolve_conflict(str(conflict["id"]), "merged", merged)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"合并失败：{_clean_error(exc)}")
                else:
                    st.rerun()


def _curation_item_id(item: dict[str, Any]) -> str:
    item_id = str(item.get("item_id") or "").strip()
    if item_id:
        return item_id
    return Path(str(item.get("output_folder") or "unknown")).name


def _curation_saved_item(decisions: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    saved = decisions.get("items", {}).get(_curation_item_id(item), {})
    return saved if isinstance(saved, dict) else {}


def _curation_decision(item: dict[str, Any], decisions: dict[str, Any]) -> str:
    value = str(_curation_saved_item(decisions, item).get("decision") or "pending")
    return value if value in CURATION_DECISION_VALUES else "pending"


def _curation_recipe(item: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    folder = Path(str(item.get("output_folder") or ""))
    recipe_path = folder / "recipe.json"
    if not recipe_path.is_file():
        return None, f"找不到 recipe.json：{recipe_path}"
    return _safe_recipe_to_data(recipe_path)


def _curation_material_rows(item: dict[str, Any], recipe: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field, label in (("ingredients", "主料"), ("seasonings", "调料")):
        values = recipe.get(field)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            rows.append(
                {
                    "来源": item.get("bvid") or _curation_item_id(item),
                    "规范名": item.get("group_title") or "",
                    "类型": label,
                    "名称": value.get("name") or "",
                    "用量": value.get("amount") or "",
                    "说明": value.get("note") or "",
                    "字幕证据": value.get("evidence") or "",
                }
            )
    return rows


def _curation_step_rows(item: dict[str, Any], recipe: dict[str, Any]) -> list[dict[str, Any]]:
    steps = recipe.get("steps") if isinstance(recipe.get("steps"), list) else []
    return [
        {
            "来源": item.get("bvid") or _curation_item_id(item),
            "规范名": item.get("group_title") or "",
            "序号": index,
            "步骤": step.get("title") or step.get("action") or "",
            "操作": step.get("action") or "",
            "火候": step.get("heat") or "",
            "时长": step.get("duration") or "",
            "提示": step.get("tips") or "",
        }
        for index, step in enumerate(steps, start=1)
        if isinstance(step, dict)
    ]


def _render_curation_review(st, config: UIConfig) -> None:
    st.subheader("最终菜谱整理")
    st.caption("按规范菜名对照不同视频的完整用料、步骤和证据，再决定主版本、不同做法、短剪合并或排除。人工决定单独保存，不会修改 outputs 中的原始菜谱。")
    out_dir = Path(config.out_dir).expanduser()
    review_dir = out_dir / DEFAULT_CURATION_REVIEW_DIR
    report_path = review_dir / "recipe-review.json"
    generate_label = "重新扫描输出" if report_path.is_file() else "生成整理清单"
    if st.button(generate_label, type="primary", key="curation_generate"):
        try:
            result = build_curation_review(out_dir, review_dir)
        except Exception as exc:  # noqa: BLE001
            st.error(f"生成整理清单失败：{_clean_error(exc)}")
        else:
            _rerun_with_notice(
                st,
                f"整理清单已更新：{result.duplicate_name_groups} 个同名组，{result.review_item_count} 条待审核来源。",
                clear_prefix="curation_",
            )
    if not report_path.is_file():
        st.info("尚未生成同名菜谱整理清单。点击“生成整理清单”后即可开始。")
        return
    try:
        report = load_curation_review(review_dir)
        decisions = load_curation_decisions(review_dir)
    except Exception as exc:  # noqa: BLE001
        st.error(f"整理数据无法读取：{_clean_error(exc)}")
        return

    groups = [group for group in report.get("groups", []) if isinstance(group, dict)]
    all_items = [
        item
        for group in groups
        for item in group.get("items", [])
        if isinstance(item, dict)
    ]
    resolved_values = {"keep_primary", "keep_variant", "merge_clip", "exclude"}
    resolved_count = sum(_curation_decision(item, decisions) in resolved_values for item in all_items)
    metric_columns = st.columns(4)
    metric_columns[0].metric("审核来源", len(all_items))
    metric_columns[1].metric("已确认", resolved_count)
    metric_columns[2].metric("待确认", len(all_items) - resolved_count)
    metric_columns[3].metric("菜名组", len(groups))
    st.progress(
        resolved_count / len(all_items) if all_items else 1.0,
        text=f"整体进度 {resolved_count}/{len(all_items)}",
    )

    conflicts = curation_decision_conflicts(report, decisions)
    for conflict in conflicts:
        st.warning(conflict)

    download_columns = st.columns([1, 1, 3])
    csv_path = review_dir / "recipe-review.csv"
    if csv_path.is_file():
        download_columns[0].download_button(
            "下载审核清单 CSV",
            data=csv_path.read_bytes(),
            file_name="recipe-review.csv",
            mime="text/csv",
            key="curation_download_csv",
        )
    download_columns[1].download_button(
        "下载人工决定 JSON",
        data=(json.dumps(decisions, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        file_name="curation-decisions.json",
        mime="application/json",
        key="curation_download_decisions",
    )

    filter_columns = st.columns([1, 2])
    status_filter = filter_columns[0].selectbox(
        "审核状态",
        ["待处理", "全部", "已确认", "推广或短剪建议", "名称待确认"],
        key="curation_status_filter",
    )
    query = filter_columns[1].text_input(
        "搜索菜名、视频标题或 BVID",
        key="curation_query",
    ).strip().casefold()

    def group_visible(group: dict[str, Any]) -> bool:
        items = [item for item in group.get("items", []) if isinstance(item, dict)]
        decisions_for_group = [_curation_decision(item, decisions) for item in items]
        if status_filter == "待处理" and all(value in resolved_values for value in decisions_for_group):
            return False
        if status_filter == "已确认" and not all(value in resolved_values for value in decisions_for_group):
            return False
        roles = {str(item.get("suggested_role") or "") for item in items}
        if status_filter == "推广或短剪建议" and not roles.intersection({"exclude_candidate", "short_clip_candidate"}):
            return False
        if status_filter == "名称待确认" and "name_review_candidate" not in roles and not group.get("similar_titles"):
            return False
        if not query:
            return True
        searchable = " ".join(
            [
                str(group.get("title") or ""),
                " ".join(str(value) for value in group.get("similar_titles", [])),
                *[
                    " ".join(
                        (
                            str(item.get("bvid") or ""),
                            str(item.get("video_title") or ""),
                        )
                    )
                    for item in items
                ],
            ]
        ).casefold()
        return query in searchable

    visible_groups = [group for group in groups if group_visible(group)]
    if not visible_groups:
        st.info("当前筛选条件下没有菜名组。")
        return

    group_map = {str(group.get("title") or "未命名"): group for group in visible_groups}

    def group_label(title: str) -> str:
        group = group_map[title]
        items = [item for item in group.get("items", []) if isinstance(item, dict)]
        completed = sum(_curation_decision(item, decisions) in resolved_values for item in items)
        similar = "、".join(str(value) for value in group.get("similar_titles", []))
        suffix = f" | 近似名：{similar}" if similar else ""
        return f"{title} | {completed}/{len(items)} 已确认{suffix}"

    selected_title = st.selectbox(
        "选择菜名组",
        list(group_map),
        format_func=group_label,
        key="curation_group",
    )
    selected_group = group_map[selected_title]
    group_items = [item for item in selected_group.get("items", []) if isinstance(item, dict)]
    similar_titles = [str(value) for value in selected_group.get("similar_titles", [])]
    all_group_map = {str(group.get("title") or ""): group for group in groups}
    comparison_items = list(group_items)
    comparison_item_ids = {_curation_item_id(item) for item in comparison_items}
    for similar_title in similar_titles:
        similar_group = all_group_map.get(similar_title, {})
        for item in similar_group.get("items", []):
            if not isinstance(item, dict) or _curation_item_id(item) in comparison_item_ids:
                continue
            comparison_items.append(item)
            comparison_item_ids.add(_curation_item_id(item))
    if similar_titles:
        st.info(f"规范名核对：当前“{selected_title}”与 {'、'.join(similar_titles)} 仅差一个字。下方已同时载入这些名称的来源，请结合做法判断是错字、别名还是不同菜。")

    summary_rows = []
    for item in comparison_items:
        decision = _curation_decision(item, decisions)
        summary_rows.append(
            {
                "人工决定": CURATION_DECISION_LABELS[decision],
                "当前规范名": item.get("group_title") or "",
                "自动建议": CURATION_ROLE_LABELS.get(str(item.get("suggested_role") or ""), item.get("suggested_role") or ""),
                "BVID": item.get("bvid") or "",
                "视频标题": item.get("video_title") or "",
                "时长(秒)": item.get("duration_seconds") or 0,
                "步骤": item.get("step_count") or 0,
                "用料": item.get("ingredient_count") or 0,
                "质量分": item.get("quality_score"),
                "字幕重合": item.get("transcript_overlap") or 0,
            }
        )
    st.dataframe(summary_rows, width="stretch", hide_index=True)
    if st.button("采用本组自动建议", key=f"curation_accept_group_{_record_key(selected_title)}"):
        updates = []
        for item in group_items:
            saved = _curation_saved_item(decisions, item)
            updates.append(
                {
                    "item_id": _curation_item_id(item),
                    "decision": suggested_curation_decision(str(item.get("suggested_role") or "")),
                    "final_title": saved.get("final_title") or selected_title,
                    "variant_name": saved.get("variant_name") or "",
                    "review_notes": saved.get("review_notes") or "",
                }
            )
        try:
            save_curation_decisions(review_dir, updates)
        except Exception as exc:  # noqa: BLE001
            st.error(f"保存本组建议失败：{_clean_error(exc)}")
        else:
            _rerun_with_notice(st, f"已采用“{selected_title}”的自动建议；仍需核对名称待确认项。")

    item_map = {_curation_item_id(item): item for item in comparison_items}
    selected_item_id = st.selectbox(
        "选择一个来源查看完整内容",
        list(item_map),
        format_func=lambda item_id: (
            f"{CURATION_ROLE_LABELS.get(str(item_map[item_id].get('suggested_role') or ''), '')} | "
            f"{item_map[item_id].get('bvid') or item_id} | {item_map[item_id].get('video_title') or ''}"
        ),
        key=f"curation_item_{_record_key(selected_title)}",
    )
    selected_item = item_map[selected_item_id]
    saved_item = _curation_saved_item(decisions, selected_item)
    role = str(selected_item.get("suggested_role") or "")
    st.markdown(f"**自动判断：{CURATION_ROLE_LABELS.get(role, role)}**")
    st.caption(str(selected_item.get("review_reasons") or ""))
    metadata_columns = st.columns(5)
    metadata_columns[0].metric("时长", f"{float(selected_item.get('duration_seconds') or 0):.0f} 秒")
    metadata_columns[1].metric("步骤", int(selected_item.get("step_count") or 0))
    metadata_columns[2].metric("用料", int(selected_item.get("ingredient_count") or 0))
    metadata_columns[3].metric("质量分", selected_item.get("quality_score") if selected_item.get("quality_score") is not None else "-")
    metadata_columns[4].metric("字幕重合", f"{float(selected_item.get('transcript_overlap') or 0) * 100:.0f}%")
    source_url = str(selected_item.get("source_url") or "").strip()
    if source_url:
        st.link_button("打开原视频核对", source_url)
    related_bvid = str(selected_item.get("related_bvid") or "").strip()
    if related_bvid:
        st.caption(f"最相关来源：{related_bvid}；用于判断当前视频是否为长版节选。")

    recipe, recipe_error = _curation_recipe(selected_item)
    if recipe_error:
        st.error(recipe_error)
    elif recipe is not None:
        uncertain_points = recipe.get("uncertain_points") if isinstance(recipe.get("uncertain_points"), list) else []
        if uncertain_points:
            st.warning("待核对信息：" + "；".join(str(value) for value in uncertain_points if value))
        with st.expander("选中来源：完整用料", expanded=True):
            material_rows = _curation_material_rows(selected_item, recipe)
            if material_rows:
                st.dataframe(material_rows, width="stretch", hide_index=True)
            else:
                st.info("没有提取到用料。")
        with st.expander("选中来源：完整步骤、证据和图片", expanded=True):
            steps = recipe.get("steps") if isinstance(recipe.get("steps"), list) else []
            folder = Path(str(selected_item.get("output_folder") or ""))
            for index, step in enumerate(steps, start=1):
                if not isinstance(step, dict):
                    continue
                st.markdown(f"**{index}. {step.get('title') or step.get('action') or '步骤'}**")
                if step.get("action"):
                    st.write(step["action"])
                details = " · ".join(
                    str(value)
                    for value in (step.get("heat"), step.get("duration"), step.get("tips"))
                    if value
                )
                if details:
                    st.caption(details)
                if step.get("evidence"):
                    st.code(str(step["evidence"]), language="text")
                screenshot = str(step.get("screenshot_path") or "").strip()
                screenshot_path = folder / screenshot if screenshot else None
                if screenshot_path and screenshot_path.is_file():
                    st.image(str(screenshot_path), width=360)

    comparison_materials: list[dict[str, Any]] = []
    comparison_steps: list[dict[str, Any]] = []
    for item in comparison_items:
        group_recipe, _ = _curation_recipe(item)
        if group_recipe is None:
            continue
        comparison_materials.extend(_curation_material_rows(item, group_recipe))
        comparison_steps.extend(_curation_step_rows(item, group_recipe))
    with st.expander("同组横向对比：全部用料"):
        st.dataframe(comparison_materials, width="stretch", hide_index=True)
    with st.expander("同组横向对比：全部步骤"):
        st.dataframe(comparison_steps, width="stretch", hide_index=True)

    st.markdown("#### 保存人工决定")
    decision_values = ["pending", "keep_primary", "keep_variant", "merge_clip", "exclude", "review"]
    current_decision = _curation_decision(selected_item, decisions)
    with st.form(f"curation_decision_form_{_record_key(selected_item_id)}"):
        decision = st.selectbox(
            "处理方式",
            decision_values,
            index=decision_values.index(current_decision),
            format_func=CURATION_DECISION_LABELS.get,
        )
        final_title = st.text_input(
            "最终规范菜名",
            value=str(saved_item.get("final_title") or selected_item.get("group_title") or selected_title),
            help="确认是同一道菜时，相关来源应填写相同的最终菜名。",
        )
        variant_name = st.text_input(
            "做法版本名",
            value=str(saved_item.get("variant_name") or ""),
            placeholder="例如：传统版、家常版、简化版、无香料版",
        )
        review_notes = st.text_area(
            "取舍理由",
            value=str(saved_item.get("review_notes") or ""),
            placeholder="例如：步骤最完整；短视频与长版字幕重合；仅展示成品没有关键火候。",
        )
        save_clicked = st.form_submit_button("保存决定", type="primary")
    if save_clicked:
        try:
            save_curation_decision(
                review_dir,
                selected_item_id,
                decision=decision,
                final_title=final_title,
                variant_name=variant_name,
                review_notes=review_notes,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"保存人工决定失败：{_clean_error(exc)}")
        else:
            _rerun_with_notice(st, f"已保存 {selected_item.get('bvid') or selected_item_id} 的整理决定。")


def _render_recipe_review(st, config: UIConfig) -> None:
    st.subheader("菜谱逐项审核")
    st.caption("中间持续显示完整草稿，右侧集中处理当前条目；长菜谱无需在正文和按钮之间来回滚动。")
    reviewable = [item for item in scan_history(config.out_dir) if item.recipe_path]
    if not reviewable:
        st.info("还没有可以审核的菜谱。")
        return

    options = _history_options(reviewable)
    selected = _select_history_item(st, "选择菜谱", options, "review_select")
    record_key = _record_key(selected.output_folder)
    current_review_path = review_path(selected.output_folder)
    if not current_review_path.exists():
        document_column, action_column = st.columns([2.15, 1], gap="large")
        with document_column:
            st.markdown("#### 草稿正文")
            note = _read_text(selected.note_path)
            if note:
                _render_note_preview(st, note, selected.output_folder)
            else:
                st.info("当前记录没有可预览的 note.md。")
        with action_column:
            _action_rail_marker(st)
            st.markdown("#### 开始审核")
            st.info("这份菜谱还没有审核版。原 recipe.json 不会立即改变。")
            if st.button("创建逐项审核版", type="primary", key=f"review_{record_key}_create", use_container_width=True):
                try:
                    data = _recipe_to_data(selected.recipe_path)  # type: ignore[arg-type]
                    recipe = condense_recipe_steps(_validate_recipe(data), config.max_recipe_steps)
                    path = create_recipe_review(recipe, selected.output_folder)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"创建审核版失败：{_clean_error(exc)}")
                else:
                    _rerun_with_notice(st, f"已创建审核版：{path}")
        return

    try:
        review = load_recipe_review(selected.output_folder)
    except Exception as exc:  # noqa: BLE001
        st.error(f"审核文件无法读取：{_clean_error(exc)}")
        return

    items = review["items"]
    pending_items = [item for item in items if item.get("decision") == "pending"]
    resolved = len(items) - len(pending_items)
    progress_column, reset_column, accept_all_column = st.columns([3, 1, 1])
    with progress_column:
        st.progress(resolved / len(items) if items else 1.0, text=f"已解决 {resolved}/{len(items)} 项")
    with reset_column:
        if st.button("重新创建审核版", key=f"review_{record_key}_reset", use_container_width=True):
            recipe = condense_recipe_steps(
                _validate_recipe(_recipe_to_data(selected.recipe_path)),  # type: ignore[arg-type]
                config.max_recipe_steps,
            )
            create_recipe_review(recipe, selected.output_folder)
            _rerun_with_notice(st, "已重新创建审核版，所有决定已重置。")
    with accept_all_column:
        if st.button(
            "全部采用剩余项",
            disabled=not pending_items,
            key=f"review_{record_key}_accept_all",
            use_container_width=True,
        ):
            accept_all_pending_review_items(selected.output_folder)
            _rerun_with_notice(st, "已采用全部剩余审核项。")

    document_column, action_column = st.columns([2.15, 1], gap="large")
    with document_column:
        st.markdown("#### 草稿正文")
        note = _read_text(selected.note_path)
        if note:
            _render_note_preview(st, note, selected.output_folder)
        else:
            st.info("当前记录没有可预览的 note.md。")

    with action_column:
        _action_rail_marker(st)
        st.markdown("#### 审核操作")
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
                height=220,
                key=editor_key,
            )
            comment = st.text_input(
                "审核备注（可选）",
                value=str(current.get("comment") or ""),
                key=f"review_{record_key}_{current['id']}_comment",
            )
            decision: str | None = None
            if st.button(
                "采用并下一项",
                type="primary",
                key=f"review_{record_key}_accept",
                use_container_width=True,
            ):
                decision = "accepted"
            edit_column, skip_column = st.columns(2)
            with edit_column:
                if st.button("修改后采用", key=f"review_{record_key}_edit", use_container_width=True):
                    decision = "edited"
            with skip_column:
                if st.button("跳过此项", key=f"review_{record_key}_skip", use_container_width=True):
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

        st.divider()
        st.markdown("#### 完成审核")
        if pending_items:
            st.caption(f"还有 {len(pending_items)} 项待处理，完成后才能写回最终菜谱。")
        else:
            st.success("所有审核项已解决，可以应用到最终菜谱。")
            if st.button(
                "应用审核结果并生成最终版",
                type="primary",
                key=f"review_{record_key}_apply",
                use_container_width=True,
            ):
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


def main() -> None:
    import streamlit as st

    _stabilize_arrow_memory_pool()
    st.set_page_config(page_title="Bili Recipe Notes", layout="wide")
    if _meal_mode_requested(st):
        _inject_workspace_styles(st)
        _show_pending_notice(st)
        meal_config = load_config()
        meal_out_dir = st.session_state.get("_meal_out_dir")
        if meal_out_dir:
            meal_config.out_dir = Path(str(meal_out_dir))
        _render_restaurant_meal_ui(st, meal_config)
        return
    st.title("Bili Recipe Notes")
    _show_pending_notice(st)

    next_page = st.session_state.pop("_next_page", None)
    if next_page in PAGES:
        st.session_state["main_page"] = next_page
    _inject_workspace_styles(st)
    active_page = _render_navigation(st)
    config = _render_sidebar(st, load_config())
    st.caption(f"当前工作区：{PAGE_GROUP_BY_PAGE[active_page]} / {active_page}")

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
            col_detail, col_edit, col_review, col_drafts = st.columns(4)
            with col_detail:
                if st.button("查看完整菜谱", key="generated_go_detail"):
                    _navigate_to_record(st, "菜谱详情", last_generated)
            with col_edit:
                if st.button("编辑完整菜谱", key="generated_go_edit"):
                    _navigate_to_record(st, "编辑修复", last_generated)
            with col_review:
                if st.button("逐项审核 AI 结果", key="generated_go_review"):
                    _navigate_to_record(st, "审核确认", last_generated)
            with col_drafts:
                if st.button("查看草稿与归档", key="generated_go_drafts"):
                    _navigate_to_record(st, "草稿与归档", last_generated)

    if active_page == "任务仪表盘":
        _render_background_dashboard(st)

    if active_page == "菜谱库全览":
        _render_library_overview(st, config)

    if active_page == "菜谱详情":
        _render_recipe_detail(st, config)

    if active_page == "本餐点菜":
        _render_meal_planner(st, config)

    if active_page == "烹饪模式":
        _render_cooking_mode(st, config)

    if active_page == "手机客户端":
        _render_mobile_client_admin(st, config)

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
            visible_history, _ = _paged_values(st, filtered, key="history_table_page")
            st.dataframe(
                [
                    {
                        "标题": item.title,
                        "UP主": item.uploader or "",
                        "分类": item.category,
                        "标签": ", ".join(item.tags or []),
                        "喜爱度": rating_stars(item.taste_rating) if item.taste_rating else "未评分",
                        "难度评级": rating_stars(item.difficulty_rating),
                        "时间评级": rating_stars(item.time_rating),
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
                    for item in visible_history
                ],
                width="stretch",
            )
            options = _history_options(filtered)
            selected = _select_history_item(st, "选择记录", options, "history_select")
            history_key = _record_key(selected.output_folder)
            note = _read_text(selected.note_path)
            preview_column, action_column = st.columns([2.15, 1], gap="large")
            with preview_column:
                st.markdown("#### 草稿正文")
                if note:
                    _render_note_preview(st, note, selected.output_folder)
                else:
                    st.info("当前记录没有可预览的 note.md。")
            with action_column:
                _action_rail_marker(st)
                st.markdown("#### 下一步")
                workflow_labels = {
                    "archived": "已归档",
                    "stale": "归档后有修改",
                    "archive_error": "归档异常",
                }
                st.metric("当前状态", workflow_labels.get(selected.workflow_status, "待整理"))
                st.caption("正文固定在左侧；从这里直接进入对应工作区，不必先翻到页面底部。")
                if st.button("查看完整菜谱", key=f"history_{history_key}_rail_detail", use_container_width=True):
                    _navigate_to_record(st, "菜谱详情", selected.output_folder)
                if st.button("编辑完整菜谱", key=f"history_{history_key}_rail_edit", use_container_width=True):
                    _navigate_to_record(st, "编辑修复", selected.output_folder)
                if st.button("逐项审核", key=f"history_{history_key}_rail_review", use_container_width=True):
                    _navigate_to_record(st, "审核确认", selected.output_folder)
                if st.button("最终菜谱整理", key=f"history_{history_key}_rail_curation", use_container_width=True):
                    st.session_state["_next_page"] = "最终菜谱整理"
                    st.rerun()
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
            archive_ratings = _render_rating_controls(
                st,
                selected.recipe_path,
                key_prefix=f"history_{history_key}_rating_",
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
                            ratings=archive_ratings,
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
            if note:
                st.caption("正文预览已固定在上方工作区；下方保留质量报告供归档前复核。")
        else:
            st.info("还没有历史记录，或搜索没有匹配结果。")

    if active_page == "审核确认":
        _render_recipe_review(st, config)

    if active_page == "最终菜谱整理":
        _render_curation_review(st, config)

    if active_page == "批量处理":
        st.subheader("批量处理")
        if st.button("打开后台任务仪表盘", type="primary", key="batch_open_dashboard"):
            st.session_state["_next_page"] = "任务仪表盘"
            st.rerun()
        saved_link_documents = _saved_creator_link_documents(config.out_dir)
        saved_link_options = [""] + [str(path) for path in saved_link_documents]
        if st.session_state.get("batch_saved_creator_links") not in saved_link_options:
            st.session_state["batch_saved_creator_links"] = saved_link_options[1] if saved_link_documents else ""
        selected_saved_links = st.selectbox(
            "已保存的 UP 主链接清单",
            saved_link_options,
            format_func=_creator_link_document_label,
            key="batch_saved_creator_links",
            help="默认选择最近一次抓取的 UP 主清单；创建新批次时会直接读取，不需要粘贴 URL。",
        )
        if selected_saved_links:
            selected_count = len(_load_batch_urls("", "", selected_saved_links))
            st.success(f"已导入最近保存的链接清单，共 {selected_count} 条。")
        elif not saved_link_documents:
            st.info("还没有已保存的 UP 主链接清单；可先到“UP 主链接”页面抓取，或在下面手动输入。")
        with st.expander("手动添加 URL 或导入其他文件（可选）", expanded=not bool(selected_saved_links)):
            links_text = st.text_area("视频 URL，每行一个", height=180)
            links_file = st.text_input("其他链接文件路径", placeholder="outputs/creator_video_links.txt")
        target_label = st.radio(
            "运行到目标阶段",
            ["仅形成原始版", "生成完整菜谱版"],
            horizontal=True,
            help="完整菜谱版会自动补齐缺失的原始版；已有原始版不会重复抓字幕。",
        )
        target_stage = "raw" if target_label == "仅形成原始版" else "recipe"
        skip_existing = st.checkbox("已生成则跳过", value=True)
        st.caption("批量任务在独立后台进程中运行并持续保存进度；刷新页面、关闭浏览器或重启 UI 后仍可恢复查看。")
        try:
            batch_states = list_batch_states()
        except Exception as exc:  # noqa: BLE001
            cached_states = st.session_state.get("_batch_states_cache")
            batch_states = cached_states if isinstance(cached_states, list) else []
            if batch_states:
                st.warning(f"本次读取批次状态失败，暂时显示上次成功读取的数据：{_clean_error(exc)}")
            else:
                st.error(f"读取批次状态失败：{_clean_error(exc)}")
        else:
            st.session_state["_batch_states_cache"] = batch_states
        batch_by_id = {state.batch_id: state for state in batch_states}
        runtime_by_id = {
            state.batch_id: get_background_batch_status(state.batch_id)
            for state in batch_states
        }
        running_batch_ids = [
            state.batch_id
            for state in batch_states
            if runtime_by_id.get(state.batch_id) and runtime_by_id[state.batch_id].status == "running"
        ]
        if running_batch_ids:
            st.success("正在运行的批次：" + "、".join(running_batch_ids))
        overlapping_running_pairs = []
        for index, batch_id in enumerate(running_batch_ids):
            left = batch_by_id[batch_id]
            left_urls = {item.url for item in left.items}
            for other_id in running_batch_ids[index + 1 :]:
                right = batch_by_id[other_id]
                overlap = len(left_urls.intersection(item.url for item in right.items))
                if overlap:
                    overlapping_running_pairs.append(f"{batch_id} 与 {other_id} 重复 {overlap} 条")
        if overlapping_running_pairs:
            st.warning(
                "检测到同时运行的批次包含重复链接，可能争用同一输出目录："
                + "；".join(overlapping_running_pairs)
                + "。请先观察现有任务，不要再次启动相同批次。"
            )
        next_batch_select = st.session_state.pop("_next_batch_select", None)
        preferred_batch_id = _preferred_batch_id(
            batch_states,
            runtime_by_id,
            str(next_batch_select) if next_batch_select else None,
            str(st.session_state.get("batch_select") or ""),
        )
        if preferred_batch_id:
            st.session_state["batch_select"] = preferred_batch_id
        else:
            st.session_state.pop("batch_select", None)

        def batch_label(value: str) -> str:
            state = batch_by_id.get(value)
            if state:
                summary = _batch_progress_summary(state)
                runtime = runtime_by_id.get(value)
                runtime_label = {
                    "running": "运行中",
                    "done": "已完成",
                    "done_with_errors": "完成但有失败",
                    "failed": "后台失败",
                    "stopped": "已停止（可继续）",
                }.get(runtime.status, runtime.status) if runtime else "历史批次"
                return (
                    f"{value} | {runtime_label} | {summary['completed']}/{summary['total']} 已完成"
                    f" | {summary['failed']} 失败"
                )
            return value

        if batch_by_id:
            selected_batch_value = st.selectbox(
                "已有批次",
                list(batch_by_id),
                format_func=batch_label,
                key="batch_select",
            )
            selected_batch_id = str(selected_batch_value).split(" | ", 1)[0]
        else:
            selected_batch_id = None
            st.info("还没有持久化批次。创建后即使刷新或重启网页，也会自动显示最新运行状态。")

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
                    urls = _load_batch_urls(links_text, links_file, selected_saved_links)
                if not urls and run_mode not in {"resume-unfinished", "retry-failed"}:
                    raise ValueError("请先输入 URL 或提供有效的链接文件。")
                if run_mode in {"new-queue", "new-direct"}:
                    overlaps = _running_batch_overlaps(urls, running_batch_ids, batch_by_id)
                    if overlaps:
                        details = "；".join(f"{batch_id} 重复 {count} 条" for batch_id, count in overlaps)
                        raise ValueError(
                            "检测到相同链接正在后台处理，已阻止重复启动："
                            f"{details}。请直接查看现有批次进度。"
                        )

                save_config(config)
                batch_id: str | None = None
                resume_mode = "new"
                if run_mode in {"new-queue", "new-direct"}:
                    batch_id = create_batch_id()
                elif run_mode in {"resume-unfinished", "retry-failed"}:
                    batch_id = selected_batch_id
                    resume_mode = run_mode
                options = BatchJobOptions(
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
                    target_stage=target_stage,
                )
                if run_mode in {"new-queue", "new-direct"}:
                    options_snapshot = {key: value for key, value in options.__dict__.items() if key != "urls"}
                    create_batch_state(urls, options_snapshot, batch_id=batch_id)
                    options.urls = []
                    options.resume_mode = "resume-unfinished"

                def post_process(result) -> None:
                    if not config.auto_archive_after_generation:
                        return
                    completed_folders = [
                        item.output_folder for item in result.items if item.output_folder and item.status == "done"
                    ]
                    if completed_folders:
                        archive_recipe_batch(completed_folders, _vault_path(config))
                        if config.archive_knowledge_with_recipe:
                            _archive_approved_knowledge(config)

                background = start_background_batch(options, on_complete=post_process)
            except Exception as exc:  # noqa: BLE001 - batch failures must not take down the UI
                st.error(f"批量处理失败：{_clean_error(exc)}")
            else:
                st.session_state["_next_batch_select"] = batch_id
                _rerun_with_notice(st, f"批次已在后台启动：{background.batch_id}")

        if selected_batch_id:
            selected_state = batch_by_id.get(selected_batch_id)
            if selected_state:
                st.markdown("#### 批次状态")
                background = runtime_by_id.get(selected_batch_id)
                if background:
                    status_text = {
                        "running": "后台运行中",
                        "done": "后台运行完成",
                        "done_with_errors": "后台运行完成（有失败项）",
                        "failed": "后台运行失败",
                        "stopped": "后台已停止（可继续未完成项）",
                    }.get(background.status, background.status)
                    st.info(f"{status_text}；启动时间：{background.started_at}")
                    if background.error:
                        st.error(f"后台状态：{background.error}")
                summary = _batch_progress_summary(selected_state)
                progress_columns = st.columns(5)
                progress_columns[0].metric("总数", summary["total"])
                progress_columns[1].metric("已完成", summary["completed"])
                progress_columns[2].metric("执行中", summary["running"])
                progress_columns[3].metric("待处理", summary["pending"])
                progress_columns[4].metric("失败", summary["failed"])
                st.progress(
                    summary["completed"] / summary["total"] if summary["total"] else 1.0,
                    text=f"完成进度 {summary['completed']}/{summary['total']}",
                )
                if st.button("刷新批次进度", key=f"refresh_batch_{selected_batch_id}"):
                    st.session_state["_next_batch_select"] = selected_batch_id
                    st.rerun()
                visible_batch_items, _ = _paged_values(
                    st,
                    selected_state.items,
                    key=f"batch_table_page_{selected_batch_id}",
                )
                st.dataframe([_batch_item_row(item) for item in visible_batch_items], width="stretch")
                batch_log = read_batch_log(selected_batch_id)
                if batch_log:
                    st.text_area("后台运行日志（最新）", value=batch_log, height=220, disabled=True)
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
                    batch_ratings = _render_rating_controls(
                        st,
                        batch_folder / "recipe.json",
                        key_prefix=f"batch_{selected_state.batch_id}_{_record_key(batch_folder)}_rating_",
                    )
                    col_detail, col_edit, col_review, col_archive = st.columns(4)
                    with col_detail:
                        if st.button("查看这条", key=f"batch_detail_{selected_state.batch_id}"):
                            _navigate_to_record(st, "菜谱详情", batch_folder)
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
                                    ratings=batch_ratings,
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

    if active_page == "工作交接":
        st.subheader("两台电脑工作交接")
        st.caption(
            "把一个批次的链接、原始字幕和已生成菜谱打成 ZIP；另一台电脑导入后可直接继续未完成任务。"
            "Cookie、临时音视频和本机归档路径不会进入交接包。"
        )
        export_tab, import_tab = st.tabs(["从这台电脑导出", "导入另一台电脑的工作"])

        with export_tab:
            try:
                handoff_states = list_batch_states()
            except Exception as exc:  # noqa: BLE001
                handoff_states = []
                st.error(f"读取批次失败：{_clean_error(exc)}")
            if not handoff_states:
                st.info("当前没有批次。先在“UP 主链接”或“批量处理”页面创建批次。")
            else:
                handoff_by_id = {state.batch_id: state for state in handoff_states}
                export_batch_id = st.selectbox(
                    "选择要交接的批次",
                    list(handoff_by_id),
                    format_func=lambda value: (
                        f"{value} | {len(handoff_by_id[value].items)} 条 | "
                        f"更新于 {handoff_by_id[value].updated_at}"
                    ),
                    key="handoff_export_batch",
                )
                export_state = handoff_by_id[export_batch_id]
                recipe_ready = sum(
                    1 for item in export_state.items if item.stages.get("recipe") and item.stages["recipe"].status == "done"
                )
                raw_ready = sum(
                    1
                    for item in export_state.items
                    if item.stages.get("raw")
                    and item.stages["raw"].status == "done"
                    and not (item.stages.get("recipe") and item.stages["recipe"].status == "done")
                )
                st.info(
                    f"批次共 {len(export_state.items)} 条：菜谱版 {recipe_ready}，仅原始版 {raw_ready}，"
                    f"其余链接也会保留为待执行。"
                )
                destination = st.text_input(
                    "保存位置（可选）",
                    placeholder=f"留空则保存到 {Path(config.out_dir).expanduser() / 'handoffs'}",
                    key="handoff_export_destination",
                    help="也可以直接填 U 盘、移动硬盘或网盘同步目录。若填目录，会自动生成文件名。",
                )
                if st.button("生成工作交接包", type="primary", key="handoff_export_start"):
                    try:
                        with st.spinner("正在整理已完成工作文件..."):
                            exported = export_batch_handoff(
                                export_batch_id,
                                config.out_dir,
                                destination=_optional_text(destination),
                            )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"导出失败：{_clean_error(exc)}")
                    else:
                        st.session_state["last_handoff_export"] = str(exported.path)
                        st.success(
                            f"交接包已生成：菜谱版 {exported.recipe_count}，原始版 {exported.raw_count}，"
                            f"共 {exported.item_count} 条链接。"
                        )
                exported_value = st.session_state.get("last_handoff_export")
                exported_path = Path(exported_value) if isinstance(exported_value, str) else None
                if exported_path and exported_path.is_file():
                    size_mb = exported_path.stat().st_size / 1024**2
                    st.code(str(exported_path), language="text")
                    st.caption(f"文件大小：{size_mb:.1f} MB。可用 AirDrop、U 盘、局域网共享或网盘传到另一台电脑。")
                    open_col, download_col = st.columns(2)
                    with open_col:
                        if st.button("在文件夹中显示", key="handoff_reveal_export"):
                            _open_folder(exported_path.parent)
                    with download_col:
                        if size_mb <= 200:
                            try:
                                handoff_bytes = exported_path.read_bytes()
                            except OSError as exc:
                                st.warning(f"暂时无法读取交接包：{_clean_error(exc)}")
                            else:
                                st.download_button(
                                    "浏览器下载交接包",
                                    data=handoff_bytes,
                                    file_name=exported_path.name,
                                    mime="application/zip",
                                    key="handoff_download_export",
                                )
                        else:
                            st.caption("文件超过 200 MB，请直接从上面的文件路径传输，避免浏览器占用过多内存。")

        with import_tab:
            st.info("导入不会带入另一台电脑的登录信息。首次继续下载前，请在侧栏重新导入本机 Edge Cookie。")
            import_path = st.text_input(
                "交接包路径",
                placeholder="U 盘、共享目录或已经下载的 .handoff.zip 文件",
                key="handoff_import_path",
            )
            uploaded_handoff = st.file_uploader(
                "或者直接选择交接包",
                type=["zip"],
                key="handoff_import_upload",
                help="大文件更推荐填写上面的本地路径。",
            )
            import_clicked = st.button(
                "校验并导入",
                type="primary",
                disabled=not import_path.strip() and uploaded_handoff is None,
                key="handoff_import_start",
            )
            if import_clicked:
                temporary_path: Path | None = None
                try:
                    source = Path(import_path).expanduser() if import_path.strip() else None
                    if source is None and uploaded_handoff is not None:
                        suffix = Path(uploaded_handoff.name).suffix or ".zip"
                        with tempfile.NamedTemporaryFile(prefix="bili-handoff-", suffix=suffix, delete=False) as file:
                            file.write(uploaded_handoff.getbuffer())
                            temporary_path = Path(file.name)
                        source = temporary_path
                    if source is None:
                        raise ValueError("请选择交接包。")
                    with st.spinner("正在校验并恢复工作文件..."):
                        imported = import_handoff_bundle(source, config.out_dir)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"导入失败：{_clean_error(exc)}")
                else:
                    st.session_state["last_handoff_import"] = imported
                    st.session_state["batch_select"] = imported.batch_id
                    st.success(
                        f"导入完成：菜谱版 {imported.recipe_count}，仅原始版 {imported.raw_count}，"
                        f"待执行 {imported.pending_count}。"
                    )
                    if imported.backup_count:
                        st.caption(f"有 {imported.backup_count} 个同名文件在更新前保留了 .bak。")
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)

            imported = st.session_state.get("last_handoff_import")
            if imported:
                st.code(str(imported.batch_path), language="text")
                if st.button("打开批次并继续处理", type="primary", key="handoff_go_batch"):
                    st.session_state["_next_batch_select"] = imported.batch_id
                    st.session_state["_next_page"] = "批量处理"
                    st.rerun()

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
                recipe_data = _dump_model(normalize_recipe_taxonomy(_validate_recipe(recipe_data)))
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
                rating_col_taste, rating_col_difficulty, rating_col_time = st.columns(3)
                with rating_col_taste:
                    recipe_data["taste_rating"] = st.selectbox(
                        "个人喜爱度",
                        [None, 1, 2, 3, 4, 5],
                        index=int(recipe_data.get("taste_rating") or 0),
                        format_func=_rating_option_label,
                        key=f"{state_prefix}taste_rating",
                    )
                with rating_col_difficulty:
                    recipe_data["difficulty_rating"] = st.selectbox(
                        "烹饪难度评级",
                        [1, 2, 3, 4, 5],
                        index=int(recipe_data.get("difficulty_rating") or 1) - 1,
                        format_func=_rating_option_label,
                        key=f"{state_prefix}difficulty_rating",
                        help="系统根据步骤和技法自动给出初值，可手动修改。",
                    )
                with rating_col_time:
                    recipe_data["time_rating"] = st.selectbox(
                        "时间投入评级",
                        [1, 2, 3, 4, 5],
                        index=int(recipe_data.get("time_rating") or 1) - 1,
                        format_func=_rating_option_label,
                        key=f"{state_prefix}time_rating",
                        help="系统根据总耗时自动给出初值，可手动修改。",
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
                        edited_ratings = None
                        if recipe_data is not None:
                            edited_ratings = {
                                "taste_rating": recipe_data.get("taste_rating"),
                                "difficulty_rating": recipe_data.get("difficulty_rating"),
                                "time_rating": recipe_data.get("time_rating"),
                            }
                        archived, knowledge_results, knowledge_error = _archive_output(
                            selected.output_folder,
                            config,
                            overwrite=force_edit_archive,
                            ratings=edited_ratings,
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

            st.markdown("#### 步骤图片")
            st.caption("自动截图会比较多个时间点；候选图只临时保存在当前会话，最终每道菜最多保留 4 张压缩图片。")
            steps = recipe_data.get("steps", []) if recipe_data else []
            if not isinstance(steps, list) or not steps:
                st.info("当前菜谱没有可重截的步骤。")
            else:
                pending_image_count = sum(
                    isinstance(step, dict) and step.get("screenshot_status") == "needs_review"
                    for step in steps
                )
                if pending_image_count:
                    st.warning(f"有 {pending_image_count} 个步骤的自动候选质量不足，建议人工确认；也可以明确选择不配图。")
                step_indexes = list(range(1, len(steps) + 1))

                def screenshot_step_label(index: int) -> str:
                    step = steps[index - 1]
                    status = str(step.get("screenshot_status") or "")
                    marker = "待选图" if status == "needs_review" else "已有图" if step.get("screenshot_path") else "无图"
                    return f"{index}. {step.get('title') or '未命名步骤'} · {marker}"

                step_index = st.selectbox(
                    "选择步骤",
                    step_indexes,
                    format_func=screenshot_step_label,
                    key=f"{state_prefix}screenshot_step",
                )
                current_step = steps[int(step_index) - 1]
                initial_timestamp = _nonnegative_float(current_step.get("start_time"))
                current_image = _local_markdown_image(
                    selected.output_folder,
                    str(current_step.get("screenshot_path") or ""),
                )
                screenshot_status = str(current_step.get("screenshot_status") or "")
                screenshot_score = current_step.get("screenshot_score")
                if current_image and current_image.is_file():
                    st.image(str(current_image), caption="当前保存图片", width=420)
                if screenshot_status == "needs_review":
                    st.warning("自动候选质量不足，因此没有强行配图。可以查看候选、精确重截、上传图片或选择不配图。")
                elif screenshot_status == "none":
                    st.info("此步骤已明确设置为不配图。")
                elif screenshot_score is not None:
                    st.caption(f"当前图片质量评分：{float(screenshot_score) * 100:.0f}%")
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

                candidate_key = f"{state_prefix}screenshot_candidates_{step_index}"
                candidate_action, clear_action = st.columns(2)
                with candidate_action:
                    if st.button(
                        "自动查找候选图",
                        key=f"{state_prefix}suggest_screenshots_{step_index}",
                        use_container_width=True,
                    ):
                        try:
                            with st.spinner("正在截取并比较多个候选画面..."):
                                for session_key in list(st.session_state):
                                    if str(session_key).startswith(f"{state_prefix}screenshot_candidates_"):
                                        st.session_state.pop(session_key, None)
                                st.session_state[candidate_key] = suggest_step_screenshots(
                                    selected.output_folder,
                                    int(step_index),
                                    cookies=_optional_text(config.cookies),
                                    video_path=_optional_text(video_path),
                                    keep_video=config.keep_media,
                                )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"候选图生成失败：{_clean_error(exc)}")
                with clear_action:
                    if st.button(
                        "此步骤不配图",
                        disabled=not confirm_overwrite,
                        key=f"{state_prefix}clear_screenshot_{step_index}",
                        use_container_width=True,
                    ):
                        try:
                            backups = _backup_files(
                                [current_image, selected.recipe_path, selected.note_path],
                                "remove-screenshot",
                            )
                            clear_step_screenshot(selected.output_folder, int(step_index))
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"移除图片失败：{_clean_error(exc)}")
                        else:
                            _rerun_with_notice(
                                st,
                                f"此步骤已设为不配图{_backup_summary(backups)}",
                                clear_prefix=state_prefix,
                            )

                candidates = st.session_state.get(candidate_key, [])
                if candidates:
                    st.markdown("##### 候选图片（质量较高者优先）")
                    candidate_columns = st.columns(3)
                    for candidate_index, candidate in enumerate(candidates):
                        with candidate_columns[candidate_index % len(candidate_columns)]:
                            st.image(candidate.content, width="stretch")
                            timestamp_label = (
                                f"{candidate.timestamp:.1f} 秒" if candidate.timestamp is not None else "本地图片"
                            )
                            st.caption(f"{timestamp_label} · 质量 {candidate.score * 100:.0f}%")
                            if st.button(
                                "采用这张",
                                disabled=not confirm_overwrite,
                                key=f"{state_prefix}use_candidate_{step_index}_{candidate_index}",
                                use_container_width=True,
                            ):
                                try:
                                    target_image = selected.output_folder / "images" / f"step_{int(step_index):02d}.jpg"
                                    backups = _backup_files(
                                        [target_image, selected.recipe_path, selected.note_path],
                                        "choose-screenshot",
                                    )
                                    image_path = save_step_screenshot_candidate(
                                        selected.output_folder,
                                        int(step_index),
                                        candidate,
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    st.error(f"保存候选图失败：{_clean_error(exc)}")
                                else:
                                    _rerun_with_notice(
                                        st,
                                        f"已采用：{image_path}{_backup_summary(backups)}",
                                        clear_prefix=state_prefix,
                                    )
                elif candidate_key in st.session_state:
                    st.warning("这个步骤范围内没有截到可读取的候选画面，可以扩大时间点精确重截、上传图片或不配图。")

                uploaded_image = st.file_uploader(
                    "上传替代图片",
                    type=["jpg", "jpeg", "png", "webp"],
                    key=f"{state_prefix}uploaded_screenshot_{step_index}",
                    help="图片会转成压缩 JPEG 后保存，原文件不会复制进菜谱目录。",
                )
                if st.button(
                    "采用上传图片",
                    disabled=uploaded_image is None or not confirm_overwrite,
                    key=f"{state_prefix}use_uploaded_screenshot_{step_index}",
                ):
                    try:
                        uploaded_content = uploaded_image.getvalue()
                        if len(uploaded_content) > 10 * 1024 * 1024:
                            raise ValueError("上传图片不能超过 10 MB")
                        target_image = selected.output_folder / "images" / f"step_{int(step_index):02d}.jpg"
                        backups = _backup_files(
                            [target_image, selected.recipe_path, selected.note_path],
                            "upload-screenshot",
                        )
                        image_path = save_uploaded_step_screenshot(
                            selected.output_folder,
                            int(step_index),
                            uploaded_content,
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"上传图片保存失败：{_clean_error(exc)}")
                    else:
                        _rerun_with_notice(
                            st,
                            f"已采用上传图片：{image_path}{_backup_summary(backups)}",
                            clear_prefix=state_prefix,
                        )

                st.markdown("##### 按时间点精确重截")
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
                            keep_video=config.keep_media,
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
        st.subheader("导入 UP 主全部视频")
        st.caption("抓取完成后总会保存独立链接清单；可以只创建待执行批次，稍后再生成菜谱。")
        home_url = st.text_input("UP 主主页 URL", placeholder="https://space.bilibili.com/123456/video")
        if st.button("抓取并保存全部链接", type="primary", disabled=not home_url.strip()):
            log = _log_box(st, height=180)
            try:
                with st.spinner("正在递归提取投稿和合集..."):
                    archive_result = crawl_and_archive_creator(
                        url=home_url.strip(),
                        cookies=_optional_text(config.cookies),
                        out=config.out_dir,
                        log=log,
                    )
            except Exception as exc:  # noqa: BLE001
                error_message = _clean_error(exc)
                log(error_message)
                st.error(f"提取失败：{error_message}")
            else:
                st.session_state["creator_archive_result"] = archive_result
                st.session_state["batch_saved_creator_links"] = str(archive_result.links_path)

        archive_result = st.session_state.get("creator_archive_result")
        if archive_result:
            crawl = archive_result.crawl
            st.success(f"{crawl.uploader}：已保存 {len(crawl.videos)} 个视频链接。")
            st.caption(f"链接清单：{archive_result.links_path}")
            st.caption(f"结构化清单：{archive_result.manifest_path}")
            if crawl.warnings:
                st.warning("\n".join(crawl.warnings))
            accept_partial = crawl.complete or st.checkbox(
                "我确认接受当前不完整结果并继续创建批次",
                value=False,
                key=f"creator_partial_{crawl.uid}",
            )
            result_version = archive_result.manifest_path.stat().st_mtime_ns
            selection_key = f"creator_selected_{crawl.uid}_{result_version}"
            revision_key = f"creator_selection_revision_{crawl.uid}_{result_version}"
            if selection_key not in st.session_state:
                st.session_state[selection_key] = [video.url for video in crawl.videos]
                st.session_state[revision_key] = 0
            selected_set = set(st.session_state[selection_key])
            select_all_col, clear_all_col = st.columns(2)
            with select_all_col:
                if st.button("全选全部视频", key=f"creator_select_all_{crawl.uid}_{result_version}"):
                    st.session_state[selection_key] = [video.url for video in crawl.videos]
                    st.session_state[revision_key] = int(st.session_state.get(revision_key, 0)) + 1
                    st.rerun()
            with clear_all_col:
                if st.button("清空全部选择", key=f"creator_clear_all_{crawl.uid}_{result_version}"):
                    st.session_state[selection_key] = []
                    st.session_state[revision_key] = int(st.session_state.get(revision_key, 0)) + 1
                    st.rerun()
            visible_videos, page_start = _paged_values(
                st,
                list(crawl.videos),
                key=f"creator_table_page_{crawl.uid}_{result_version}",
            )
            rows = [
                {"选择": video.url in selected_set, "标题": video.title, "BV": video.bvid, "URL": video.url}
                for video in visible_videos
            ]
            edited_rows = st.data_editor(
                rows,
                hide_index=True,
                disabled=["标题", "BV", "URL"],
                width="stretch",
                key=(
                    f"creator_videos_{crawl.uid}_{result_version}_{page_start}_"
                    f"{st.session_state.get(revision_key, 0)}"
                ),
            )
            for row in edited_rows:
                url = str(row.get("URL") or "")
                if not url:
                    continue
                if row.get("选择"):
                    selected_set.add(url)
                else:
                    selected_set.discard(url)
            selected_urls = [video.url for video in crawl.videos if video.url in selected_set]
            st.session_state[selection_key] = selected_urls
            st.caption(
                f"已选择 {len(selected_urls)} / {len(crawl.videos)} 个视频；链接文档仍保留全部视频。"
            )
            creator_target_label = st.radio(
                "立即运行时的目标阶段",
                ["仅形成原始版", "生成完整菜谱版"],
                horizontal=True,
                key=f"creator_target_{crawl.uid}",
            )
            creator_target = "raw" if creator_target_label == "仅形成原始版" else "recipe"

            creator_batch_options = BatchJobOptions(
                urls=selected_urls,
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
                skip_existing=True,
                target_stage=creator_target,
            )
            deferred_col, immediate_col = st.columns(2)
            can_create = bool(selected_urls) and bool(accept_partial)
            with deferred_col:
                defer_clicked = st.button(
                    "保存清单并创建待执行批次",
                    disabled=not can_create,
                    use_container_width=True,
                )
            with immediate_col:
                run_clicked = st.button(
                    "保存清单并立即运行",
                    type="primary",
                    disabled=not can_create,
                    use_container_width=True,
                )
            if defer_clicked:
                batch_id = create_batch_id()
                options_snapshot = {
                    key: value for key, value in creator_batch_options.__dict__.items() if key != "urls"
                }
                state = create_batch_state(selected_urls, options_snapshot, batch_id=batch_id)
                st.success(f"待执行批次已创建：{state.batch_id}，共 {len(state.items)} 条。")
            if run_clicked:
                batch_id = create_batch_id()
                creator_batch_options.batch_id = batch_id
                try:
                    options_snapshot = {
                        key: value for key, value in creator_batch_options.__dict__.items() if key != "urls"
                    }
                    create_batch_state(selected_urls, options_snapshot, batch_id=batch_id)
                    creator_batch_options.urls = []
                    creator_batch_options.resume_mode = "resume-unfinished"

                    def post_process_creator(result) -> None:
                        if not config.auto_archive_after_generation:
                            return
                        completed_folders = [
                            item.output_folder for item in result.items if item.output_folder and item.status == "done"
                        ]
                        if completed_folders:
                            archive_recipe_batch(completed_folders, _vault_path(config))
                            if config.archive_knowledge_with_recipe:
                                _archive_approved_knowledge(config)

                    background = start_background_batch(
                        creator_batch_options,
                        on_complete=post_process_creator,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"批次启动失败：{_clean_error(exc)}")
                else:
                    st.session_state["creator_active_batch_id"] = batch_id
                    st.success(f"批次已在后台启动：{background.batch_id}。可到“批量处理”页面查看进度。")

            active_creator_batch = st.session_state.get("creator_active_batch_id")
            if active_creator_batch:
                background = get_background_batch_status(str(active_creator_batch))
                if background:
                    st.info(f"后台批次 {active_creator_batch}：{background.status}")
                creator_log = read_batch_log(str(active_creator_batch))
                if creator_log:
                    st.text_area("后台运行日志（最新）", value=creator_log, height=180, disabled=True)

            content = archive_result.links_path.read_text(encoding="utf-8")
            st.download_button(
                "下载全部视频链接",
                data=content,
                file_name=f"{crawl.uid}-video_links.txt",
                mime="text/plain",
            )


if __name__ == "__main__":
    main()
