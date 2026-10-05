"""Menu publishing assets, device pairing and synchronized practice records."""
from __future__ import annotations

import base64
import io
import re
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse

from .admin_settings import enqueue_settings_operation, register_settings_download, register_settings_operation
from .branding import configured_logo_path
from .menu_image import MenuImageOptions, generate_menu_images, menu_image_zip
from .mobile_sync import ValidationError


def _service(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc


def _qr(value: str) -> str:
    import qrcode
    output = io.BytesIO()
    qrcode.make(value).save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _base_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise HTTPException(422, "请输入有效的点餐网址")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
        raise HTTPException(422, "请输入仅含协议、服务器地址和端口的点餐网址")
    return value.rstrip("/")


def _render_menu(ctx, arguments: dict) -> dict:
    selected = set(arguments["recipe_ids"])
    rows = [row for row in ctx.rows() if row["id"] in selected and row["published"] and not row["hidden"]]
    if not rows:
        raise ValueError("所选菜品已下架或隐藏，请重新选择后生成")
    config = ctx.config()
    options = MenuImageOptions(
        title=arguments["title"], subtitle=arguments["subtitle"], footer=arguments["footer"],
        image_format=arguments["image_format"], include_photos=arguments["include_photos"],
        brand_name=config.restaurant_name, brand_subtitle=config.restaurant_subtitle,
        logo_path=configured_logo_path(config, ctx.store.project_root),
    )
    recipes = [{**row["payload"], "published": True, "recommended": row["recommended"]} for row in rows]
    result = generate_menu_images(recipes, ctx.store.asset_path, options)
    downloads = []
    with tempfile.TemporaryDirectory(prefix="recipe-menu-export-") as temporary:
        directory = Path(temporary)
        for image in result.files:
            path = directory / Path(image.name).name
            path.write_bytes(image.content)
            downloads.append({**register_settings_download(ctx, path), "width": image.width, "height": image.height})
        if len(result.files) > 1:
            archive = directory / "chef-zhai-menu-a4.zip"
            archive.write_bytes(menu_image_zip(result))
            primary = register_settings_download(ctx, archive)
        else:
            primary = downloads[0]
    return {**primary, "files": downloads, "recipe_count": result.recipe_count, "category_count": result.category_count,
            "skipped_count": len(selected) - len(rows)}


def _practice_updates(payload: dict) -> dict:
    allowed = {"cooked_on", "outcome", "rating", "notes"}
    if set(payload) - allowed:
        raise HTTPException(422, "只允许修改日期、结果、评分和心得")
    if "cooked_on" in payload:
        try:
            datetime.strptime(payload["cooked_on"], "%Y-%m-%d")
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "实践日期格式应为 YYYY-MM-DD") from exc
    if "outcome" in payload and payload["outcome"] not in {None, "", "success", "partial", "failed"}:
        raise HTTPException(422, "请选择有效的实践结果")
    if "rating" in payload and payload["rating"] is not None and (type(payload["rating"]) is not int or not 1 <= payload["rating"] <= 5):
        raise HTTPException(422, "评分应为 1–5 或留空")
    if "notes" in payload and (not isinstance(payload["notes"], str) or not 1 <= len(payload["notes"].strip()) <= 5000):
        raise HTTPException(422, "心得需为 1–5000 字")
    return payload


