from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.requests import Request

from bili_recipe_notes.mobile_api import create_app

ACCESS_HEADERS = {"X-Access-Request": "1"}
ADMIN_HEADERS = {"X-Admin-Request": "1"}


def setup(client, app):
    response = client.post("/api/access/setup", headers=ACCESS_HEADERS, json={
        "setup_token": app.state.access_control.setup_token_path.read_text().strip(),
        "password": "Administrator!23",
    })
    assert response.status_code == 200


def settings(client, *, enabled=True, required=True, password=None):
    data = {"ordering_enabled": enabled, "ordering_password_required": required}
    if password:
        data["ordering_password"] = password
    response = client.put("/api/access/settings", headers=ACCESS_HEADERS, json=data)
    assert response.status_code == 200, response.text


def test_default_blocks_all_admin_and_existing_device_paths(tmp_path):
    app = create_app(tmp_path, start_background_indexer=False)
    paired = app.state.store.join_device("previous device")
    headers = {"Authorization": f"Bearer {paired['access_token']}"}
    with TestClient(app) as client:
        state = client.get("/api/access/status").json()
        assert state["ordering_password_required"] and not state["admin_authenticated"]
        for path in ("/api/admin/v1/settings", "/api/admin/v1/recipes", "/api/admin/v1/tasks",
                     "/api/admin/v1/media/arbitrary/image", "/api/admin/v1/downloads/arbitrary"):
            # Unknown protected routes may resolve 404, but no private response.
            assert client.get(path, headers=ADMIN_HEADERS).status_code in (401, 404)
        for path in ("/docs", "/redoc", "/openapi.json", "/api/v1/index-status", "/api/v1/assets/abc"):
            assert client.get(path, headers=headers).status_code == 401
        assert client.post("/api/v1/sync", headers=headers, json={}).status_code == 401
        assert client.post("/api/v1/join", json={"device_name": "stranger"}).status_code == 401
        assert client.post("/api/v1/pair", json={"pairing_token": "anything", "device_name": "stranger"}).status_code == 401
        assert "indexing" not in client.get("/api/v1/health").json()
        assert client.get("/api/access/status").headers["cache-control"] == "no-store"


def test_ordering_login_closure_rotation_and_public_mode_enforced_on_api(tmp_path):
    app = create_app(tmp_path, start_background_indexer=False)
    with TestClient(app) as admin, TestClient(app) as visitor:
        setup(admin, app)
        settings(admin, password="Family-password1")
        assert visitor.post("/api/access/ordering/login", headers=ACCESS_HEADERS,
                            json={"password": "wrong"}).status_code == 401
        assert visitor.post("/api/access/ordering/login", headers=ACCESS_HEADERS,
                            json={"password": "Family-password1"}).status_code == 200
        assert visitor.get("/api/admin/v1/settings", headers=ADMIN_HEADERS).status_code == 401
        paired = visitor.post("/api/v1/join", json={"device_name": "family"}).json()
        device_headers = {"Authorization": f"Bearer {paired['access_token']}"}
        assert visitor.post("/api/v1/sync", headers=device_headers, json={}).status_code == 200
        cookie = visitor.cookies.get("chef_ordering_session")
        assert cookie
        cookie_request = Request({"type": "http", "headers": [(b"cookie", f"chef_ordering_session={cookie}".encode())]})
        assert app.state.access_control.ordering_access_allowed(cookie_request)
        settings(admin, enabled=False)
        assert visitor.post("/api/v1/sync", headers=device_headers, json={}).status_code == 403
        assert not app.state.access_control.ordering_access_allowed(cookie_request)
        assert admin.post("/api/v1/join", json={"device_name": "admin cannot bypass closure"}).status_code == 403
        settings(admin)
        assert visitor.post("/api/v1/sync", headers=device_headers, json={}).status_code == 401
        visitor.post("/api/access/ordering/login", headers=ACCESS_HEADERS, json={"password": "Family-password1"})
        settings(admin, password="Family-password2")
        assert visitor.post("/api/v1/sync", headers=device_headers, json={}).status_code == 401
        assert visitor.post("/api/access/ordering/login", headers=ACCESS_HEADERS,
                            json={"password": "Family-password1"}).status_code == 401
        settings(admin, required=False)
        visitor.cookies.clear()
        assert visitor.post("/api/v1/sync", headers=device_headers, json={}).status_code == 200
        assert visitor.post("/api/v1/sync", headers={**device_headers, "Origin": "http://evil.test"}, json={}).status_code == 403
        assert visitor.get("/api/admin/v1/settings", headers=ADMIN_HEADERS).status_code == 401


def test_admin_cookie_always_required_for_downloads_and_mutation_and_expiry(tmp_path):
    from bili_recipe_notes.storage import atomic_write_json
    app = create_app(tmp_path, start_background_indexer=False)
    with TestClient(app) as admin:
        setup(admin, app)
        assert admin.get("/api/admin/v1/settings", headers=ADMIN_HEADERS).status_code == 200
        assert admin.get("/api/admin/v1/settings", headers={**ADMIN_HEADERS, "Origin": "http://evil.test"}).status_code == 403
        path = app.state.access_control.path
        raw = json.loads(path.read_text())
        for session in raw["sessions"].values():
            session["expires_at"] = 1
        atomic_write_json(path, raw)
        assert admin.get("/api/admin/v1/settings", headers=ADMIN_HEADERS).status_code == 401
        assert admin.get("/api/admin/v1/recipes", headers=ADMIN_HEADERS).status_code == 401


def test_sensitive_access_state_and_bootstrap_never_enter_deployment(tmp_path):
    from bili_recipe_notes.deployment import _state_files
    from bili_recipe_notes.data_paths import state_dir
    root = state_dir(tmp_path)
    root.mkdir(parents=True)
    for name in ("access-control.json", "access-control.json.bak", "admin-setup-token", "admin-setup-token.bak"):
        (root / name).write_text("secret")
    (root / "ordinary.json").write_text("{}")
    files = dict(_state_files(tmp_path, tmp_path / "recipes"))
    assert files == {"state/ordinary.json": b"{}"}
