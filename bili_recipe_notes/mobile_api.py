from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
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


class PairRequest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    pairing_token: str
    device_name: str = Field(min_length=1, max_length=80)
    device_id: str | None = None


class SyncRequest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    cursor: int = Field(default=0, ge=0)
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


def create_app(
    project_root: str | Path | None = None,
    *,
    out_dir: str | Path | None = None,
    database_path: str | Path | None = None,
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

    app = FastAPI(title="Bili Recipe Notes Mobile API", version=str(PROTOCOL_VERSION), lifespan=lifespan)
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
        }

    @app.post("/api/v1/pair")
    def pair(payload: PairRequest) -> dict[str, Any]:
        if payload.schema_version != SCHEMA_VERSION:
            raise HTTPException(status_code=400, detail="Unsupported schema_version")
        try:
            return store.pair_device(payload.pairing_token, payload.device_name, payload.device_id)
        except (AuthenticationError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/sync")
    def sync(payload: SyncRequest, device: dict[str, Any] = Depends(authenticated_device)) -> dict[str, Any]:
        if payload.schema_version != SCHEMA_VERSION:
            raise HTTPException(status_code=400, detail="Unsupported schema_version")
        try:
            return store.sync(device["id"], payload.cursor, payload.operations)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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

    return app


app = create_app()