def create_router(ctx) -> APIRouter:
    router = APIRouter()
    register_settings_operation("menu_image", _render_menu)

    def current_log(log_id: str) -> dict:
        for item in ctx.store.list_practice_logs():
            if item["id"] == log_id:
                return item
        raise HTTPException(404, "实践日志不存在或已删除")

    @router.get("/menu")
    def menu(q: str = "", offset: int = 0, limit: int = 50):
        rows = ctx.rows()
        visible = [row for row in rows if not row["hidden"]]
        matching = [row for row in visible if q.casefold() in f"{row['title']} {row['creator_name']}".casefold()]
        matching.sort(key=lambda row: (not row["recommended"], row["title"]))
        offset, limit = max(0, offset), max(1, min(limit, 100))
        return {"items": [{key: row[key] for key in ("id", "title", "creator_name", "published", "recommended", "category")} for row in matching[offset:offset + limit]],
                "total": len(matching), "published": sum(row["published"] for row in visible),
                "recommended": sum(row["recommended"] for row in visible), "indexing": ctx.indexer.snapshot()}

    @router.post("/menu/recommendations")
    def recommendations(payload: dict = Body(...)):
        recipe_ids, enabled = payload.get("recipe_ids"), payload.get("recommended")
        if not isinstance(recipe_ids, list) or not recipe_ids or len(recipe_ids) > 500 or any(not isinstance(item, str) for item in recipe_ids) or type(enabled) is not bool:
            raise HTTPException(422, "请选择 1–500 道菜品和推荐状态")
        by_id = {row["id"]: row for row in ctx.rows()}
        if any(recipe_id not in by_id for recipe_id in recipe_ids):
            raise HTTPException(404, "部分菜谱已不存在，请刷新列表")
        if enabled and any(by_id[recipe_id]["hidden"] for recipe_id in recipe_ids):
            raise HTTPException(409, "隐藏内容不能设为推荐，请先在审核工作台核对展示决定")
        return {"updated": _service(ctx.store.set_recipe_recommendations, {recipe_id: enabled for recipe_id in recipe_ids})}

    @router.post("/menu/images")
    def menu_images(payload: dict = Body(...)):
        visible = {row["id"] for row in ctx.rows() if row["published"] and not row["hidden"]}
        recipe_ids = payload.get("recipe_ids", sorted(visible))
        if not isinstance(recipe_ids, list) or not recipe_ids or len(recipe_ids) > 5000 or any(not isinstance(item, str) or item not in visible for item in recipe_ids):
            raise HTTPException(422, "请选择当前已上架且可展示的菜品")
        config = ctx.config()
        arguments = {"recipe_ids": list(dict.fromkeys(recipe_ids))}
        for key, default in (("title", f"{config.restaurant_name} · 本周菜单"), ("subtitle", "主厨精选 · 新鲜现做"), ("footer", "请直接回复菜名预订 · 菜品以当日供应为准")):
            value = payload.get(key, default)
            if not isinstance(value, str) or len(value) > 500:
                raise HTTPException(422, "菜单文字不能超过 500 字")
            arguments[key] = value
        image_format = payload.get("image_format", "share")
        if image_format not in {"share", "print"} or type(payload.get("include_photos", True)) is not bool:
            raise HTTPException(422, "请选择长图或 A4 打印格式")
        if image_format == "share" and len(arguments["recipe_ids"]) > 500:
            raise HTTPException(422, "单张长图最多支持 500 道菜，请选择部分菜品或使用 A4 分页格式")
        arguments.update(image_format=image_format, include_photos=payload.get("include_photos", True))
        return enqueue_settings_operation(ctx, "menu_image", arguments)

    @router.post("/menu/link")
    def kitchen_link(request: Request, payload: dict = Body(default={})):
        url = _base_url(payload.get("base_url", str(request.base_url)))
        return {"url": url, "qr": _qr(url), "self_join_enabled": ctx.store.self_join_enabled()}

    @router.post("/menu/pairing")
    def pairing(request: Request, payload: dict = Body(default={})):
        url = _base_url(payload.get("base_url", str(request.base_url)))
        credential = ctx.store.issue_pairing_credential(url)
        content = credential.qr_payload()
        return {"payload": content, "expires_at": credential.expires_at, "qr": _qr(content), "base_url": url}

    @router.get("/menu/practice")
    def practice(query: str = "", recipe_id: str = "", offset: int = 0, limit: int = 50):
        titles = {row["id"]: row["title"] for row in ctx.rows()}
        entries = [{**item, "recipe_title": titles.get(item["recipe_id"], "未知菜谱"),
                    "photo_url": f"/api/admin/v1/menu/media/{item['photo_sha256']}" if item.get("photo_sha256") else None}
                   for item in ctx.store.list_practice_logs(recipe_id=recipe_id or None)]
        entries = [item for item in entries if query.casefold() in f"{item['recipe_title']} {item['notes']}".casefold()]
        offset, limit = max(0, offset), max(1, min(limit, 100))
        return {"items": entries[offset:offset + limit], "total": len(entries)}

    @router.get("/menu/media/{digest}")
    def practice_photo(digest: str):
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not any(item.get("photo_sha256") == digest for item in ctx.store.list_practice_logs()):
            raise HTTPException(404, "实践照片不存在")
        found = ctx.store.asset_path(digest)
        if not found:
            raise HTTPException(404, "实践照片不存在")
        return FileResponse(found[0], media_type=found[1])

    @router.put("/menu/practice/{log_id}")
    def update_practice(log_id: str, payload: dict = Body(...)):
        current_log(log_id)
        updates = _practice_updates(payload)
        return _service(ctx.store.admin_update_practice, log_id, updates)

    @router.delete("/menu/practice/{log_id}")
    def delete_practice(log_id: str):
        current_log(log_id)
        return _service(ctx.store.admin_update_practice, log_id, {}, delete=True)

    @router.get("/menu/conflicts")
    def conflicts():
        titles = {row["id"]: row["title"] for row in ctx.rows()}
        result = []
        for item in ctx.store.list_conflicts():
            result.append({key: value for key, value in item.items() if key not in {"incoming_json", "server_json"}} | {
                "recipe_title": titles.get(item["server"].get("recipe_id") or item["incoming"].get("recipe_id"), "未知菜谱")})
        return {"items": result}

    @router.post("/menu/conflicts/{conflict_id}/resolve")
    def resolve_conflict(conflict_id: str, payload: dict = Body(...)):
        conflict = next((item for item in ctx.store.list_conflicts() if item["id"] == conflict_id), None)
        if conflict is None:
            raise HTTPException(404, "冲突不存在或已解决")
        resolution = payload.get("resolution")
        if resolution not in {"server", "incoming", "merged"}:
            raise HTTPException(422, "请选择服务器版本、离线版本或手工合并")
        merged = None
        if resolution == "merged":
            updates = payload.get("updates")
            if not isinstance(updates, dict):
                raise HTTPException(422, "请填写合并后的日期、结果、评分和心得")
            merged = {**conflict["server"], **conflict["incoming"], **_practice_updates(updates)}
            merged["id"] = conflict["entity_id"]
            merged.pop("action", None)
        _service(ctx.store.resolve_conflict, conflict_id, resolution, merged)
        return {"resolved": True}

    return router
