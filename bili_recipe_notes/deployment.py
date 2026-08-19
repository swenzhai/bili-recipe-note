from __future__ import annotations

import hashlib
import csv
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Any

from .storage import atomic_write_text, backup_path


DEPLOYMENT_FORMAT = "bili-recipe-notes-deployment"
DEPLOYMENT_VERSION = 1
DEPLOYMENT_ROOT = "bili-recipe-notes"
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
EXCLUDED_OUTPUT_DIRECTORIES = {"deployments", "handoffs"}
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
        files.append((path, PurePosixPath("outputs") / PurePosixPath(relative.as_posix())))
    return files


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _basename(value: Any) -> str:
    text = str(value or "").rstrip("/\\")
    if not text:
        return ""
    return re.split(r"[/\\]", text)[-1]


def _portable_value(value: Any, *, key: str = "") -> Any:
    lowered = key.casefold()
    if "cookie" in lowered:
        return None
    if isinstance(value, dict):
        return {str(item_key): _portable_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_portable_value(item, key=key) for item in value]
    if key in {"out", "out_dir"}:
        return "outputs"
    if key == "obsidian_vault_dir":
        return "obsidian-vault"
    return value


def _portable_batch(path: Path) -> bytes:
    value = _portable_value(_read_object(path))
    items = value.get("items") if isinstance(value.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        folder_name = _basename(item.get("output_folder"))
        if not folder_name:
            continue
        folder = PurePosixPath("outputs") / folder_name
        for key in PATH_KEYS:
            if key not in item:
                continue
            if key == "output_folder":
                item[key] = folder.as_posix()
            else:
                filename = _basename(item.get(key))
                item[key] = (folder / filename).as_posix() if filename else None
    return _json_bytes(value)


def _portable_config(path: Path) -> bytes:
    value = _portable_value(_read_object(path))
    value["out_dir"] = "outputs"
    value["cookies"] = None
    value["obsidian_vault_dir"] = "obsidian-vault"
    return _json_bytes(value)


def _portable_output_bytes(path: Path, relative: PurePosixPath) -> bytes:
    if path.name == "job.json":
        value = _read_object(path)
        folder = relative.parent
        for key in PATH_KEYS:
            if key not in value:
                continue
            if key == "output_folder":
                value[key] = folder.as_posix()
            else:
                filename = _basename(value.get(key))
                value[key] = (folder / filename).as_posix() if filename else None
        return _json_bytes(value)
    if relative.as_posix() == "outputs/curation-review/recipe-review.json":
        value = _read_object(path)
        value["source_output_dir"] = "outputs"
        for group in value.get("groups", []):
            if not isinstance(group, dict):
                continue
            for item in group.get("items", []):
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("item_id") or _basename(item.get("output_folder"))).strip()
                if item_id:
                    item["output_folder"] = (PurePosixPath("outputs") / item_id).as_posix()
        return _json_bytes(value)
    if relative.as_posix() == "outputs/curation-review/recipe-review.csv":
        text = path.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(StringIO(text))
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
        for row in rows:
            item_id = str(row.get("item_id") or _basename(row.get("output_folder"))).strip()
            if item_id:
                row["output_folder"] = (PurePosixPath("outputs") / item_id).as_posix()
        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return ("\ufeff" + buffer.getvalue()).encode("utf-8")
    return path.read_bytes()


def _state_files(project_root: Path) -> list[tuple[str, bytes]]:
    config_root = project_root / ".bili-recipe-notes"
    files: list[tuple[str, bytes]] = []
    config_path = config_root / "config.json"
    if config_path.is_file():
        files.append((".bili-recipe-notes/config.json", _portable_config(config_path)))
    meal_plans_path = config_root / "meal-plans.json"
    if meal_plans_path.is_file():
        files.append((".bili-recipe-notes/meal-plans.json", meal_plans_path.read_bytes()))
    for path in sorted((config_root / "batches").glob("*.json")):
        if path.name.endswith(".json.bak"):
            continue
        files.append((f".bili-recipe-notes/batches/{path.name}", _portable_batch(path)))
        log_path = path.with_suffix(".log")
        if log_path.is_file():
            files.append((f".bili-recipe-notes/batches/{log_path.name}", log_path.read_bytes()))
    return files


