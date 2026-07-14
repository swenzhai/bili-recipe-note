from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from contextlib import suppress
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence
from urllib.parse import unquote

from .markdown_writer import upsert_rating_block
from .recipe_extractor import Recipe, normalize_recipe_taxonomy
from .storage import atomic_write_bytes, atomic_write_json, atomic_write_text, file_lock, read_json


ArchiveConflictPolicy = Literal["update", "overwrite", "skip", "error"]

IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\r\n]+)\)")
BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]+)\b", re.IGNORECASE)
INVALID_COMPONENT_RE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]+")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class ObsidianArchiveError(RuntimeError):
    """Base error for Obsidian vault archive operations."""


class ObsidianArchiveConflict(ObsidianArchiveError):
    """Raised before an archive would overwrite a manually edited vault note."""


@dataclass(frozen=True)
class ObsidianVaultLayout:
    recipes_dir: str = "菜谱"
    tips_dir: str = "烹饪技巧"
    attachments_dir: str = "附件"


@dataclass(frozen=True)
class RecipeArchiveResult:
    output_folder: Path
    source_id: str
    action: str
    note_path: Path
    recipe_data_path: Path
    attachment_paths: tuple[Path, ...]
    state_path: Path
    source_fingerprint: str
    revision: int


@dataclass(frozen=True)
class KnowledgeArchiveResult:
    entry_id: str
    action: str
    note_path: Path


@dataclass(frozen=True)
class RecipeBatchArchiveItem:
    output_folder: Path
    status: str
    result: RecipeArchiveResult | None = None
    error: str = ""


