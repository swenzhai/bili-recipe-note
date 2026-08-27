from __future__ import annotations

import hashlib
import csv
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR_NAME
from .content_analysis import _recipe_to_context, _read_json, _read_text, _transcript_to_text
from .llm import clean_llm_markdown_output, complete_markdown_prompt, get_last_llm_error
from .storage import CorruptDataError, atomic_write_json, atomic_write_text, file_lock, read_json


KNOWLEDGE_BASE_FILE_NAME = "knowledge_base.json"
DOCUMENT_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
KNOWLEDGE_CATEGORIES = ("技巧", "原理", "食材处理", "火候", "调味", "工具", "避坑", "其他")
KNOWLEDGE_QUERY_ALIASES = {
    "除腥": "去腥",
    "醒面": "松弛",
    "收汁": "勾芡",
    "煎锅": "平底锅",
    "二发": "最终发酵",
}
JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n(?P<body>.*?)(?:\n)?```\s*$", re.DOTALL | re.IGNORECASE)


@dataclass
class CookingKnowledgeEntry:
    id: str
    title: str
    category: str
    content: str
    rationale: str = ""
    applicable_to: list[str] = field(default_factory=list)
    evidence: str = ""
    tags: list[str] = field(default_factory=list)
    confidence: float | None = None
    source_title: str = ""
    source_url: str = ""
    source_output_folder: str = ""
    source_refs: list[dict[str, str]] = field(default_factory=list)
    source_kind: str = "video"
    source_excerpt: str = ""
    review_status: str = "draft"
    mastery: str = "new"
    review_count: int = 0
    last_reviewed_at: str = ""
    next_review_at: str = ""
    practice_records: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class KnowledgeExtractionOptions:
    llm_provider: str = "opencode"
    openai_model: str = "gpt-5.5"
    local_llm_command: str | None = None
    codex_model: str | None = None
    codex_profile: str | None = None
    llm_cli_extra_instructions: str | None = None


@dataclass
class KnowledgeExtractionResult:
    knowledge_path: Path
    entries: list[CookingKnowledgeEntry]
    added_count: int
    updated_count: int


@dataclass
class KnowledgeBatchExtractionItem:
    output_folder: Path
    status: str
    added_count: int = 0
    updated_count: int = 0
    error: str = ""


@dataclass
class KnowledgeBatchExtractionResult:
    knowledge_path: Path
    items: list[KnowledgeBatchExtractionItem]
    added_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0


def knowledge_base_path(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return root / CONFIG_DIR_NAME / KNOWLEDGE_BASE_FILE_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _clean_source_refs(value: Any) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            ref = {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "output_folder": str(item.get("output_folder") or "").strip(),
                "evidence": str(item.get("evidence") or "").strip(),
                "excerpt": str(item.get("excerpt") or "").strip(),
            }
            if any(ref.values()):
                refs.append(ref)
    return refs


def _source_ref(entry: CookingKnowledgeEntry) -> dict[str, str] | None:
    ref = {
        "title": entry.source_title.strip(),
        "url": entry.source_url.strip(),
        "output_folder": entry.source_output_folder.strip(),
        "evidence": entry.evidence.strip(),
        "excerpt": entry.source_excerpt.strip(),
    }
    return ref if any(ref.values()) else None


def _merge_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in items:
        cleaned = str(item).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        merged.append(cleaned)
    return merged


def _merge_source_refs(entries: list[CookingKnowledgeEntry]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        candidates = list(entry.source_refs)
        ref = _source_ref(entry)
        if ref:
            candidates.append(ref)
        for item in candidates:
            key = (item.get("title", ""), item.get("url", ""), item.get("output_folder", ""))
            if key in seen:
                continue
            seen.add(key)
            refs.append(item)
    return refs


def _entry_id(title: str, content: str, source_url: str) -> str:
    raw = "\n".join([title.strip(), content.strip(), source_url.strip()])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _entry_from_dict(data: dict[str, Any]) -> CookingKnowledgeEntry:
    title = str(data.get("title") or "").strip()
    content = str(data.get("content") or "").strip()
    source_url = str(data.get("source_url") or "").strip()
    entry_id = str(data.get("id") or "").strip() or _entry_id(title, content, source_url)
    return CookingKnowledgeEntry(
        id=entry_id,
        title=title,
        category=str(data.get("category") or "其他").strip() or "其他",
        content=content,
        rationale=str(data.get("rationale") or "").strip(),
        applicable_to=_clean_list(data.get("applicable_to")),
        evidence=str(data.get("evidence") or "").strip(),
        tags=_clean_list(data.get("tags")),
        confidence=float(data["confidence"]) if isinstance(data.get("confidence"), (int, float)) else None,
        source_title=str(data.get("source_title") or "").strip(),
        source_url=source_url,
        source_output_folder=str(data.get("source_output_folder") or "").strip(),
        source_refs=_clean_source_refs(data.get("source_refs")),
        source_kind=str(data.get("source_kind") or "video").strip() or "video",
        source_excerpt=str(data.get("source_excerpt") or "").strip(),
        review_status=(
            str(data.get("review_status") or "draft").strip()
            if str(data.get("review_status") or "draft").strip() in {"draft", "approved"}
            else "draft"
        ),
        mastery=str(data.get("mastery") or "new").strip() or "new",
        review_count=int(data.get("review_count") or 0),
        last_reviewed_at=str(data.get("last_reviewed_at") or "").strip(),
        next_review_at=str(data.get("next_review_at") or "").strip(),
        practice_records=data.get("practice_records") if isinstance(data.get("practice_records"), list) else [],
        created_at=str(data.get("created_at") or "").strip(),
        updated_at=str(data.get("updated_at") or "").strip(),
    )


def _load_knowledge_entries_from_path(path: Path) -> list[CookingKnowledgeEntry]:
    try:
        raw = read_json(path, expected_type=(dict, list))
    except CorruptDataError:
        raise
    if isinstance(raw, dict):
        if "entries" not in raw:
            raise CorruptDataError(f"Invalid knowledge base in {path}: missing entries field.")
        raw_entries = raw["entries"]
    else:
        raw_entries = raw
    if not isinstance(raw_entries, list):
        raise CorruptDataError(f"Invalid knowledge base in {path}: entries must be a list.")
    entries: list[CookingKnowledgeEntry] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise CorruptDataError(f"Invalid knowledge entry #{index + 1} in {path}: expected an object.")
        try:
            entry = _entry_from_dict(item)
        except (TypeError, ValueError) as exc:
            raise CorruptDataError(f"Invalid knowledge entry #{index + 1} in {path}: {exc}") from exc
        if not entry.title or not entry.content:
            raise CorruptDataError(
                f"Invalid knowledge entry #{index + 1} in {path}: title and content are required."
            )
        if entry.id in seen_ids:
            raise CorruptDataError(f"Duplicate knowledge entry id {entry.id!r} in {path}.")
        seen_ids.add(entry.id)
        entries.append(entry)
    return entries


def load_knowledge_entries(project_root: Path | None = None) -> list[CookingKnowledgeEntry]:
    path = knowledge_base_path(project_root)
    if not path.exists():
        return []
    return _load_knowledge_entries_from_path(path)


def knowledge_quality_issues(entry: CookingKnowledgeEntry) -> list[str]:
    """Return actionable quality warnings without rejecting a valid draft."""
    issues: list[str] = []
    if not entry.source_url and not entry.source_output_folder:
        issues.append("缺少可回溯来源")
    if not entry.evidence:
        issues.append("缺少原文或视频依据")
    if len(entry.content) < 20:
        issues.append("可执行内容过短")
    if not entry.applicable_to:
        issues.append("未填写适用场景")
    if entry.confidence is not None and not 0 <= entry.confidence <= 1:
        issues.append("置信度应在 0 到 1 之间")
    return issues


def list_knowledge_backups(project_root: Path | None = None) -> list[Path]:
    path = knowledge_base_path(project_root)
    if not path.parent.exists():
        return []
    backups = list(path.parent.glob(f"{path.stem}.before-*{path.suffix}"))
    fixed = path.with_suffix(path.suffix + ".bak")
    if fixed.is_file():
        backups.append(fixed)
    return sorted((item for item in backups if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True)


def restore_knowledge_backup(backup: str | Path, project_root: Path | None = None) -> Path:
    backup_path = Path(backup).expanduser().resolve()
    path = knowledge_base_path(project_root)
    if not backup_path.is_file() or backup_path.parent != path.parent.resolve():
        raise ValueError("知识库备份路径无效")
    entries = _load_knowledge_entries_from_path(backup_path)
    return save_knowledge_entries(entries, project_root=project_root)


def ocr_image_text(image_path: str | Path) -> str:
    path = Path(image_path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in DOCUMENT_IMAGE_SUFFIXES:
        raise ValueError("不是受支持的图片文件")
    try:
        import pytesseract
        from PIL import Image

        text = pytesseract.image_to_string(Image.open(path), lang="chi_sim+eng")
        if text.strip():
            return text
    except ImportError:
        pass
    except Exception:
        pass
    for language in ("chi_sim+eng", "eng"):
        try:
            result = subprocess.run(
                ["tesseract", str(path), "stdout", "-l", language],
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        if result.stdout.strip():
            return result.stdout
    raise RuntimeError("无法识别图片文字，请安装 pytesseract 与 Tesseract，或先导出 OCR 文本")


def read_document_text(document_path: str | Path) -> str:
    """Read OCR/text content from a PDF or a plain text export."""
    path = Path(document_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"文档不存在：{path}")
    if path.suffix.lower() in {".txt", ".md", ".markdown"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in DOCUMENT_IMAGE_SUFFIXES:
        return ocr_image_text(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError("仅支持 PDF、TXT 或 Markdown 文档")
    try:
        from pypdf import PdfReader

        pages = [page.extract_text() or "" for page in PdfReader(str(path)).pages]
        text = "\n\n".join(f"[第 {index + 1} 页]\n{page}" for index, page in enumerate(pages))
        if text.strip():
            return text
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"PDF 文本读取失败：{exc}") from exc
    try:
        result = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("无法读取 PDF 文本，请先安装 pypdf 或 pdftotext，或上传 OCR TXT") from exc
    return result.stdout


def build_document_knowledge_prompt(document_text: str, source_title: str = "") -> str:
    return (
        "请从以下书籍/PDF/OCR资料中提取可迁移的通用烹饪知识。"
        "只提取技巧、原理、判断标准、避坑经验、食材处理、调味逻辑和工具方法；不要输出完整菜谱步骤。"
        "每条保留页码或原文摘录作为 evidence，无法确认的内容不要杜撰。输出 JSON 数组。\n"
        "字段：title、category（技巧/原理/食材处理/火候/调味/工具/避坑/其他）、content、rationale、"
        "applicable_to（数组）、evidence、tags（数组）、confidence（0 到 1）。\n\n"
        f"资料标题：{source_title or '未命名文档'}\n\n资料正文：\n{document_text}"
    )


def extract_knowledge_from_document(
    document_path: str | Path,
    options: KnowledgeExtractionOptions | None = None,
    project_root: Path | None = None,
) -> KnowledgeExtractionResult:
    path = Path(document_path).expanduser().resolve()
    text = read_document_text(path)
    if not text.strip():
        raise ValueError("文档没有可提取的文字，请先进行 OCR")
    opts = options or KnowledgeExtractionOptions()
    output = complete_markdown_prompt(
        build_document_knowledge_prompt(text, path.stem),
        provider=opts.llm_provider,
        openai_model=opts.openai_model,
        local_llm_command=opts.local_llm_command,
        codex_model=opts.codex_model,
        codex_profile=opts.codex_profile,
        cli_extra_instructions=opts.llm_cli_extra_instructions,
    )
    if not output:
        detail = get_last_llm_error()
        raise RuntimeError(f"文档知识提取失败：{detail or opts.llm_provider}")
    entries = parse_knowledge_entries(
        output,
        source={
            "source_title": path.stem,
            "source_url": path.as_uri(),
            "source_kind": "image" if path.suffix.lower() in DOCUMENT_IMAGE_SUFFIXES else "document",
            "source_excerpt": text[:500],
        },
    )
    knowledge_path, added, updated = upsert_knowledge_entries(entries, project_root=project_root)
    return KnowledgeExtractionResult(knowledge_path, entries, added, updated)


def _write_knowledge_entries(path: Path, entries: list[CookingKnowledgeEntry]) -> Path:
    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("Knowledge entry ids must be unique.")
    if any(not entry.id or not entry.title or not entry.content for entry in entries):
        raise ValueError("Each knowledge entry requires a non-empty id, title, and content.")
    payload = {
        "version": 1,
        "updated_at": _now(),
        "entries": [asdict(entry) for entry in entries],
    }
    return atomic_write_json(path, payload)


def save_knowledge_entries(entries: list[CookingKnowledgeEntry], project_root: Path | None = None) -> Path:
    path = knowledge_base_path(project_root)
    with file_lock(path):
        if path.exists():
            _load_knowledge_entries_from_path(path)
        return _write_knowledge_entries(path, entries)


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for chunk in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            if len(chunk) == 1:
                tokens.add(chunk)
            else:
                tokens.update(chunk[idx : idx + 2] for idx in range(len(chunk) - 1))
        else:
            tokens.add(chunk)
    return tokens


def _entry_text(entry: CookingKnowledgeEntry) -> str:
    return " ".join([entry.title, entry.category, entry.content, entry.rationale, " ".join(entry.tags)])


def knowledge_similarity(left: CookingKnowledgeEntry, right: CookingKnowledgeEntry) -> float:
    left_tokens = _tokenize(_entry_text(left))
    right_tokens = _tokenize(_entry_text(right))
    if not left_tokens or not right_tokens:
        return 0.0
    score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    if left.category and right.category and left.category == right.category:
        score += 0.1
    if left.title and right.title and left.title == right.title:
        score += 0.35
    return min(score, 1.0)


def _merge_entry(primary: CookingKnowledgeEntry, incoming: CookingKnowledgeEntry) -> CookingKnowledgeEntry:
    now = _now()
    primary.applicable_to = _merge_unique([*primary.applicable_to, *incoming.applicable_to])
    primary.tags = _merge_unique([*primary.tags, *incoming.tags])
    if incoming.evidence and incoming.evidence not in primary.evidence:
        primary.evidence = "；".join(_merge_unique([primary.evidence, incoming.evidence]))
    if not primary.rationale and incoming.rationale:
        primary.rationale = incoming.rationale
    if not primary.source_title:
        primary.source_title = incoming.source_title
    if not primary.source_url:
        primary.source_url = incoming.source_url
    if not primary.source_output_folder:
        primary.source_output_folder = incoming.source_output_folder
    primary.source_refs = _merge_source_refs([primary, incoming])
    if incoming.review_status == "approved":
        primary.review_status = "approved"
    if primary.confidence is None:
        primary.confidence = incoming.confidence
    elif incoming.confidence is not None:
        primary.confidence = max(primary.confidence, incoming.confidence)
    primary.updated_at = now
    return primary


def find_similar_knowledge_entries(
    entry: CookingKnowledgeEntry,
    entries: list[CookingKnowledgeEntry] | None = None,
    threshold: float = 0.55,
    project_root: Path | None = None,
) -> list[tuple[CookingKnowledgeEntry, float]]:
    pool = entries if entries is not None else load_knowledge_entries(project_root)
    matches = [(candidate, knowledge_similarity(entry, candidate)) for candidate in pool if candidate.id != entry.id]
    return sorted((item for item in matches if item[1] >= threshold), key=lambda item: item[1], reverse=True)


def suggest_duplicate_groups(
    threshold: float = 0.55,
    project_root: Path | None = None,
) -> list[list[CookingKnowledgeEntry]]:
    entries = load_knowledge_entries(project_root)
    groups: list[list[CookingKnowledgeEntry]] = []
    used: set[str] = set()
    for entry in entries:
        if entry.id in used:
            continue
        matches = [candidate for candidate, _ in find_similar_knowledge_entries(entry, entries, threshold)]
        group = [entry, *[item for item in matches if item.id not in used]]
        if len(group) > 1:
            groups.append(group)
            used.update(item.id for item in group)
    return groups


def upsert_knowledge_entries(
    entries: list[CookingKnowledgeEntry],
    project_root: Path | None = None,
) -> tuple[Path, int, int]:
    path = knowledge_base_path(project_root)
    with file_lock(path):
        existing = _load_knowledge_entries_from_path(path) if path.exists() else []
        by_id = {entry.id: entry for entry in existing}
        added = 0
        updated = 0
        now = _now()
        for entry in entries:
            if not entry.title or not entry.content:
                continue
            old = by_id.get(entry.id)
            if old:
                entry.created_at = old.created_at or now
                entry.updated_at = now
                by_id[entry.id] = _merge_entry(old, entry)
                updated += 1
            else:
                similar = find_similar_knowledge_entries(entry, list(by_id.values()), threshold=0.66)
                if similar:
                    by_id[similar[0][0].id] = _merge_entry(similar[0][0], entry)
                    updated += 1
                else:
                    entry.created_at = entry.created_at or now
                    entry.updated_at = now
                    entry.source_refs = _merge_source_refs([entry])
                    by_id[entry.id] = entry
                    added += 1
        sorted_entries = sorted(by_id.values(), key=lambda item: item.updated_at or item.created_at, reverse=True)
        return _write_knowledge_entries(path, sorted_entries), added, updated


def update_knowledge_entry(entry_id: str, updates: dict[str, Any], project_root: Path | None = None) -> CookingKnowledgeEntry:
    path = knowledge_base_path(project_root)
    with file_lock(path):
        entries = _load_knowledge_entries_from_path(path) if path.exists() else []
        for entry in entries:
            if entry.id != entry_id:
                continue
            data = asdict(entry)
            data.update(updates)
            updated = _entry_from_dict(data)
            updated.id = entry.id
            updated.created_at = entry.created_at
            updated.updated_at = _now()
            entries = [updated if item.id == entry_id else item for item in entries]
            _write_knowledge_entries(path, entries)
            return updated
    raise KeyError(f"Knowledge entry not found: {entry_id}")


def delete_knowledge_entry(entry_id: str, project_root: Path | None = None) -> Path:
    path = knowledge_base_path(project_root)
    with file_lock(path):
        entries = _load_knowledge_entries_from_path(path) if path.exists() else []
        if not any(entry.id == entry_id for entry in entries):
            raise KeyError(f"Knowledge entry not found: {entry_id}")
        return _write_knowledge_entries(path, [entry for entry in entries if entry.id != entry_id])


def merge_knowledge_entries(
    primary_id: str,
    duplicate_ids: list[str],
    project_root: Path | None = None,
) -> CookingKnowledgeEntry:
    if primary_id in duplicate_ids:
        raise ValueError("The primary knowledge entry cannot be merged into itself.")
    duplicates = set(duplicate_ids)
    path = knowledge_base_path(project_root)
    with file_lock(path):
        entries = _load_knowledge_entries_from_path(path) if path.exists() else []
        by_id = {entry.id: entry for entry in entries}
        if primary_id not in by_id:
            raise KeyError(f"Knowledge entry not found: {primary_id}")
        missing = duplicates - by_id.keys()
        if missing:
            raise KeyError(f"Knowledge entries not found: {', '.join(sorted(missing))}")
        primary = by_id[primary_id]
        for duplicate_id in duplicate_ids:
            primary = _merge_entry(primary, by_id[duplicate_id])
        remaining = [entry for entry in entries if entry.id not in duplicates]
        remaining = [primary if entry.id == primary_id else entry for entry in remaining]
        _write_knowledge_entries(path, remaining)
        return primary


def record_knowledge_review(entry_id: str, mastery: str, project_root: Path | None = None) -> CookingKnowledgeEntry:
    intervals = {
        "已掌握": 14,
        "还模糊": 3,
        "需要实践": 1,
        "new": 1,
    }
    path = knowledge_base_path(project_root)
    with file_lock(path):
        entries = _load_knowledge_entries_from_path(path) if path.exists() else []
        now = datetime.now(timezone.utc)
        for entry in entries:
            if entry.id != entry_id:
                continue
            entry.mastery = mastery
            entry.review_count += 1
            entry.last_reviewed_at = now.isoformat()
            entry.next_review_at = (now + timedelta(days=intervals.get(mastery, 3))).isoformat()
            entry.updated_at = now.isoformat()
            _write_knowledge_entries(path, entries)
            return entry
    raise KeyError(f"Knowledge entry not found: {entry_id}")


def due_review_entries(project_root: Path | None = None, limit: int = 20) -> list[CookingKnowledgeEntry]:
    now = datetime.now(timezone.utc)
    entries = load_knowledge_entries(project_root)
    due_entries: list[CookingKnowledgeEntry] = []
    for entry in entries:
        if not entry.next_review_at:
            due_entries.append(entry)
            continue
        try:
            due = datetime.fromisoformat(entry.next_review_at)
        except ValueError as exc:
            raise CorruptDataError(
                f"Invalid next_review_at for knowledge entry {entry.id!r}: {entry.next_review_at!r}."
            ) from exc
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due <= now:
            due_entries.append(entry)

    return sorted(due_entries, key=lambda entry: entry.next_review_at or entry.updated_at or entry.created_at)[:limit]


def add_practice_record(
    entry_id: str,
    dish: str,
    outcome: str,
    notes: str,
    photo_path: str = "",
    project_root: Path | None = None,
) -> CookingKnowledgeEntry:
    path = knowledge_base_path(project_root)
    with file_lock(path):
        entries = _load_knowledge_entries_from_path(path) if path.exists() else []
        for entry in entries:
            if entry.id != entry_id:
                continue
            record = {
                "created_at": _now(),
                "dish": dish.strip(),
                "outcome": outcome.strip(),
                "notes": notes.strip(),
                "photo_path": photo_path.strip(),
            }
            entry.practice_records.append(record)
            entry.updated_at = record["created_at"]
            _write_knowledge_entries(path, entries)
            return entry
    raise KeyError(f"Knowledge entry not found: {entry_id}")


def search_knowledge_entries(
    query: str = "",
    category: str = "",
    project_root: Path | None = None,
) -> list[CookingKnowledgeEntry]:
    entries = load_knowledge_entries(project_root)
    cleaned_query = query.strip().lower()
    expanded_queries = [cleaned_query]
    for source, target in KNOWLEDGE_QUERY_ALIASES.items():
        if source in cleaned_query:
            expanded_queries.append(cleaned_query.replace(source, target))
    cleaned_category = category.strip()
    results = []
    for entry in entries:
        if cleaned_category and entry.category != cleaned_category:
            continue
        haystack = " ".join(
            [
                entry.title,
                entry.category,
                entry.content,
                entry.rationale,
                entry.evidence,
                " ".join(entry.applicable_to),
                " ".join(entry.tags),
                entry.source_title,
            ]
        ).lower()
        if cleaned_query and not any(item in haystack for item in expanded_queries):
            continue
        score = 0
        if cleaned_query:
            score += 5 if cleaned_query in entry.title.lower() else 0
            score += 3 if cleaned_query in " ".join(entry.tags).lower() else 0
            score += 1 if cleaned_query in haystack else 0
        results.append((score, entry.updated_at or entry.created_at or "", entry))
    return [entry for _, _, entry in sorted(results, key=lambda item: (item[0], item[1]), reverse=True)]


def _markdown_for_entries(entries: list[CookingKnowledgeEntry]) -> str:
    lines = ["# 个人厨艺知识库", ""]
    for entry in entries:
        lines.extend([f"## {entry.title}", "", f"- 分类：{entry.category}", f"- 掌握状态：{entry.mastery}"])
        if entry.tags:
            lines.append(f"- 标签：{', '.join(entry.tags)}")
        lines.extend(["", entry.content, ""])
        if entry.rationale:
            lines.extend(["### 原理", "", entry.rationale, ""])
        if entry.applicable_to:
            lines.extend(["### 适用场景", "", *[f"- {item}" for item in entry.applicable_to], ""])
        if entry.evidence:
            lines.extend(["### 视频依据", "", entry.evidence, ""])
        if entry.source_excerpt:
            lines.extend(["### 原文摘录", "", entry.source_excerpt, ""])
        if entry.source_refs:
            lines.extend(["### 来源", ""])
            for ref in entry.source_refs:
                label = ref.get("title") or ref.get("url") or ref.get("output_folder") or "来源"
                lines.append(f"- {label}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _safe_export_suffix(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", value.strip())
    return cleaned.strip("_")


def export_knowledge_base(kind: str = "markdown", category: str = "", project_root: Path | None = None) -> Path:
    entries = load_knowledge_entries(project_root)
    if category.strip():
        entries = [entry for entry in entries if entry.category == category.strip()]
    root = project_root or Path.cwd()
    out_dir = root / CONFIG_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{_safe_export_suffix(category)}" if category.strip() else ""
    if kind == "anki":
        path = out_dir / f"knowledge_base{suffix}_anki.tsv"
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file, delimiter="\t")
            for entry in entries:
                front = f"{entry.title}（{entry.category}）"
                back_parts = [entry.content]
                if entry.rationale:
                    back_parts.append(f"原理：{entry.rationale}")
                if entry.applicable_to:
                    back_parts.append(f"适用场景：{'；'.join(entry.applicable_to)}")
                if entry.evidence:
                    back_parts.append(f"依据：{entry.evidence}")
                tags = " ".join(entry.tags + [entry.category])
                writer.writerow([front, "<br>".join(back_parts), tags])
        return path
    if kind == "csv":
        path = out_dir / f"knowledge_base{suffix}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "id",
                    "title",
                    "category",
                    "content",
                    "rationale",
                    "applicable_to",
                    "tags",
                    "source_title",
                    "source_url",
                    "source_kind",
                    "source_excerpt",
                    "mastery",
                ],
            )
            writer.writeheader()
            for entry in entries:
                row = asdict(entry)
                row["applicable_to"] = "；".join(entry.applicable_to)
                row["tags"] = "；".join(entry.tags)
                writer.writerow({key: row.get(key, "") for key in writer.fieldnames})
        return path
    path = out_dir / f"knowledge_base{suffix}.md"
    path.write_text(_markdown_for_entries(entries), encoding="utf-8")
    return path


def _source_metadata(output_folder: Path) -> dict[str, str]:
    recipe_path = output_folder / "recipe.json"
    job_path = output_folder / "job.json"
    recipe: dict[str, Any] = {}
    job: dict[str, Any] = {}
    if recipe_path.exists():
        try:
            raw = _read_json(recipe_path)
            recipe = raw if isinstance(raw, dict) else {}
        except Exception:
            recipe = {}
    if job_path.exists():
        try:
            raw = _read_json(job_path)
            job = raw if isinstance(raw, dict) else {}
        except Exception:
            job = {}
    return {
        "source_title": str(recipe.get("title") or job.get("title") or output_folder.name),
        "source_url": str(recipe.get("source_url") or job.get("source_url") or ""),
        "source_output_folder": str(output_folder),
    }


def build_knowledge_extraction_prompt(output_folder: str | Path) -> str:
    folder = Path(output_folder)
    note = _read_text(folder / "note.md")
    transcript = _transcript_to_text(folder / "transcript.json")
    recipe = _recipe_to_context(folder / "recipe.json")

    return (
        "请从一个做菜视频资料中提取可沉淀到个人厨艺知识库的通用知识。"
        "只提取能迁移到其他菜的技巧、原理、判断标准、避坑经验、食材处理方法、调味逻辑。"
        "不要提取只属于这道菜的完整步骤，不要杜撰资料里没有的信息。"
        "输出必须是 JSON 数组，不要 Markdown，不要解释过程。\n\n"
        "每个数组元素必须包含字段：\n"
        "- title: 简短标题\n"
        "- category: 技巧/原理/食材处理/火候/调味/工具/避坑/其他 之一\n"
        "- content: 这条知识的可执行表述\n"
        "- rationale: 背后的原因或原理，不确定则写空字符串\n"
        "- applicable_to: 适用场景数组\n"
        "- evidence: 视频资料中的依据或近似原话\n"
        "- tags: 关键词数组\n"
        "- confidence: 0 到 1 的数字\n\n"
        "如果没有值得沉淀的通用知识，输出 []。\n\n"
        "=== note.md ===\n"
        f"{note or '无'}\n\n"
        "=== recipe.json ===\n"
        f"{recipe or '无'}\n\n"
        "=== transcript.json 文本 ===\n"
        f"{transcript or '无'}\n"
    )


def parse_knowledge_entries(raw_text: str, source: dict[str, str] | None = None) -> list[CookingKnowledgeEntry]:
    cleaned = clean_llm_markdown_output(raw_text)
    match = JSON_FENCE_RE.match(cleaned)
    if match:
        cleaned = match.group("body").strip()
    data = json.loads(cleaned)
    if isinstance(data, dict):
        data = data.get("entries", [])
    if not isinstance(data, list):
        raise ValueError("Knowledge extraction output must be a JSON array")
    source = source or {}
    entries: list[CookingKnowledgeEntry] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        merged = {**item, **{key: value for key, value in source.items() if value}}
        entry = _entry_from_dict(merged)
        if entry.title and entry.content:
            entries.append(entry)
    return entries


def extract_knowledge_from_video(
    output_folder: str | Path,
    options: KnowledgeExtractionOptions | None = None,
    project_root: Path | None = None,
) -> KnowledgeExtractionResult:
    folder = Path(output_folder)
    if not folder.exists():
        raise FileNotFoundError(f"Output folder not found: {folder}")
    if not (folder / "note.md").exists() and not (folder / "transcript.json").exists() and not (folder / "recipe.json").exists():
        raise FileNotFoundError(f"No note, transcript, or recipe found in: {folder}")

    opts = options or KnowledgeExtractionOptions()
    prompt = build_knowledge_extraction_prompt(folder)
    output = complete_markdown_prompt(
        prompt,
        provider=opts.llm_provider,
        openai_model=opts.openai_model,
        local_llm_command=opts.local_llm_command,
        codex_model=opts.codex_model,
        codex_profile=opts.codex_profile,
        cli_extra_instructions=opts.llm_cli_extra_instructions,
    )
    if not output:
        detail = get_last_llm_error()
        message = f"LLM knowledge extraction failed: {opts.llm_provider}"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message)

    entries = parse_knowledge_entries(output, source=_source_metadata(folder))
    path, added, updated = upsert_knowledge_entries(entries, project_root=project_root)
    return KnowledgeExtractionResult(
        knowledge_path=path,
        entries=entries,
        added_count=added,
        updated_count=updated,
    )


def _known_source_folders(project_root: Path | None = None) -> set[str]:
    folders: set[str] = set()
    for entry in load_knowledge_entries(project_root):
        if entry.source_output_folder:
            folders.add(entry.source_output_folder)
        for ref in entry.source_refs:
            if ref.get("output_folder"):
                folders.add(ref["output_folder"])
    return folders


def extract_knowledge_from_folders(
    output_folders: list[str | Path],
    options: KnowledgeExtractionOptions | None = None,
    project_root: Path | None = None,
    skip_existing: bool = True,
) -> KnowledgeBatchExtractionResult:
    path = knowledge_base_path(project_root)
    known = _known_source_folders(project_root) if skip_existing else set()
    items: list[KnowledgeBatchExtractionItem] = []
    total_added = 0
    total_updated = 0
    skipped = 0
    failed = 0
    for folder_like in output_folders:
        folder = Path(folder_like)
        if skip_existing and str(folder) in known:
            items.append(KnowledgeBatchExtractionItem(output_folder=folder, status="skipped"))
            skipped += 1
            continue
        try:
            result = extract_knowledge_from_video(folder, options=options, project_root=project_root)
        except Exception as exc:  # noqa: BLE001
            items.append(KnowledgeBatchExtractionItem(output_folder=folder, status="failed", error=str(exc)))
            failed += 1
            continue
        total_added += result.added_count
        total_updated += result.updated_count
        items.append(
            KnowledgeBatchExtractionItem(
                output_folder=folder,
                status="done",
                added_count=result.added_count,
                updated_count=result.updated_count,
            )
        )
        path = result.knowledge_path
    return KnowledgeBatchExtractionResult(
        knowledge_path=path,
        items=items,
        added_count=total_added,
        updated_count=total_updated,
        skipped_count=skipped,
        failed_count=failed,
    )


def related_knowledge_for_recipe(
    output_folder: str | Path,
    project_root: Path | None = None,
    limit: int = 6,
) -> list[CookingKnowledgeEntry]:
    folder = Path(output_folder)
    context = " ".join(
        [
            _read_text(folder / "note.md"),
            _recipe_to_context(folder / "recipe.json"),
            _transcript_to_text(folder / "transcript.json"),
        ]
    )
    context_tokens = _tokenize(context)
    scored: list[tuple[CookingKnowledgeEntry, float]] = []
    for entry in load_knowledge_entries(project_root):
        entry_tokens = _tokenize(_entry_text(entry))
        if not entry_tokens:
            continue
        score = len(context_tokens & entry_tokens) / len(entry_tokens)
        tag_hits = sum(1 for tag in entry.tags if tag and tag in context)
        score += min(tag_hits * 0.2, 0.6)
        if score > 0:
            scored.append((entry, score))
    return [entry for entry, _ in sorted(scored, key=lambda item: item[1], reverse=True)[:limit]]


def write_related_knowledge_to_note(
    output_folder: str | Path,
    project_root: Path | None = None,
    limit: int = 6,
) -> Path:
    folder = Path(output_folder)
    note_path = folder / "note.md"
    if not note_path.exists():
        raise FileNotFoundError(f"Missing note.md: {note_path}")
    entries = related_knowledge_for_recipe(folder, project_root=project_root, limit=limit)
    note = note_path.read_text(encoding="utf-8")
    note = re.sub(r"\n## 相关知识库条目\n.*$", "", note, flags=re.DOTALL).rstrip()
    if entries:
        lines = ["", "## 相关知识库条目", ""]
        for entry in entries:
            lines.append(f"- **{entry.title}**（{entry.category}）：{entry.content}")
        note = note + "\n" + "\n".join(lines).rstrip() + "\n"
    atomic_write_text(note_path, note)
    return note_path
