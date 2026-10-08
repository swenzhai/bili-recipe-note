"""Persistent administrator and ordering access, independent of device pairing.

The bootstrap secret is available only from the server's filesystem. A public
visitor can never claim an unconfigured server merely by choosing a password.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import math
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from .data_paths import state_dir
from .storage import atomic_write_json, atomic_write_text, file_lock, read_json

ADMIN_COOKIE = "chef_admin_session"
ORDERING_COOKIE = "chef_ordering_session"
SESSION_SECONDS = {"admin": 12 * 60 * 60, "ordering": 7 * 24 * 60 * 60}
PASSWORD_ITERATIONS = 600_000
MAX_BODY_BYTES = 8192
LOGIN_WINDOW_SECONDS = 60
LOGIN_ATTEMPTS = 8
MAX_SESSIONS_PER_ROLE = 256


def _password_hash(password: str) -> dict[str, Any]:
    salt = secrets.token_hex(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS)
    return {"algorithm": "pbkdf2_sha256", "iterations": PASSWORD_ITERATIONS, "salt": salt, "digest": digest.hex()}


def _password_matches(password: str, encoded: dict[str, Any] | None) -> bool:
    # The dummy calculation keeps a missing credential on the same slow path.
    candidate = encoded or {"algorithm": "pbkdf2_sha256", "iterations": PASSWORD_ITERATIONS,
                            "salt": "00" * 24, "digest": "00" * 32}
    try:
        if candidate.get("algorithm") != "pbkdf2_sha256" or candidate.get("iterations") != PASSWORD_ITERATIONS:
            return False
        salt = bytes.fromhex(candidate["salt"])
        expected = bytes.fromhex(candidate["digest"])
        if len(salt) != 24 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
        return bool(encoded) and hmac.compare_digest(expected, actual)
    except (TypeError, KeyError, ValueError):
        return False


def _password(value: Any, *, new: bool = False) -> str:
    minimum = 8 if new else 1
    try:
        valid = isinstance(value, str) and minimum <= len(value) <= 128 and len(value.encode()) <= 512 and bool(value.strip())
    except UnicodeError:
        valid = False
    if not valid:
        raise HTTPException(422, f"密码须为 {minimum}–128 个字符")
    return value


def require_access_request(request: Request) -> None:
    """Reject cross-origin mutations even if a browser has valid cookies."""
    if request.headers.get("x-access-request") != "1":
        raise HTTPException(403, "请从当前服务器页面提交访问设置")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "不允许跨站访问")
    expected = urlsplit(str(request.base_url))
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        try:
            supplied = urlsplit(value)
            equal = (supplied.scheme, supplied.hostname, supplied.port or (443 if supplied.scheme == "https" else 80)) == (
                expected.scheme, expected.hostname, expected.port or (443 if expected.scheme == "https" else 80))
            equal = equal and supplied.username is None and supplied.password is None
        except ValueError:
            equal = False
        if not equal:
            raise HTTPException(403, "请求必须来自当前服务器页面")


async def _payload(request: Request, allowed: set[str]) -> dict[str, Any]:
    require_access_request(request)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "请提交 JSON 请求")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "请求过大")
    try:
        value = json.loads(body)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise HTTPException(400, "无效 JSON 请求") from exc
    if not isinstance(value, dict) or set(value) - allowed:
        # Do not echo request contents: they can contain passwords and tokens.
        raise HTTPException(422, "请求字段无效")
    return value


class AccessControl:
    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.path = state_dir(self.project_root) / "access-control.json"
        self.setup_token_path = self.path.parent / "admin-setup-token"
        with file_lock(self.path):
            if self.path.exists():
                state = self._load()
                self.path.chmod(0o600)
            else:
                state = {"schema_version": 1, "admin_password": None, "ordering_password": None,
                         "ordering_enabled": True, "ordering_password_required": True,
                         "sessions": {}, "attempts": {}}
                self._save(state)
            if state["admin_password"] is None:
                if not self.setup_token_path.exists():
                    atomic_write_text(self.setup_token_path, secrets.token_urlsafe(32) + "\n", backup=False)
                self.setup_token_path.chmod(0o600)
            else:
                self.setup_token_path.unlink(missing_ok=True)

    def _load(self) -> dict[str, Any]:
        state = read_json(self.path, expected_type=dict)
        valid = (state.get("schema_version") == 1
                 and type(state.get("ordering_enabled")) is bool
                 and type(state.get("ordering_password_required")) is bool
                 and isinstance(state.get("sessions"), dict) and isinstance(state.get("attempts"), dict)
                 and all(key in state and (state[key] is None or isinstance(state[key], dict))
                         for key in ("admin_password", "ordering_password")))
        if not valid:
            raise RuntimeError("访问配置无效；拒绝访问，请在服务器上检查 access-control.json")
        return state

    def _save(self, state: dict[str, Any]) -> None:
        # A previous-state backup could restore revoked sessions after recovery.
        atomic_write_json(self.path, state, backup=False)
        self.path.chmod(0o600)

    @staticmethod
    def _token(request: Request, role: str) -> str | None:
        token = request.cookies.get(ADMIN_COOKIE if role == "admin" else ORDERING_COOKIE)
        if not token or len(token) != 43 or not token.isascii():
            return None
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _unexpired(session: Any, role: str | None = None) -> bool:
        if not isinstance(session, dict) or session.get("role") not in SESSION_SECONDS:
            return False
        expiry = session.get("expires_at")
        return ((role is None or session["role"] == role) and type(expiry) in (int, float)
                and math.isfinite(expiry) and expiry > time.time())

    def _authenticated(self, state: dict[str, Any], request: Request, role: str) -> bool:
        digest = self._token(request, role)
        session = state["sessions"].get(digest) if digest else None
        return self._unexpired(session, role)

    def _status(self, state: dict[str, Any], request: Request) -> dict[str, bool]:
        admin = bool(state["admin_password"]) and self._authenticated(state, request, "admin")
        return {
            "admin_configured": bool(state["admin_password"]), "admin_authenticated": admin,
            "ordering_enabled": state["ordering_enabled"],
            "ordering_password_required": state["ordering_password_required"],
            "ordering_password_configured": bool(state["ordering_password"]),
            "ordering_authenticated": bool(state["admin_password"]) and state["ordering_enabled"] and (
                admin or not state["ordering_password_required"] or (
                    bool(state["ordering_password"]) and self._authenticated(state, request, "ordering"))),
        }

    def status(self, request: Request) -> dict[str, bool]:
        return self._status(self._load(), request)

    def require_admin(self, request: Request) -> None:
        if not self.status(request)["admin_authenticated"]:
            raise HTTPException(401, "请先登录管理员")

    def require_ordering(self, request: Request) -> None:
        status = self.status(request)
        if not status["admin_configured"]:
            raise HTTPException(401, "管理员尚未完成安全设置")
        if not status["ordering_enabled"]:
            raise HTTPException(403, "管理员已关闭点餐页面")
        if not status["ordering_authenticated"]:
            raise HTTPException(401, "请先输入点餐密码")

    def ordering_access_allowed(self, request: Request) -> bool:
        return self.status(request)["ordering_authenticated"]

    @staticmethod
    def _prune(state: dict[str, Any]) -> None:
        now = time.time()
        state["sessions"] = {digest: session for digest, session in state["sessions"].items()
                             if AccessControl._unexpired(session)}
        state["attempts"] = {key: times for key, times in state["attempts"].items()
                             if isinstance(times, list) and times and times[-1] > now - LOGIN_WINDOW_SECONDS}

    @staticmethod
    def _peer(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def _attempt(self, state: dict[str, Any], peer: str, role: str) -> None:
        self._prune(state)
        key = hashlib.sha256(f"{role}:{peer}".encode()).hexdigest()
        now = time.time()
        attempts = [stamp for stamp in state["attempts"].get(key, []) if stamp > now - LOGIN_WINDOW_SECONDS]
        if len(attempts) >= LOGIN_ATTEMPTS:
            retry = max(1, math.ceil(attempts[0] + LOGIN_WINDOW_SECONDS - now))
            raise HTTPException(429, "尝试次数过多，请稍后重试", headers={"Retry-After": str(retry)})
        # Limit stale or spoofed-address growth even outside the usual LAN deployment.
        if len(state["attempts"]) >= 1024 and key not in state["attempts"]:
            raise HTTPException(429, "尝试次数过多，请稍后重试", headers={"Retry-After": "60"})
        state["attempts"][key] = [*attempts, now]
        self._save(state)

    def _new_session(self, state: dict[str, Any], role: str) -> str:
        self._prune(state)
        same_role = {digest: session for digest, session in state["sessions"].items() if session["role"] == role}
        while len(same_role) >= MAX_SESSIONS_PER_ROLE:
            oldest = min(same_role, key=lambda key: same_role[key]["expires_at"])
            del state["sessions"][oldest]
            del same_role[oldest]
        token = secrets.token_urlsafe(32)
        state["sessions"][hashlib.sha256(token.encode()).hexdigest()] = {
            "role": role, "expires_at": time.time() + SESSION_SECONDS[role],
        }
        return token

    @staticmethod
    def _cookie(response: Response, request: Request, role: str, token: str | None) -> None:
        name = ADMIN_COOKIE if role == "admin" else ORDERING_COOKIE
        options = {"path": "/", "secure": request.url.scheme == "https", "httponly": True, "samesite": "strict"}
        if token:
            response.set_cookie(name, token, max_age=SESSION_SECONDS[role], **options)
        else:
            response.delete_cookie(name, **options)
        response.headers["Cache-Control"] = "no-store"

    def setup(self, request: Request, response: Response, payload: dict[str, Any]) -> dict[str, bool]:
        password = _password(payload.get("password"), new=True)
        supplied = payload.get("setup_token")
        if not isinstance(supplied, str) or not 1 <= len(supplied) <= 128 or not supplied.isascii():
            raise HTTPException(422, "请输入服务器上的初始化口令")
        with file_lock(self.path):
            state = self._load()
            if state["admin_password"]:
                raise HTTPException(409, "管理员已设置，请登录")
            self._attempt(state, self._peer(request), "setup")
            expected = self.setup_token_path.read_text(encoding="utf-8").strip()
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                raise HTTPException(401, "初始化口令不正确")
            state["admin_password"] = _password_hash(password)
            token = self._new_session(state, "admin")
            self._save(state)
            self.setup_token_path.unlink(missing_ok=True)
        self._cookie(response, request, "admin", token)
        result = self._status(state, request)
        result["admin_authenticated"] = True
        result["ordering_authenticated"] = state["ordering_enabled"]
        return result

    def authenticate_password(self, role: str, password: str, client_key: str) -> str:
        """Authenticate trusted local UI callers with the same throttle and sessions."""
        if role not in SESSION_SECONDS:
            raise ValueError("Unknown access role")
        password = _password(password)
        with file_lock(self.path):
            state = self._load()
            self._attempt(state, client_key, role)
            if not state["admin_password"]:
                raise HTTPException(401, "管理员尚未完成安全设置")
            if role == "ordering" and not state["ordering_enabled"]:
                raise HTTPException(403, "管理员已关闭点餐页面")
            if not _password_matches(password, state[f"{role}_password"]):
                raise HTTPException(401, "密码不正确")
            token = self._new_session(state, role)
            self._save(state)
            return token

    def session_valid(self, token: str | None, role: str) -> bool:
        if role not in SESSION_SECONDS or not isinstance(token, str) or len(token) != 43 or not token.isascii():
            return False
        state = self._load()
        session = state["sessions"].get(hashlib.sha256(token.encode()).hexdigest())
        if role == "ordering" and not state["ordering_enabled"]:
            return False
        return bool(state["admin_password"]) and self._unexpired(session, role)

    def revoke_session(self, token: str | None, role: str) -> None:
        if role not in SESSION_SECONDS or not isinstance(token, str) or len(token) != 43 or not token.isascii():
            return
        digest = hashlib.sha256(token.encode()).hexdigest()
        with file_lock(self.path):
            state = self._load()
            session = state["sessions"].get(digest)
            if isinstance(session, dict) and session.get("role") == role:
                state["sessions"].pop(digest)
                self._save(state)

    def login(self, request: Request, response: Response, role: str, password: str) -> dict[str, bool]:
        token = self.authenticate_password(role, password, self._peer(request))
        self._cookie(response, request, role, token)
        state = self._load()
        result = self._status(state, request)
        result[f"{role}_authenticated"] = True
        if role == "admin":
            result["ordering_authenticated"] = state["ordering_enabled"]
        return result

    def logout(self, request: Request, response: Response, role: str) -> dict[str, bool]:
        with file_lock(self.path):
            state = self._load()
            state["sessions"].pop(self._token(request, role), None)
            self._save(state)
        self._cookie(response, request, role, None)
        return self._status(state, request)

    def settings(self, request: Request) -> dict[str, bool]:
        self.require_admin(request)
        return self.status(request)

    def update_settings(self, request: Request, payload: dict[str, Any]) -> dict[str, bool]:
        self.require_admin(request)
        for key in ("ordering_enabled", "ordering_password_required"):
            if type(payload.get(key)) is not bool:
                raise HTTPException(422, "请提供点餐开关和密码开关")
        supplied = payload.get("ordering_password")
        new_password = _password(supplied, new=True) if supplied is not None else None
        with file_lock(self.path):
            state = self._load()
            if not self._authenticated(state, request, "admin"):
                raise HTTPException(401, "请重新登录管理员")
            if payload["ordering_enabled"] and payload["ordering_password_required"] and not state["ordering_password"] and new_password is None:
                raise HTTPException(422, "启用点餐密码时请先设置密码")
            changed = any(state[key] != payload[key] for key in ("ordering_enabled", "ordering_password_required")) or new_password is not None
            state.update({key: payload[key] for key in ("ordering_enabled", "ordering_password_required")})
            if new_password is not None:
                state["ordering_password"] = _password_hash(new_password)
            if changed:
                state["sessions"] = {key: session for key, session in state["sessions"].items() if session["role"] != "ordering"}
            self._save(state)
            return self._status(state, request)

    def change_admin_password(self, request: Request, response: Response, payload: dict[str, Any]) -> dict[str, bool]:
        self.require_admin(request)
        current = _password(payload.get("current_password"))
        password = _password(payload.get("new_password"), new=True)
        with file_lock(self.path):
            state = self._load()
            if not self._authenticated(state, request, "admin"):
                raise HTTPException(401, "请重新登录管理员")
            self._attempt(state, self._peer(request), "admin_change")
            if not _password_matches(current, state["admin_password"]):
                raise HTTPException(400, "当前密码不正确")
            state["admin_password"] = _password_hash(password)
            state["sessions"] = {key: session for key, session in state["sessions"].items() if session["role"] != "admin"}
            token = self._new_session(state, "admin")
            self._save(state)
        self._cookie(response, request, "admin", token)
        result = self._status(state, request)
        result["admin_authenticated"] = True
        result["ordering_authenticated"] = state["ordering_enabled"]
        return result

    def reset_admin_password(self, password: str) -> None:
        """Local operator recovery; this method is never exposed by HTTP."""
        password = _password(password, new=True)
        with file_lock(self.path):
            state = self._load()
            state["admin_password"] = _password_hash(password)
            state["sessions"] = {}
            state["attempts"] = {}
            self._save(state)
            self.setup_token_path.unlink(missing_ok=True)


def create_access_router(access: AccessControl) -> APIRouter:
    router = APIRouter(prefix="/api/access", tags=["access"])

    @router.get("/status")
    def status(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return access.status(request)

    @router.post("/setup")
    async def setup(request: Request, response: Response):
        payload = await _payload(request, {"setup_token", "password"})
        return await run_in_threadpool(access.setup, request, response, payload)

    @router.post("/admin/login")
    async def admin_login(request: Request, response: Response):
        payload = await _payload(request, {"password"})
        return await run_in_threadpool(access.login, request, response, "admin", payload.get("password"))

    @router.post("/ordering/login")
    async def ordering_login(request: Request, response: Response):
        payload = await _payload(request, {"password"})
        return await run_in_threadpool(access.login, request, response, "ordering", payload.get("password"))

    @router.post("/admin/logout")
    async def admin_logout(request: Request, response: Response):
        require_access_request(request)
        return await run_in_threadpool(access.logout, request, response, "admin")

    @router.post("/ordering/logout")
    async def ordering_logout(request: Request, response: Response):
        require_access_request(request)
        return await run_in_threadpool(access.logout, request, response, "ordering")

    @router.get("/settings")
    def settings(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return access.settings(request)

    @router.put("/settings")
    async def update_settings(request: Request, response: Response):
        payload = await _payload(request, {"ordering_enabled", "ordering_password_required", "ordering_password"})
        response.headers["Cache-Control"] = "no-store"
        return await run_in_threadpool(access.update_settings, request, payload)

    @router.put("/admin/password")
    async def change_password(request: Request, response: Response):
        payload = await _payload(request, {"current_password", "new_password"})
        return await run_in_threadpool(access.change_admin_password, request, response, payload)

    return router


def main() -> None:
    parser = argparse.ArgumentParser(description="在服务器本机设置或重置管理员密码，并撤销所有已登录会话")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("command", choices=["reset-admin"])
    args = parser.parse_args()
    password = getpass.getpass("新管理员密码（至少 8 个字符）: ")
    confirmation = getpass.getpass("再次输入密码: ")
    if password != confirmation:
        parser.error("两次密码不一致，未修改")
    try:
        AccessControl(args.project_root).reset_admin_password(password)
    except HTTPException as exc:
        parser.error(str(exc.detail))
    print("管理员密码已更新，所有登录会话已撤销。")


if __name__ == "__main__":
    main()