@dataclass(frozen=True)
class RecipeBatchArchiveResult:
    items: tuple[RecipeBatchArchiveItem, ...]

    @property
    def archived_count(self) -> int:
        return sum(item.status == "archived" for item in self.items)

    @property
    def skipped_count(self) -> int:
        return sum(item.status == "skipped" for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.items)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_vault_component(value: str, *, fallback: str = "未分类", limit: int = 80) -> str:
    """Return one portable path component without allowing directory traversal."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = INVALID_COMPONENT_RE.sub("-", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .-")
    if not normalized or normalized in {".", ".."}:
        normalized = fallback
    if normalized.upper() in WINDOWS_RESERVED_NAMES:
        normalized = f"_{normalized}"
    if len(normalized) > limit:
        normalized = normalized[:limit].rstrip(" .-") or fallback
    return normalized


def _safe_source_id(value: str) -> str:
    cleaned = safe_vault_component(value, fallback="source", limit=48)
    return re.sub(r"\s+", "-", cleaned)


def _read_object(path: Path) -> dict[str, Any]:
    value = read_json(path, expected_type=dict)
    return dict(value)


def _normalized_recipe_data(data: Mapping[str, Any]) -> tuple[Recipe, dict[str, Any]]:
    recipe = Recipe.model_validate(data) if hasattr(Recipe, "model_validate") else Recipe(**dict(data))
    recipe = normalize_recipe_taxonomy(recipe)
    normalized = recipe.model_dump() if hasattr(recipe, "model_dump") else dict(recipe.__dict__)
    return recipe, normalized


def _source_id(recipe: Mapping[str, Any], output_folder: Path) -> str:
    job_path = output_folder / "job.json"
    if job_path.is_file():
        job = _read_object(job_path)
        bvid = str(job.get("bvid") or "").strip()
        if bvid:
            return _safe_source_id(bvid)
    source_url = str(recipe.get("source_url") or "").strip()
    match = BVID_RE.search(source_url)
    if match:
        return _safe_source_id(match.group(1))
    identity = source_url or "\n".join(
        [str(recipe.get("video_title") or recipe.get("title") or ""), str(recipe.get("uploader") or "")]
    )
    if not identity.strip():
        identity = output_folder.name
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _vault_paths(vault_path: str | Path, layout: ObsidianVaultLayout) -> tuple[Path, Path, Path, Path]:
    vault = Path(vault_path).expanduser().resolve()
    recipes = vault / safe_vault_component(layout.recipes_dir, fallback="菜谱")
    tips = vault / safe_vault_component(layout.tips_dir, fallback="烹饪技巧")
    attachments = vault / safe_vault_component(layout.attachments_dir, fallback="附件")
    index = vault / ".bili-recipe-notes" / "archive-index.json"
    return vault, recipes, tips, attachments, index


def _load_index(index_path: Path) -> dict[str, Any]:
    if not index_path.exists():
        return {"schema_version": 1, "items": {}}
    index = read_json(index_path, expected_type=dict)
    items = index.get("items")
    if not isinstance(items, dict):
        raise ObsidianArchiveError(f"Invalid Obsidian archive index: {index_path}")
    return {"schema_version": 1, "items": dict(items)}


def _relative_to_vault(vault: Path, path: Path) -> str:
    return path.resolve().relative_to(vault).as_posix()


def _path_from_index(vault: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = (vault / Path(value)).resolve()
    try:
        candidate.relative_to(vault)
    except ValueError:
        raise ObsidianArchiveError("Archive index contains a path outside the vault") from None
    return candidate


def _frontmatter_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _frontmatter(fields: Mapping[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value in (None, "", []):
            continue
        lines.append(f"{key}: {_frontmatter_value(value)}")
    return "\n".join([*lines, "---", ""])


def _strip_frontmatter(markdown: str) -> str:
    normalized = markdown.lstrip("\ufeff")
    if not normalized.startswith("---\n"):
        return normalized.lstrip("\n")
    marker = normalized.find("\n---\n", 4)
    if marker < 0:
        return normalized.lstrip("\n")
    return normalized[marker + len("\n---\n") :].lstrip("\n")


def _markdown_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    return unquote(target.replace("\\", "/"))


def _local_image(output_folder: Path, raw_target: str) -> Path | None:
    target = _markdown_target(raw_target)
    if not target or target.startswith(("http://", "https://", "data:", "#")) or "://" in target:
        return None
    root = output_folder.resolve()
    candidate = (root / Path(target)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ObsidianArchiveError(f"Image path escapes the recipe output folder: {raw_target}") from None
    if not candidate.is_file():
        raise FileNotFoundError(f"Referenced recipe image does not exist: {candidate}")
    return candidate


def _managed_image_name(path: Path, output_folder: Path) -> str:
    relative = path.relative_to(output_folder.resolve()).as_posix()
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:8]
    stem = safe_vault_component(path.stem, fallback="image", limit=52).replace(" ", "-")
    suffix = path.suffix.lower() if re.fullmatch(r"\.[a-zA-Z0-9]{1,8}", path.suffix) else ".img"
    return f"{stem}-{digest}{suffix}"


def _recipe_sources(
    output_folder: Path,
    markdown: str,
) -> tuple[list[tuple[str, Path]], str]:
    references: list[tuple[str, Path]] = []
    digest = hashlib.sha256()
    note_bytes = markdown.encode("utf-8")
    recipe_bytes = (output_folder / "recipe.json").read_bytes()
    digest.update(note_bytes)
    digest.update(b"\0recipe.json\0")
    digest.update(recipe_bytes)
    seen: set[Path] = set()
    for match in IMAGE_RE.finditer(markdown):
        image = _local_image(output_folder, match.group(2))
        if image is None:
            continue
        references.append((match.group(2), image))
        if image in seen:
            continue
        seen.add(image)
        digest.update(b"\0image\0")
        digest.update(image.relative_to(output_folder.resolve()).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(image.read_bytes())
    return references, digest.hexdigest()


def _relative_markdown_link(note_path: Path, target: Path) -> str:
    relative = Path(os.path.relpath(target, note_path.parent)).as_posix()
    return f"<{relative}>"


def _render_recipe_markdown(
    body: str,
    *,
    note_path: Path,
    recipe_data_path: Path,
    references: Sequence[tuple[str, Path]],
    image_targets: Mapping[Path, Path],
    fields: Mapping[str, Any],
) -> str:
    replacements = {
        raw: _relative_markdown_link(note_path, image_targets[path]) for raw, path in references
    }

    def replace_image(match: re.Match[str]) -> str:
        replacement = replacements.get(match.group(2))
        if replacement is None:
            return match.group(0)
        return f"![{match.group(1)}]({replacement})"

    archive_fields = dict(fields)
    archive_fields["recipe_data"] = Path(os.path.relpath(recipe_data_path, note_path.parent)).as_posix()
    cleaned_body = _strip_frontmatter(body).rstrip() + "\n"
    return _frontmatter(archive_fields) + "\n" + IMAGE_RE.sub(replace_image, cleaned_body)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_archive_state(
    output_folder: Path,
    *,
    status: str,
    action: str = "",
    vault: Path | None = None,
    note_path: Path | None = None,
    recipe_data_path: Path | None = None,
    source_id: str = "",
    source_fingerprint: str = "",
    source_note_fingerprint: str = "",
    vault_note_fingerprint: str = "",
    category: str = "",
    ratings: Mapping[str, Any] | None = None,
    attachment_paths: Sequence[Path] = (),
    error: str = "",
) -> tuple[Path, int]:
    state_path = output_folder / "archive.json"
    previous_revision = 0
    if state_path.is_file():
        with suppress(Exception):
            previous = read_json(state_path, expected_type=dict)
            previous_revision = max(0, int(previous.get("revision") or 0))
    revision = previous_revision
    if status == "archived" and action in {"created", "updated"}:
        revision += 1
    elif status == "archived" and revision == 0:
        revision = 1
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "action": action,
        "vault": str(vault) if vault else "",
        "vault_root": str(vault) if vault else "",
        "note_path": str(note_path) if note_path else "",
        "recipe_data_path": str(recipe_data_path) if recipe_data_path else "",
        "attachment_paths": [str(path) for path in attachment_paths],
        "asset_paths": [str(path) for path in attachment_paths],
        "source_id": source_id,
        "source_fingerprint": source_fingerprint,
        # ``note_fingerprint`` remains a concise compatibility name for history/UI.
        "note_fingerprint": source_note_fingerprint,
        "note_sha256": source_note_fingerprint,
        "source_note_fingerprint": source_note_fingerprint,
        "vault_note_fingerprint": vault_note_fingerprint,
        "revision": revision,
        "category": category,
        "taste_rating": (ratings or {}).get("taste_rating"),
        "difficulty_rating": (ratings or {}).get("difficulty_rating"),
        "time_rating": (ratings or {}).get("time_rating"),
        "archived_at": _now(),
        "error": error,
    }
    atomic_write_json(state_path, payload)
    return state_path, revision


def archive_recipe_to_obsidian(
    output_folder: str | Path,
    vault_path: str | Path,
    *,
    category: str | None = None,
    tags: Iterable[str] = (),
    conflict: ArchiveConflictPolicy = "update",
    layout: ObsidianVaultLayout | None = None,
) -> RecipeArchiveResult:
    """Archive one finalized recipe output into an Obsidian vault.

    ``update`` is safe by default: it updates the same source only when the
    archived note has not been edited by hand. Use ``overwrite`` to explicitly
    replace a manually edited destination.
    """

    if conflict not in {"update", "overwrite", "skip", "error"}:
        raise ValueError(f"Unsupported archive conflict policy: {conflict}")
    folder = Path(output_folder).resolve()
    note_source = folder / "note.md"
    recipe_source = folder / "recipe.json"
    if not note_source.is_file():
        raise FileNotFoundError(f"Missing finalized recipe note: {note_source}")
    if not recipe_source.is_file():
        raise FileNotFoundError(f"Missing finalized recipe data: {recipe_source}")

    chosen_layout = layout or ObsidianVaultLayout()
    vault, recipes_root, _tips_root, attachments_root, index_path = _vault_paths(vault_path, chosen_layout)
    vault.mkdir(parents=True, exist_ok=True)
    recipe_model, recipe = _normalized_recipe_data(_read_object(recipe_source))
    markdown = upsert_rating_block(note_source.read_text(encoding="utf-8"), recipe_model)
    source_id = _source_id(recipe, folder)
    archive_id = f"recipe:{source_id}"
    title = str(recipe.get("title") or recipe.get("video_title") or folder.name).strip() or folder.name
    chosen_category = safe_vault_component(category or str(recipe.get("category") or "未分类"))
    safe_title = safe_vault_component(title, fallback="菜谱")
    note_path = recipes_root / chosen_category / f"{safe_title}--{source_id}.md"
    attachment_dir = attachments_root / "菜谱" / source_id
    recipe_data_path = attachment_dir / "recipe.json"
    references, source_fingerprint = _recipe_sources(folder, markdown)
    unique_images = list(dict.fromkeys(path for _raw, path in references))
    image_targets = {path: attachment_dir / _managed_image_name(path, folder) for path in unique_images}
    recipe_tags = recipe.get("tags") if isinstance(recipe.get("tags"), list) else []
    cuisine = str(recipe.get("cuisine") or "").strip()
    archive_tags = list(
        dict.fromkeys(
            item
            for item in [
                "菜谱",
                chosen_category,
                cuisine,
                *(str(tag).strip() for tag in recipe_tags),
                *(str(tag).strip() for tag in tags),
            ]
            if item
        )
    )
    archived_at = _now()
    fields = {
        "title": title,
        "type": "recipe",
        "status": "archived",
        "category": chosen_category,
        "cuisine": cuisine,
        "tags": archive_tags,
        "source": str(recipe.get("source_url") or ""),
        "source_id": source_id,
        "recipe_id": source_id,
        "archive_id": archive_id,
        "uploader": str(recipe.get("uploader") or ""),
        "rating": recipe.get("taste_rating"),
        "taste_rating": recipe.get("taste_rating"),
        "difficulty_rating": recipe.get("difficulty_rating"),
        "time_rating": recipe.get("time_rating"),
        "rating_scale": 5,
        "archived_at": archived_at,
        "bili_recipe_notes_fingerprint": source_fingerprint,
    }
    rendered = _render_recipe_markdown(
        markdown,
        note_path=note_path,
        recipe_data_path=recipe_data_path,
        references=references,
        image_targets=image_targets,
        fields=fields,
    )
    rendered_fingerprint = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    action = "created"

    try:
        with file_lock(index_path):
            index = _load_index(index_path)
            items: dict[str, Any] = index["items"]
            old = items.get(archive_id) if isinstance(items.get(archive_id), dict) else None
            old_note = _path_from_index(vault, old.get("note_path")) if old else None
            existing = old_note if old_note and old_note.is_file() else None

            if existing:
                if conflict == "skip":
                    action = "skipped"
                elif conflict == "error":
                    raise FileExistsError(f"Recipe source is already archived: {existing}")
                elif (
                    str(old.get("source_fingerprint") or "") == source_fingerprint
                    and str(old.get("category") or "") == chosen_category
                    and old.get("tags") == archive_tags
                ):
                    action = "unchanged"
                elif conflict == "update":
                    expected = str(old.get("vault_note_fingerprint") or "")
                    if not expected or _file_sha256(existing) != expected:
                        raise ObsidianArchiveConflict(
                            f"Archived note was edited manually; use conflict='overwrite' to replace it: {existing}"
                        )
                    action = "updated"
                else:
                    action = "updated"
            elif note_path.exists():
                if conflict == "skip":
                    action = "skipped"
                elif conflict != "overwrite":
                    raise FileExistsError(f"Archive target already exists but is not indexed: {note_path}")

            if action not in {"skipped", "unchanged"}:
                for source, target in image_targets.items():
                    atomic_write_bytes(target, source.read_bytes(), backup=False)
                atomic_write_bytes(recipe_data_path, recipe_source.read_bytes(), backup=False)
                atomic_write_text(note_path, rendered, backup=False)

                previous_files = old.get("managed_files", []) if old else []
                current_files = {
                    _relative_to_vault(vault, target) for target in [recipe_data_path, *image_targets.values()]
                }
                for relative in previous_files if isinstance(previous_files, list) else []:
                    stale = _path_from_index(vault, relative)
                    if stale and relative not in current_files and stale.is_file():
                        stale.unlink()
                if old_note and old_note != note_path and old_note.is_file():
                    old_note.unlink()

                items[archive_id] = {
                    "kind": "recipe",
                    "source_id": source_id,
                    "note_path": _relative_to_vault(vault, note_path),
                    "source_fingerprint": source_fingerprint,
                    "vault_note_fingerprint": rendered_fingerprint,
                    "recipe_data_path": _relative_to_vault(vault, recipe_data_path),
                    "managed_files": sorted(current_files),
                    "category": chosen_category,
                    "tags": archive_tags,
                    "updated_at": archived_at,
                }
                atomic_write_json(index_path, index)
            else:
                note_path = existing or note_path
                indexed_recipe_path = _path_from_index(vault, old.get("recipe_data_path")) if old else None
                if indexed_recipe_path:
                    recipe_data_path = indexed_recipe_path

        attachment_paths = tuple(image_targets.values())
        source_note_fingerprint = _file_sha256(note_source)
        vault_note_fingerprint = _file_sha256(note_path) if note_path.is_file() else ""
        state_path, revision = _record_archive_state(
            folder,
            status="stale" if action == "skipped" else "archived",
            action=action,
            vault=vault,
            note_path=note_path,
            recipe_data_path=recipe_data_path,
            source_id=source_id,
            source_fingerprint=source_fingerprint,
            source_note_fingerprint=source_note_fingerprint,
            vault_note_fingerprint=vault_note_fingerprint,
            category=chosen_category,
            ratings=recipe,
            attachment_paths=attachment_paths,
        )
        return RecipeArchiveResult(
            output_folder=folder,
            source_id=source_id,
            action=action,
            note_path=note_path,
            recipe_data_path=recipe_data_path,
            attachment_paths=attachment_paths,
            state_path=state_path,
            source_fingerprint=source_fingerprint,
            revision=revision,
        )
    except Exception as exc:
        with suppress(Exception):
            _record_archive_state(
                folder,
                status="failed",
                vault=vault,
                note_path=note_path,
                recipe_data_path=recipe_data_path,
                source_id=source_id,
                source_fingerprint=source_fingerprint,
                source_note_fingerprint=_file_sha256(note_source),
                category=chosen_category,
                ratings=recipe,
                error=str(exc),
            )
        raise


def archive_recipes_to_obsidian(
    output_folders: Iterable[str | Path],
    vault_path: str | Path,
    *,
    category: str | None = None,
    tags: Iterable[str] = (),
    conflict: ArchiveConflictPolicy = "update",
    layout: ObsidianVaultLayout | None = None,
) -> RecipeBatchArchiveResult:
    """Archive a batch without one damaged output blocking the remaining recipes."""

    items: list[RecipeBatchArchiveItem] = []
    for value in output_folders:
        folder = Path(value).resolve()
        try:
            result = archive_recipe_to_obsidian(
                folder,
                vault_path,
                category=category,
                tags=tags,
                conflict=conflict,
                layout=layout,
            )
        except Exception as exc:  # noqa: BLE001 - failures are reported per batch item
            items.append(RecipeBatchArchiveItem(folder, "failed", error=str(exc)))
            continue
        status = "skipped" if result.action == "skipped" else "archived"
        items.append(RecipeBatchArchiveItem(folder, status, result=result))
    return RecipeBatchArchiveResult(tuple(items))


def load_archive_manifest(output_folder: str | Path) -> dict[str, Any] | None:
    """Load ``archive.json`` for history/UI consumers, or return ``None`` for a draft."""

    path = Path(output_folder).resolve() / "archive.json"
    if not path.is_file():
        return None
    return _read_object(path)


def recipe_archive_status(output_folder: str | Path) -> str:
    """Return ``draft``, ``archived``, ``stale`` or ``archive_error``.

    Staleness is derived from the current finalized ``note.md`` rather than
    relying on every editor to remember to update the archive manifest.
    """

    folder = Path(output_folder).resolve()
    try:
        manifest = load_archive_manifest(folder)
    except Exception:
        return "archive_error"
    if manifest is None:
        return "draft"
    if manifest.get("status") == "stale":
        return "stale"
    if manifest.get("status") != "archived":
        return "archive_error"
    note_path = Path(str(manifest.get("note_path") or ""))
    if not note_path.is_file():
        return "archive_error"
    source_note = folder / "note.md"
    expected = str(
        manifest.get("source_note_fingerprint")
        or manifest.get("note_sha256")
        or manifest.get("note_fingerprint")
        or ""
    )
    if not source_note.is_file() or not expected:
        return "archive_error"
    current = hashlib.sha256(source_note.read_bytes()).hexdigest()
    return "archived" if current == expected else "stale"


def _knowledge_dict(entry: Any) -> dict[str, Any]:
    if isinstance(entry, Mapping):
        return dict(entry)
    if is_dataclass(entry):
        return asdict(entry)
    raise TypeError("Knowledge entries must be mappings or dataclass instances")


def _knowledge_body(entry: Mapping[str, Any]) -> str:
    title = str(entry.get("title") or "烹饪技巧").strip()
    lines = [f"# {title}", "", str(entry.get("content") or "").strip()]
    rationale = str(entry.get("rationale") or "").strip()
    if rationale:
        lines.extend(["", "## 原理", "", rationale])
    applicable = entry.get("applicable_to")
    if isinstance(applicable, list) and applicable:
        lines.extend(["", "## 适用场景", "", *(f"- {str(item).strip()}" for item in applicable if str(item).strip())])
    source_url = str(entry.get("source_url") or "").strip()
    source_title = str(entry.get("source_title") or source_url).strip()
    if source_url:
        lines.extend(["", "## 来源", "", f"- [{source_title}]({source_url})"])
    return "\n".join(lines).rstrip() + "\n"


def archive_knowledge_to_obsidian(
    entries: Iterable[Any],
    vault_path: str | Path,
    *,
    conflict: ArchiveConflictPolicy = "update",
    layout: ObsidianVaultLayout | None = None,
    approved_only: bool = True,
) -> tuple[KnowledgeArchiveResult, ...]:
    """Archive confirmed cooking knowledge as concise, independently editable notes.

    Legacy entries without a review status remain supported. When a caller
    supplies ``review_status``/``curation_status``, draft and rejected entries
    are ignored by default so AI candidates cannot accidentally enter the vault.
    """

    if conflict not in {"update", "overwrite", "skip", "error"}:
        raise ValueError(f"Unsupported archive conflict policy: {conflict}")
    chosen_layout = layout or ObsidianVaultLayout()
    vault, _recipes_root, tips_root, _attachments_root, index_path = _vault_paths(vault_path, chosen_layout)
    vault.mkdir(parents=True, exist_ok=True)
    results: list[KnowledgeArchiveResult] = []
    with file_lock(index_path):
        index = _load_index(index_path)
        items: dict[str, Any] = index["items"]
        for raw_entry in entries:
            entry = _knowledge_dict(raw_entry)
            review_status = str(entry.get("review_status") or entry.get("curation_status") or "").strip().lower()
            if approved_only and review_status and review_status not in {"approved", "accepted", "已确认", "已采用"}:
                continue
            title = str(entry.get("title") or "").strip()
            content = str(entry.get("content") or "").strip()
            if not title or not content:
                raise ValueError("Knowledge entries require non-empty title and content")
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id:
                identity = "\n".join([title, content, str(entry.get("source_url") or "")])
                entry_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            entry_id = _safe_source_id(entry_id)
            archive_id = f"knowledge:{entry_id}"
            category = safe_vault_component(str(entry.get("category") or "其他"))
            note_path = tips_root / category / f"{safe_vault_component(title, fallback='技巧')}--{entry_id}.md"
            entry_tags = entry.get("tags") if isinstance(entry.get("tags"), list) else []
            tags = list(dict.fromkeys(["烹饪技巧", category, *(str(tag).strip() for tag in entry_tags if str(tag).strip())]))
            body = _knowledge_body(entry)
            source_fingerprint = hashlib.sha256(
                json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            rendered = _frontmatter(
                {
                    "title": title,
                    "type": "cooking-tip",
                    "status": "approved",
                    "category": category,
                    "tags": tags,
                    "source": str(entry.get("source_url") or ""),
                    "source_id": entry_id,
                    "archive_id": archive_id,
                    "archived_at": _now(),
                    "bili_recipe_notes_fingerprint": source_fingerprint,
                }
            ) + "\n" + body
            rendered_fingerprint = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            old = items.get(archive_id) if isinstance(items.get(archive_id), dict) else None
            old_note = _path_from_index(vault, old.get("note_path")) if old else None
            existing = old_note if old_note and old_note.is_file() else None
            action = "created"
            if existing:
                if conflict == "skip":
                    action = "skipped"
                elif conflict == "error":
                    raise FileExistsError(f"Knowledge entry is already archived: {existing}")
                elif str(old.get("source_fingerprint") or "") == source_fingerprint:
                    action = "unchanged"
                elif conflict == "update":
                    expected = str(old.get("vault_note_fingerprint") or "")
                    if not expected or _file_sha256(existing) != expected:
                        raise ObsidianArchiveConflict(
                            f"Archived knowledge note was edited manually; use conflict='overwrite': {existing}"
                        )
                    action = "updated"
                else:
                    action = "updated"
            elif note_path.exists() and conflict != "overwrite":
                if conflict == "skip":
                    action = "skipped"
                else:
                    raise FileExistsError(f"Knowledge archive target already exists: {note_path}")

            if action not in {"skipped", "unchanged"}:
                atomic_write_text(note_path, rendered, backup=False)
                if old_note and old_note != note_path and old_note.is_file():
                    old_note.unlink()
                items[archive_id] = {
                    "kind": "knowledge",
                    "source_id": entry_id,
                    "note_path": _relative_to_vault(vault, note_path),
                    "source_fingerprint": source_fingerprint,
                    "vault_note_fingerprint": rendered_fingerprint,
                    "category": category,
                    "updated_at": _now(),
                }
            else:
                note_path = existing or note_path
            results.append(KnowledgeArchiveResult(entry_id, action, note_path))
        atomic_write_json(index_path, index)
    return tuple(results)


# Short aliases make the service convenient to call from UI code.
archive_recipe = archive_recipe_to_obsidian
archive_recipe_batch = archive_recipes_to_obsidian
archive_knowledge = archive_knowledge_to_obsidian
