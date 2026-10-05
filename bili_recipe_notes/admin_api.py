"""Compose the React administration API without importing the legacy UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.routing import APIRoute

from .admin_common import AdminContext, require_admin_request
from .storage import StorageError


class AdminRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except HTTPException:
                raise
            except FileNotFoundError as exc:
                raise HTTPException(404, str(exc)) from exc
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from exc
            except (StorageError, OSError, RuntimeError) as exc:
                raise HTTPException(409, str(exc)) from exc

        return handle


def create_admin_router(store, indexer):
    from . import admin_curation, admin_library, admin_menu, admin_recipe_tools, admin_review, admin_settings, admin_tasks

    context = AdminContext(store, indexer)
    router = APIRouter(prefix="/api/admin/v1", dependencies=[Depends(require_admin_request)])
    for module in (admin_library, admin_review, admin_tasks, admin_settings, admin_curation, admin_recipe_tools, admin_menu):
        routes = module.create_router(context)
        for route in routes.routes:
            if isinstance(route, APIRoute):
                router.add_api_route(
                    route.path, route.endpoint, methods=route.methods,
                    response_model=route.response_model, status_code=route.status_code,
                    response_class=route.response_class, responses=route.responses,
                    dependencies=route.dependencies, name=route.name, tags=route.tags,
                    summary=route.summary, description=route.description,
                    include_in_schema=route.include_in_schema, route_class_override=AdminRoute,
                )
    return router
