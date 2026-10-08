from __future__ import annotations

import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from bili_recipe_notes.access_control import ORDERING_COOKIE
from bili_recipe_notes.mobile_api import create_app
from bili_recipe_notes.mobile_sync import PROTOCOL_VERSION, SCHEMA_VERSION
from test_admin_library import make_recipe


ACCESS_HEADERS = {"X-Access-Request": "1"}
ORDERING_PASSWORD = "synthetic-ordering-password"


@pytest.fixture
def kitchen(tmp_path, authorize_client):
    root = tmp_path / "data" / "recipes"
    make_recipe(root)
    app = create_app(tmp_path, out_dir=root, start_background_indexer=False)
    with TestClient(app) as admin:
        authorize_client(admin)
        response = admin.put("/api/access/settings", headers=ACCESS_HEADERS, json={
            "ordering_enabled": True, "ordering_password_required": True,
            "ordering_password": ORDERING_PASSWORD,
        })
        assert response.status_code == 200, response.text
        with TestClient(app) as guest:
            yield app, admin, guest


@pytest.mark.parametrize("path", [
    "/api/admin/v1/overview", "/api/%61dmin/v1/recipes", "/api/admin/v1/tasks",
    "/api/admin/v1/settings", "/api/admin/v1/recipes/missing/media/cover",
    "/api/admin/v1/downloads/missing", "/api/admin/v1/curation/export",
])
def test_admin_api_and_media_require_cookie_even_with_public_header(kitchen, path):
    _, _, guest = kitchen
    response = guest.get(path, headers={"X-Admin-Request": "1"})
    assert response.status_code == 401, response.text
    assert response.headers["cache-control"] == "no-store"


def test_admin_image_is_private_and_replayed_logged_out_cookie_cannot_download(kitchen):
    _, admin, guest = kitchen
    recipe_id = admin.get("/api/admin/v1/recipes").json()["items"][0]["id"]
    image = admin.get(f"/api/admin/v1/recipes/{recipe_id}").json()["step_images"][0]["url"]
    response = admin.get(image, headers={"X-Admin-Request": ""})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert guest.get(image).status_code == 401
    guest.cookies.update(admin.cookies)
    assert admin.post("/api/access/admin/logout", headers=ACCESS_HEADERS).status_code == 200
    assert guest.get(image).status_code == 401


def test_existing_device_token_does_not_bypass_ordering_password_or_disable(kitchen):
    app, admin, guest = kitchen
    store = app.state.store
    device = store.join_device("existing tablet")
    pairing = store.issue_pairing_credential("http://testserver")
    digest = hashlib.sha256(b"private photo").hexdigest()
    store.store_asset(digest, b"private photo", "image/jpeg")
    headers = {"Authorization": f"Bearer {device['access_token']}"}
    endpoints = [
        ("POST", "/api/v1/join", {"device_name": "new guest"}),
        ("POST", "/api/v1/pair", {"pairing_token": pairing.pairing_token, "device_name": "legacy app"}),
        ("POST", "/api/v1/sync", {"cursor": 0, "operations": []}),
        ("GET", "/api/v1/index-status", None), ("GET", "/api/v1/meal-history", None),
        ("GET", "/api/v1/events", None), ("GET", f"/api/v1/assets/{digest}", None),
        ("PUT", f"/api/v1/assets/{digest}", None),
    ]
    for method, path, body in endpoints:
        response = guest.request(method, path, headers=headers, json=body)
        assert response.status_code == 401, (path, response.text)
    assert guest.post("/api/access/ordering/login", headers=ACCESS_HEADERS,
                      json={"password": ORDERING_PASSWORD}).status_code == 200
    assert guest.get(f"/api/v1/assets/{digest}", headers=headers).content == b"private photo"
    assert guest.get("/api/v1/index-status").status_code == 401  # Device identity is still required.
    assert guest.get("/api/v1/index-status", headers=headers).status_code == 200
    disabled = admin.put("/api/access/settings", headers=ACCESS_HEADERS, json={
        "ordering_enabled": False, "ordering_password_required": True,
    })
    assert disabled.status_code == 200
    for method, path, body in endpoints:
        response = guest.request(method, path, headers=headers, json=body)
        assert response.status_code == 403, (path, response.text)
    assert admin.get("/api/admin/v1/overview").status_code == 200


def test_public_health_hides_library_revision_devices_and_index_errors(kitchen, monkeypatch):
    app, admin, guest = kitchen
    snapshot = {"status": "degraded", "last_error": "/private/user-data/recipes is unavailable"}
    monkeypatch.setattr(app.state.indexer, "snapshot", lambda: snapshot)
    assert guest.get("/api/v1/health").json() == {"schema_version": SCHEMA_VERSION, "protocol_version": PROTOCOL_VERSION}
    assert "/private/" not in guest.get("/api/v1/health").text
    assert admin.get("/api/v1/health").json()["indexing"] == snapshot
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert guest.get(path).status_code == 401
    assert admin.get("/openapi.json").status_code == 200


def test_cross_origin_ordering_request_cannot_use_cookie_and_device(kitchen):
    app, _, guest = kitchen
    device = app.state.store.join_device("tablet")
    assert guest.post("/api/access/ordering/login", headers=ACCESS_HEADERS,
                      json={"password": ORDERING_PASSWORD}).status_code == 200
    for extra in ({"Origin": "https://untrusted.example"}, {"Sec-Fetch-Site": "cross-site"}):
        response = guest.post("/api/v1/sync", headers={
            "Authorization": f"Bearer {device['access_token']}", **extra,
        }, json={"cursor": 0, "operations": []})
        assert response.status_code == 403


@pytest.mark.parametrize("change", ["disabled", "password", "logout"])
def test_open_ordering_event_stream_stops_on_access_revocation(kitchen, change):
    app, admin, guest = kitchen
    device = app.state.store.join_device("tablet")
    assert guest.post("/api/access/ordering/login", headers=ACCESS_HEADERS,
                      json={"password": ORDERING_PASSWORD}).status_code == 200
    cookie = f"{ORDERING_COOKIE}={guest.cookies.get(ORDERING_COOKIE)}"
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/v1/events")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def check_stream():
        request = Request({"type": "http", "method": "GET", "path": "/api/v1/events",
                           "headers": [(b"cookie", cookie.encode())], "app": app}, receive=receive)
        response = await endpoint(request, device)
        iterator = response.body_iterator
        assert "event: revision" in await anext(iterator)
        if change == "logout":
            changed = guest.post("/api/access/ordering/logout", headers=ACCESS_HEADERS)
        else:
            payload = {"ordering_enabled": change != "disabled", "ordering_password_required": True}
            if change == "password":
                payload["ordering_password"] = "changed-ordering-password"
            changed = admin.put("/api/access/settings", headers=ACCESS_HEADERS, json=payload)
        assert changed.status_code == 200
        denied = await anext(iterator)
        assert "event: access-denied" in denied and "revision" not in denied
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)

    asyncio.run(check_stream())
