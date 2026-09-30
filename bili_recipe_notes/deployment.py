from __future__ import annotations

import hashlib
import csv
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from .data_paths import creators_dir, data_root, exports_dir, relocate_value, state_dir
from .storage import atomic_write_text, backup_path, read_json


DEPLOYMENT_FORMAT = "bili-recipe-notes-deployment"
DEPLOYMENT_VERSION = 3
DEPLOYMENT_ROOT = "bili-recipe-notes"
DATA_ROOT = "data"
MAX_DEPLOYMENT_FILES = 100_000
MAX_DEPLOYMENT_TOTAL_SIZE = 20 * 1024**3

APP_ROOT_FILES = {
    ".gitignore",
    ".gitkeep",
    "README.md",
    "TODO.md",
    "requirements.txt",
    "requirements-dev.txt",
    "start-ui-linux.sh",
    "start-ui-mac.command",
    "start-ui-windows.bat",
    "package-ui-mac.command",
    "package-ui-windows.bat",
    "bili-recipe-notes.spec",
    "bili-recipe-notes-ui.spec",
}
APP_DIRECTORIES = {
    ".github",
    "bili_recipe_notes",
    "contracts",
    "examples",
    "mobile",
    "tests",
    "web",
}
EXCLUDED_DIRECTORY_NAMES = {
    ".dart_tool",
    ".git",
    ".idea",
    ".next",
    ".pytest_cache",
    ".venv",
    ".vscode",
    "Pods",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
EXCLUDED_OUTPUT_DIRECTORIES = {"deployments", "handoffs", "exports"}
EXCLUDED_OUTPUT_SUFFIXES = {
    ".bak",
    ".lock",
    ".m4a",
    ".mp3",
    ".mp4",
    ".tmp",
    ".wav",
    ".webm",
    ".zip",
}
SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    "cookies.txt",
    "credentials.json",
    "service-account.json",
}
PATH_KEYS = {"output_folder", "source_path", "transcript_path", "recipe_path", "note_path", "job_path"}


@dataclass(frozen=True)
class DeploymentBundleResult:
    path: Path
    checksum_path: Path
    sha256: str
    app_file_count: int
    output_file_count: int
    state_file_count: int
    file_count: int
    source_size_bytes: int
    archive_size_bytes: int


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _safe_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in path.parts):
        return False
    lowered = path.name.lower()
    if lowered in SENSITIVE_FILE_NAMES or lowered.startswith(".env."):
        return False
    if lowered.endswith((".pyc", ".pyo", ".bak", ".tmp", ".lock", ".pem", ".key")):
        return False
    return True


def _application_files(project_root: Path) -> list[tuple[Path, PurePosixPath]]:
    files: list[tuple[Path, PurePosixPath]] = []
    for name in sorted(APP_ROOT_FILES):
        path = project_root / name
        if _safe_file(path):
            files.append((path, PurePosixPath(name)))
    for directory_name in sorted(APP_DIRECTORIES):
        directory = project_root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if _safe_file(path):
                files.append((path, PurePosixPath(path.relative_to(project_root).as_posix())))
    return files


def _output_files(output_root: Path) -> list[tuple[Path, PurePosixPath]]:
    files: list[tuple[Path, PurePosixPath]] = []
    if not output_root.is_dir():
        return files
    for path in sorted(output_root.rglob("*")):
        if not _safe_file(path):
            continue
        relative = path.relative_to(output_root)
        if relative.parts and relative.parts[0] in EXCLUDED_OUTPUT_DIRECTORIES:
            continue
        if path.suffix.lower() in EXCLUDED_OUTPUT_SUFFIXES:
            continue
        prefix = "creators" if relative.parts[0] == "creators" else "recipes"
        if prefix == "creators":
            relative = Path(*relative.parts[1:])
        files.append((path, PurePosixPath(prefix) / PurePosixPath(relative.as_posix())))
    return files


def _read_object(path: Path) -> dict[str, Any]:
    return read_json(path, expected_type=dict)


def _basename(value: Any) -> str:
    text = str(value or "").rstrip("/\\")
    if not text:
        return ""
    return re.split(r"[/\\]", text)[-1]