def _deployment_guide() -> bytes:
    return """# Bili Recipe Notes 部署包

本包包含应用源码、全部菜谱/字幕/步骤图片、同名菜谱审核报告、已保存套餐以及人工决定。
不包含 Cookie、虚拟环境、Git 历史、缓存、原始音视频、备份文件或移动端数据库。

## Windows

1. 安装 64 位 Python 3.10 或更新版本。
2. 在 PowerShell 中进入本目录：`py -3 -m venv .venv`。
3. 运行 `.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt`。
4. 双击 `start-ui-windows.bat`，浏览器打开 `http://127.0.0.1:8501`。

## Linux（可信局域网）

1. 运行 `chmod +x start-ui-linux.sh`。
2. 运行 `./start-ui-linux.sh`；脚本会自动创建 `.venv`、安装依赖并显示局域网访问地址。
3. 当前网页没有登录认证，请勿把 8501 或 8765 端口暴露到公网或公共 Wi-Fi。

## macOS

1. 运行 `python3 -m venv .venv`。
2. 运行 `.venv/bin/python -m pip install -r requirements.txt`。
3. 运行 `ARROW_DEFAULT_MEMORY_POOL=system .venv/bin/python -m streamlit run bili_recipe_notes/ui.py --server.address=127.0.0.1 --server.port=8501 --server.headless=true`。

## 恢复整理工作

进入网页“最终菜谱整理”，先点击“重新扫描输出”刷新新电脑上的绝对路径。人工决定按稳定目录 ID 单独保存在 `outputs/curation-review/curation-decisions.json`，不会被重新扫描覆盖。

远程使用时不要把无认证页面直接监听到公网。请保留 `127.0.0.1`，并在自己的电脑执行 `ssh -N -L 8501:127.0.0.1:8501 用户名@服务器地址`。
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
    output_root = Path(out_dir).expanduser().resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(destination).expanduser() if destination else root / "deployments"
    if target.suffix.lower() != ".zip":
        target = target / f"bili-recipe-notes-{stamp}.deployment.zip"
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    app_files = _application_files(root)
    output_files = _output_files(output_root)
    state_files = _state_files(root)
    records: list[dict[str, Any]] = []
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary_path, "w", allowZip64=True) as archive:
            for path, relative in app_files:
                _write_member(
                    archive,
                    relative,
                    path.read_bytes(),
                    records,
                    kind="app",
                    executable=bool(path.stat().st_mode & stat.S_IXUSR),
                )
            for path, relative in output_files:
                _write_member(
                    archive,
                    relative,
                    _portable_output_bytes(path, relative),
                    records,
                    kind="output",
                    stored=path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"},
                )
            for name, data in state_files:
                _write_member(archive, PurePosixPath(name), data, records, kind="state")
            _write_member(
                archive,
                PurePosixPath("DEPLOYMENT.md"),
                _deployment_guide(),
                records,
                kind="guide",
            )
            manifest = {
                "format": DEPLOYMENT_FORMAT,
                "version": DEPLOYMENT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "root": DEPLOYMENT_ROOT,
                "app_file_count": len(app_files),
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
        app_file_count=len(app_files),
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
        if manifest.get("format") != DEPLOYMENT_FORMAT or manifest.get("version") != DEPLOYMENT_VERSION:
            raise ValueError("不支持的部署包格式或版本")
        expected_names = {manifest_name}
        total_size = 0
        for record in manifest.get("files", []):
            relative = str(record.get("path") or "")
            name = (PurePosixPath(DEPLOYMENT_ROOT) / PurePosixPath(relative)).as_posix()
            if name in expected_names or name not in names:
                raise ValueError(f"部署包清单不一致：{relative}")
            expected_names.add(name)
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
