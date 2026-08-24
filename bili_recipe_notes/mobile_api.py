from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .mobile_sync import (
    MAX_PRACTICE_PHOTO_BYTES,
    AuthenticationError,
    MobileSyncStore,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    ValidationError,
    is_private_client,
)
from .branding import branding_payload, configured_logo_path
from .config import load_config


class PairRequest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    pairing_token: str
    device_name: str = Field(min_length=1, max_length=80)
    device_id: str | None = None


class JoinRequest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    device_name: str = Field(min_length=1, max_length=80)
    device_id: str | None = None


class SyncRequest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    cursor: int = Field(default=0, ge=0)
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    capabilities: list[str] | None = None


def create_app(
    project_root: str | Path | None = None,
    *,
    out_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    static_dir: str | Path | None = None,
    start_background_indexer: bool = True,
) -> FastAPI:
    store = MobileSyncStore(project_root, out_dir=out_dir, database_path=database_path)

    async def index_loop() -> None:
        while True:
            await asyncio.to_thread(store.index_recipes)
            await asyncio.sleep(10)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await asyncio.to_thread(store.index_recipes)
        task = asyncio.create_task(index_loop()) if start_background_indexer else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Chef Zhai Family Kitchen API", version=str(PROTOCOL_VERSION), lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.state.store = store

    @app.middleware("http")
    async def private_network_only(request: Request, call_next):
        client_host = request.client.host if request.client else "127.0.0.1"
        if not is_private_client(client_host):
            return Response(status_code=status.HTTP_403_FORBIDDEN, content="Private network access only")
        return await call_next(request)

    def authenticated_device(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Device token required")
        try:
            return store.authenticate(authorization.removeprefix("Bearer ").strip())
        except AuthenticationError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "server_id": store.server_id,
            "revision": store.current_revision(),
            "self_join_enabled": store.self_join_enabled(),
        }

    @app.get("/api/v1/branding")
    def branding() -> dict[str, Any]:
        return branding_payload(load_config(store.project_root), store.project_root, logo_url="/api/v1/branding/logo")

    @app.get("/api/v1/branding/logo")
    def branding_logo():
        path = configured_logo_path(load_config(store.project_root), store.project_root)
        if path is None:
            raise HTTPException(status_code=404, detail="Logo not configured")
        return FileResponse(path, media_type="image/png", filename=path.name)

    @app.post("/api/v1/join")
    def join(payload: JoinRequest) -> dict[str, Any]:
        if payload.schema_version != SCHEMA_VERSION:
            raise HTTPException(status_code=400, detail="Unsupported schema_version")
        try:
            return store.join_device(payload.device_name, payload.device_id)
        except AuthenticationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/pair")
    def pair(payload: PairRequest) -> dict[str, Any]:
        if payload.schema_version != SCHEMA_VERSION:
            raise HTTPException(status_code=400, detail="Unsupported schema_version")
        try:
            paired = store.pair_device(payload.pairing_token, payload.device_name, payload.device_id)
            return {**paired, "device_name": payload.device_name.strip()}
        except (AuthenticationError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/sync")
    def sync(payload: SyncRequest, device: dict[str, Any] = Depends(authenticated_device)) -> dict[str, Any]:
        if payload.schema_version != SCHEMA_VERSION:
            raise HTTPException(status_code=400, detail="Unsupported schema_version")
        try:
            return store.sync(device["id"], payload.cursor, payload.operations, payload.capabilities)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/events")
    async def events(
        request: Request,
        device: dict[str, Any] = Depends(authenticated_device),
    ) -> StreamingResponse:
        del device

        async def stream():
            last_revision = -1
            heartbeat = 0
            while not await request.is_disconnected():
                revision = await asyncio.to_thread(store.current_revision)
                if revision != last_revision:
                    yield f"event: revision\ndata: {json.dumps({'revision': revision})}\n\n"
                    last_revision = revision
                    heartbeat = 0
                elif heartbeat >= 15:
                    yield ": keep-alive\n\n"
                    heartbeat = 0
                await asyncio.sleep(1)
                heartbeat += 1

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/meal-history")
    def meal_history(
        limit: int = 20,
        device: dict[str, Any] = Depends(authenticated_device),
    ) -> dict[str, Any]:
        del device
        return {"meals": store.list_meal_history(limit)}

    @app.put("/api/v1/assets/{digest}")
    async def upload_asset(
        digest: str,
        request: Request,
        device: dict[str, Any] = Depends(authenticated_device),
    ) -> dict[str, Any]:
        del device
        length = request.headers.get("content-length")
        if length:
            try:
                declared_length = int(length)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
            if declared_length > MAX_PRACTICE_PHOTO_BYTES:
                raise HTTPException(status_code=413, detail="Practice photo exceeds 5 MiB")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_PRACTICE_PHOTO_BYTES:
                raise HTTPException(status_code=413, detail="Practice photo exceeds 5 MiB")
        try:
            return store.store_asset(digest, bytes(content), request.headers.get("content-type", ""))
        except ValidationError as exc:
            raise HTTPException(status_code=413 if "5 MiB" in str(exc) else 422, detail=str(exc)) from exc

    @app.get("/api/v1/assets/{digest}")
    def download_asset(digest: str, device: dict[str, Any] = Depends(authenticated_device)):
        del device
        found = store.asset_path(digest)
        if not found:
            raise HTTPException(status_code=404, detail="Asset not found")
        path, mime_type = found
        return FileResponse(path, media_type=mime_type, filename=path.name)

    built_web = Path(static_dir).resolve() if static_dir else Path(__file__).resolve().parents[1] / "web" / "dist"
    index_file = built_web / "index.html"
    if index_file.is_file():
        @app.get("/{path:path}", include_in_schema=False)
        def static_app(path: str):
            candidate = (built_web / path).resolve()
            try:
                candidate.relative_to(built_web)
            except ValueError:
                candidate = index_file
            served = candidate if candidate.is_file() else index_file
            response = FileResponse(served)
            if served == index_file or served.name == "sw.js":
                response.headers["Cache-Control"] = "no-store"
            elif "assets" in served.parts:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response

    return app


app = create_app()