def _portable_recipe_path(value: Any, output_root: Path, project_root: Path) -> str | None:
    value = relocate_value(value, project_root)
    text = str(value or "").replace("\\", "/").rstrip("/")
    if not text:
        return None
    root_text = output_root.as_posix().rstrip("/")
    if text == root_text:
        return "data/recipes"
    if text.startswith(root_text + "/"):
        relative = text[len(root_text) + 1:]
    else:
        parts = PurePosixPath(text).parts
        markers = {"outputs", "recipes", output_root.name}
        indices = [index for index, part in enumerate(parts) if part in markers]
        relative = "/".join(parts[indices[-1] + 1:]) if indices else _basename(text)
    return (PurePosixPath("data/recipes") / relative).as_posix()


def _portable_value(value: Any, output_root: Path, project_root: Path, *, key: str = "") -> Any:
    lowered = key.casefold()
    if "cookie" in lowered:
        return None
    if isinstance(value, dict):
        return {
            str(item_key): _portable_value(item, output_root, project_root, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_portable_value(item, output_root, project_root, key=key) for item in value]
    if key in {"out", "out_dir", "source_output_dir"}:
        return "data/recipes"
    if key in PATH_KEYS:
        return _portable_recipe_path(value, output_root, project_root)
    if key == "obsidian_vault_dir":
        return "data/exports/obsidian-vault"
    return value


def _portable_batch(path: Path, output_root: Path, project_root: Path) -> bytes:
    return _json_bytes(_portable_value(_read_object(path), output_root, project_root))


def _portable_config(path: Path, output_root: Path, project_root: Path) -> bytes:
    value = _portable_value(_read_object(path), output_root, project_root)
    value["out_dir"] = "data/recipes"
    value["cookies"] = None
    value["obsidian_vault_dir"] = "data/exports/obsidian-vault"
    return _json_bytes(value)


def _portable_output_bytes(path: Path, relative: PurePosixPath, output_root: Path, project_root: Path) -> bytes:
    if path.name == "job.json":
        value = _portable_value(_read_object(path), output_root, project_root)
        folder = PurePosixPath(DATA_ROOT) / relative.parent
        for key in PATH_KEYS:
            if key not in value:
                continue
            if key == "output_folder":
                value[key] = folder.as_posix()
            else:
                filename = _basename(value.get(key))
                value[key] = (folder / filename).as_posix() if filename else None
        return _json_bytes(value)
    if relative.as_posix() == "recipes/curation-review/recipe-review.json":
        return _json_bytes(_portable_value(_read_object(path), output_root, project_root))
    if relative.as_posix() == "recipes/curation-review/recipe-review.csv":
        reader = csv.DictReader(StringIO(path.read_text(encoding="utf-8-sig")))
        rows = [_portable_value(row, output_root, project_root) for row in reader]
        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(reader.fieldnames or []))
        writer.writeheader()
        writer.writerows(rows)
        return ("\ufeff" + buffer.getvalue()).encode("utf-8")
    if relative.parts[:2] == ("recipes", ".ai-review") and path.suffix == ".json":
        return _json_bytes(_portable_value(_read_object(path), output_root, project_root))
    return path.read_bytes()


def _sqlite_snapshot(path: Path, output_root: Path, project_root: Path) -> bytes:
    # SQLite backup includes committed WAL pages without copying live sidecars.
    with tempfile.TemporaryDirectory(prefix="bili-recipe-snapshot-") as temporary:
        snapshot_path = Path(temporary) / path.name
        uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=15) as source:
            with sqlite3.connect(snapshot_path) as snapshot:
                source.backup(snapshot)
                tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if path.name == "mobile-sync.sqlite3" and "recipes" in tables:
                    for recipe_id, folder in snapshot.execute("SELECT id,output_folder FROM recipes").fetchall():
                        snapshot.execute(
                            "UPDATE recipes SET output_folder=? WHERE id=?",
                            (_portable_recipe_path(folder, output_root, project_root), recipe_id),
                        )
                if path.name == "mobile-sync.sqlite3" and "assets" in tables:
                    for digest, asset_path in snapshot.execute("SELECT sha256,path FROM assets").fetchall():
                        asset = str(relocate_value(asset_path, project_root)).replace("\\", "/")
                        media_marker = "/mobile-media/"
                        if media_marker in asset:
                            mapped = "data/state/mobile-media/" + asset.split(media_marker, 1)[1]
                        else:
                            mapped = _portable_recipe_path(asset, output_root, project_root)
                        snapshot.execute("UPDATE assets SET path=? WHERE sha256=?", (mapped, digest))
                snapshot.commit()
                snapshot.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return snapshot_path.read_bytes()


