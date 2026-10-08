"""Shared context for the authenticated management API."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from .config import load_config
from .file_cache import read_json_cached


def require_admin_request(request: Request) -> None:
    # The custom header is only a CSRF guard; it never grants admin access.
    access = getattr(request.app.state, "access_control", None)
    if access is None:
        raise HTTPException(401, "请先登录管理员")
    access.require_admin(request)
    origin = request.headers.get("origin")
    if origin:
        supplied = urlsplit(origin)
        expected = urlsplit(str(request.base_url))
        if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
            raise HTTPException(403, "管理请求必须来自当前服务器页面")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站访问管理接口")
    media = request.method == "GET" and (
        "/media/" in request.url.path or "/downloads/" in request.url.path
        or request.url.path.endswith("/export")
    )
    if not media and request.headers.get("x-admin-request") != "1":
        raise HTTPException(403, "请从管理页面访问此接口")


def inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise HTTPException(400, "路径不属于当前菜谱库")
    return resolved


def read_object(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return read_json_cached(path)


def revision(folder: Path) -> str:
    digest = hashlib.sha256((folder / "recipe.json").read_bytes())
    note = inside(folder / "note.md", folder)
    digest.update(b"\x00note\x00")
    digest.update(note.read_bytes() if note.is_file() else b"")
    return digest.hexdigest()


@dataclass
class AdminContext:
    store: Any
    indexer: Any

    @property
    def root(self) -> Path:
        return Path(self.store.out_dir).resolve()

    def config(self):
        value = load_config(self.store.project_root)
        # The service's DB/index and all task operations must use one library,
        # even if a different startup default has just been saved in settings.
        value.out_dir = str(self.root)
        return value

    def rows(self) -> list[dict]:
        return self.store.list_management_recipes()

    def row(self, recipe_id: str) -> dict:
        for row in self.rows():
            if row["id"] == recipe_id:
                return row
        raise HTTPException(404, "菜谱不存在或尚未完成索引")

    def folder(self, recipe_id: str) -> Path:
        path = self.store.management_recipe_folder(recipe_id)
        if path is None:
            raise HTTPException(404, "菜谱不存在或尚未完成索引")
        folder = inside(Path(path), self.root)
        if folder == self.root or not (folder / "recipe.json").is_file():
            raise HTTPException(404, "菜谱原文件不可用")
        inside(folder / "recipe.json", folder)
        return folder

    def check_revision(self, folder: Path, expected: str) -> None:
        if not expected or revision(folder) != expected:
            raise HTTPException(409, "菜谱已发生变化，请刷新后重新确认；本次修改未保存")

    def index_after_save(self) -> str | None:
        """A committed edit must not look failed when its derived index fails."""
        try:
            result = self.store.index_recipes()
            if result.get("errors"):
                return "内容已保存；部分菜单索引暂未更新，后台将自动重试。"
        except Exception:
            logging.getLogger(__name__).exception("Saved admin change; menu index refresh failed")
            return "内容已保存；菜单暂未更新，后台将自动重试。"
        return None