def _state_files(project_root: Path, output_root: Path) -> list[tuple[str, bytes]]:
    config_root = state_dir(project_root)
    if not config_root.is_dir():
        config_root = project_root / ".bili-recipe-notes"
    files: list[tuple[str, bytes]] = []
    for path in sorted(config_root.rglob("*")):
        relative = path.relative_to(config_root)
        if "cookies" in relative.parts or path.name.endswith(("-wal", "-shm")):
            continue
        # Keep recoverable JSON backups; transient locks and exports are omitted.
        is_backup = (
            path.name.endswith(".json.bak") and path.is_file() and not path.is_symlink()
            and path.name[:-4].lower() not in SENSITIVE_FILE_NAMES
            and not any(part in EXCLUDED_DIRECTORY_NAMES for part in path.parts)
        )
        if not _safe_file(path) and not is_backup:
            continue
        name = (PurePosixPath("state") / relative.as_posix()).as_posix()
        if path.name in {"config.json", "config.json.bak"}:
            data = _portable_config(path, output_root, project_root)
        elif relative.parts[0] == "batches" and path.name.endswith((".json", ".json.bak")):
            data = _portable_batch(path, output_root, project_root)
        elif path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
            data = _sqlite_snapshot(path, output_root, project_root)
        else:
            data = path.read_bytes()
        files.append((name, data))
    return files


def _support_files(project_root: Path) -> list[tuple[Path, PurePosixPath]]:
    files: list[tuple[Path, PurePosixPath]] = []
    directories = (
        (creators_dir(project_root), PurePosixPath("creators")),
        (data_root(project_root) / "sources", PurePosixPath("sources")),
        (exports_dir(project_root) / "obsidian-vault", PurePosixPath("exports/obsidian-vault")),
    )
    for root, prefix in directories:
        files.extend(
            (path, prefix / path.relative_to(root).as_posix())
            for path in sorted(root.rglob("*"))
            if _safe_file(path) and path.suffix.lower() not in EXCLUDED_OUTPUT_SUFFIXES
        )
    return files


def _deployment_guide() -> bytes:
    return """# Bili Recipe Notes 数据迁移包

本包包含菜谱数据、字幕、图片、UP 主链接清单、知识库、批次和整理记录，以及移动同步 SQLite 的一致快照、实践照片、JSON 备份、sources 原资料和默认 Obsidian 归档。
移动数据库含设备凭证、套餐、本餐、实践记录和人工上下架决定，请将本包作为私有备份保存。
应用源码、运行环境、Git 历史、Cookie、缓存和原始音视频不会写入本包。

## 恢复数据

在目标电脑先从 Git 获取同版本应用，再将本包解压到临时目录；包内统一的 `data/` 目录包含全部可迁移数据。将整个 `data/` 放到 Git 项目根目录即可；也可将其放到独立磁盘并设置 `BILI_RECIPE_DATA_DIR` 指向该目录。
新布局包含 `recipes/`、`creators/`、`state/`；启动应用后会更新移动菜谱索引。

如果目标项目已有同名数据，解压前请先备份；批次、任务、整理报告和移动数据库中的运行路径会在导出副本中转换为可移植路径，原数据不受影响。
""".encode("utf-8")


def _archive_info(name: str, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16
    return info


def _write_member(
    archive: zipfile.ZipFile,
    relative: PurePosixPath,
    data: bytes,
    records: list[dict[str, Any]],
    *,
    kind: str,
    executable: bool = False,
    stored: bool = False,
) -> None:
    name = (PurePosixPath(DEPLOYMENT_ROOT) / relative).as_posix()
    info = _archive_info(name, executable=executable)
    if stored:
        info.compress_type = zipfile.ZIP_STORED
    archive.writestr(info, data)
    records.append(
        {
            "path": relative.as_posix(),
            "kind": kind,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )


def export_deployment_bundle(
    out_dir: str | Path,
    destination: str | Path | None = None,
    *,
    project_root: str | Path | None = None,
) -> DeploymentBundleResult:
    root = Path(project_root or Path.cwd()).expanduser().resolve()
    output_path = Path(out_dir).expanduser()
    output_root = (output_path if output_path.is_absolute() else root / output_path).resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(destination).expanduser() if destination else exports_dir(root) / "deployments"
    if target.suffix.lower() != ".zip":
        target = target / f"bili-recipe-notes-{stamp}.deployment.zip"
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    output_files = _output_files(output_root)
    seen = {relative: path for path, relative in output_files}
    for path, relative in _support_files(root):
        if relative in seen:
            if seen[relative].read_bytes() != path.read_bytes():
                raise ValueError(f"UP 主清单在新旧数据目录中冲突：{relative}")
            continue
        output_files.append((path, relative))
        seen[relative] = path
    state_files = _state_files(root, output_root)
    records: list[dict[str, Any]] = []
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary_path, "w", allowZip64=True) as archive:
            for path, relative in output_files:
                _write_member(
                    archive,
                    PurePosixPath(DATA_ROOT) / relative,
                    _portable_output_bytes(path, relative, output_root, root),
                    records,
                    kind="output",
                    stored=path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"},
                )
            for name, data in state_files:
                _write_member(archive, PurePosixPath(DATA_ROOT) / PurePosixPath(name), data, records, kind="state")
            _write_member(
                archive,
                PurePosixPath("DATA-MIGRATION.md"),
                _deployment_guide(),
                records,
                kind="guide",
            )
            manifest = {
                "format": DEPLOYMENT_FORMAT,
                "version": DEPLOYMENT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "root": DEPLOYMENT_ROOT,
                "app_file_count": 0,
                "data_only": True,
                "data_root": DATA_ROOT,
                "output_file_count": len(output_files),
                "state_file_count": len(state_files),
                "source_size_bytes": sum(record["size"] for record in records),
                "files": records,
            }
            archive.writestr(
                _archive_info(f"{DEPLOYMENT_ROOT}/deployment-manifest.json"),
                _json_bytes(manifest),
            )
        if target.exists():
            shutil.copy2(target, backup_path(target))
        os.replace(temporary_path, target)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    validate_deployment_bundle(target)
    archive_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    checksum_path = target.with_suffix(f"{target.suffix}.sha256")
    atomic_write_text(
        checksum_path,
        f"{archive_sha256}  {target.name}\n",
        backup=False,
    )
    return DeploymentBundleResult(
        path=target,
        checksum_path=checksum_path,
        sha256=archive_sha256,
        app_file_count=0,
        output_file_count=len(output_files),
        state_file_count=len(state_files),
        file_count=len(records),
        source_size_bytes=sum(record["size"] for record in records),
        archive_size_bytes=target.stat().st_size,
    )


def _safe_member_name(info: zipfile.ZipInfo) -> str:
    path = PurePosixPath(info.filename)
    if not info.filename or info.filename.startswith(("/", "\\")) or ".." in path.parts:
        raise ValueError(f"部署包包含不安全路径：{info.filename!r}")
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode and stat.S_ISLNK(mode):
        raise ValueError(f"部署包不允许符号链接：{info.filename!r}")
    return path.as_posix()


def validate_deployment_bundle(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_DEPLOYMENT_FILES:
            raise ValueError("部署包文件数量超过安全上限")
        names = {_safe_member_name(info): info for info in infos}
        manifest_name = f"{DEPLOYMENT_ROOT}/deployment-manifest.json"
        if manifest_name not in names:
            raise ValueError("部署包缺少 deployment-manifest.json")
        manifest = json.loads(archive.read(manifest_name))
        if manifest.get("format") != DEPLOYMENT_FORMAT or manifest.get("version") not in {2, DEPLOYMENT_VERSION}:
            raise ValueError("不支持的部署包格式或版本")
        if manifest.get("data_only") is not True:
            raise ValueError("部署包不是数据迁移包")
        if manifest.get("data_root") != DATA_ROOT:
            raise ValueError("部署包缺少统一 data 数据根目录")
        expected_names = {manifest_name}
        total_size = 0
        for record in manifest.get("files", []):
            relative = str(record.get("path") or "")
            name = (PurePosixPath(DEPLOYMENT_ROOT) / PurePosixPath(relative)).as_posix()
            if name in expected_names or name not in names:
                raise ValueError(f"部署包清单不一致：{relative}")
            expected_names.add(name)
            if record.get("kind") == "app":
                raise ValueError("数据迁移包不应包含应用文件")
            data = archive.read(name)
            total_size += len(data)
            raw_size = record.get("size")
            expected_size = int(raw_size) if raw_size is not None else -1
            if len(data) != expected_size:
                raise ValueError(f"部署包文件大小校验失败：{relative}")
            if hashlib.sha256(data).hexdigest() != str(record.get("sha256") or ""):
                raise ValueError(f"部署包文件哈希校验失败：{relative}")
        if set(names) != expected_names:
            raise ValueError("部署包包含未登记文件")
        if total_size > MAX_DEPLOYMENT_TOTAL_SIZE:
            raise ValueError("部署包解压后大小超过安全上限")
        return manifest
