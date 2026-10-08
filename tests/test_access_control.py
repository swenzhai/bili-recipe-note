from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from bili_recipe_notes import access_control as access_module
from bili_recipe_notes.access_control import ADMIN_COOKIE, ORDERING_COOKIE, AccessControl, create_access_router

ADMIN_PASSWORD = "correct-admin-password"
ORDERING_PASSWORD = "family-order-password"
ACCESS_HEADERS = {"X-Access-Request": "1"}


def access_app(tmp_path: Path) -> tuple[FastAPI, AccessControl]:
    access = AccessControl(tmp_path)
    app = FastAPI()
    app.state.access_control = access
    app.include_router(create_access_router(access))

    @app.get("/protected/admin")
    def admin(request: Request):
        access.require_admin(request)
        return {"ok": True}

    @app.get("/protected/ordering")
    def ordering(request: Request):
        access.require_ordering(request)
        return {"ok": True}

    return app, access


def initialize(client: TestClient, access: AccessControl):
    response = client.post("/api/access/setup", headers=ACCESS_HEADERS,
                           json={"setup_token": access.setup_token_path.read_text().strip(), "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return response


def settings(client: TestClient, *, enabled=True, required=True, password=None):
    payload = {"ordering_enabled": enabled, "ordering_password_required": required}
    if password is not None:
        payload["ordering_password"] = password
    return client.put("/api/access/settings", headers=ACCESS_HEADERS, json=payload)


def login(client: TestClient, role="admin", password=ADMIN_PASSWORD):
    return client.post(f"/api/access/{role}/login", headers=ACCESS_HEADERS, json={"password": password})


def test_default_is_locked_and_bootstrap_secret_is_local_only(tmp_path):
    app, access = access_app(tmp_path)
    client = TestClient(app)
    response = client.get("/api/access/status")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "admin_configured": False, "admin_authenticated": False, "ordering_enabled": True,
        "ordering_password_required": True, "ordering_password_configured": False, "ordering_authenticated": False,
    }
    secret = access.setup_token_path.read_text().strip()
    assert len(secret) == 43 and secret not in response.text
    if os.name != "nt":
        assert stat.S_IMODE(access.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(access.setup_token_path.stat().st_mode) == 0o600
    for endpoint in ("/api/access/settings", "/protected/admin", "/protected/ordering"):
        assert client.get(endpoint).status_code == 401
    bad = client.post("/api/access/setup", headers=ACCESS_HEADERS,
                      json={"setup_token": "some-public-visitor", "password": ADMIN_PASSWORD})
    assert bad.status_code == 401
    assert access.setup_token_path.read_text().strip() == secret
    assert AccessControl(tmp_path).setup_token_path.read_text().strip() == secret


def test_setup_once_password_hash_and_cookie_flags(tmp_path):
    app, access = access_app(tmp_path)
    client = TestClient(app, base_url="https://testserver")
    token = access.setup_token_path.read_text().strip()
    response = initialize(client, access)
    assert response.json()["admin_authenticated"] is True
    assert response.json()["ordering_authenticated"] is True
    assert not access.setup_token_path.exists()
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" in cookie and "Max-Age=43200" in cookie
    serialized = access.path.read_text()
    assert ADMIN_PASSWORD not in serialized and token not in serialized
    assert client.cookies.get(ADMIN_COOKIE) not in serialized
    assert json.loads(serialized)["admin_password"]["iterations"] >= 600_000
    assert client.get("/protected/admin").status_code == 200
    assert client.post("/api/access/setup", headers=ACCESS_HEADERS,
                       json={"setup_token": token, "password": "replacement-password"}).status_code == 409
    assert not AccessControl(tmp_path).setup_token_path.exists()


@pytest.mark.parametrize("headers", [
    {}, {"X-Access-Request": "1", "Origin": "https://evil.example"},
    {"X-Access-Request": "1", "Origin": "null"},
    {"X-Access-Request": "1", "Referer": "http://evil.example/form"},
    {"X-Access-Request": "1", "Origin": "http://testserver:9876"},
    {"X-Access-Request": "1", "Sec-Fetch-Site": "cross-site"},
    {"X-Access-Request": "1", "Origin": "http://[invalid"},
])
def test_setup_rejects_cross_origin_and_missing_custom_header(tmp_path, headers):
    app, access = access_app(tmp_path)
    client = TestClient(app)
    response = client.post("/api/access/setup", headers=headers,
                           json={"setup_token": access.setup_token_path.read_text().strip(), "password": ADMIN_PASSWORD})
    assert response.status_code == 403
    assert client.get("/api/access/status").json()["admin_configured"] is False


def test_same_origin_referer_and_json_limits_do_not_echo_credentials(tmp_path):
    app, access = access_app(tmp_path)
    client = TestClient(app)
    token = access.setup_token_path.read_text().strip()
    for password in ("short", " " * 8, "x" * 129, {"secret": "hidden"}, "\ud800"):
        response = client.post("/api/access/setup", headers={**ACCESS_HEADERS, "Content-Type": "application/json"},
                               content=json.dumps({"setup_token": token, "password": password}))
        assert response.status_code == 422
        assert token not in response.text and "hidden" not in response.text
    response = client.post("/api/access/admin/login", headers=ACCESS_HEADERS, json={"password": "x" * 9000})
    assert response.status_code == 413
    response = client.post("/api/access/admin/login", headers=ACCESS_HEADERS, content="password=hidden")
    assert response.status_code == 415
    headers = {**ACCESS_HEADERS, "Origin": "http://testserver", "Referer": "http://testserver/admin/"}
    assert client.post("/api/access/setup", headers=headers, json={"setup_token": token, "password": ADMIN_PASSWORD}).status_code == 200
    bad = client.put("/api/access/settings", headers=ACCESS_HEADERS,
                     json={"ordering_enabled": "false", "ordering_password_required": True})
    assert bad.status_code == 422
    no_header = client.put("/api/access/settings", json={"ordering_enabled": False, "ordering_password_required": False})
    assert no_header.status_code == 403


def test_ordering_password_is_required_by_default_and_independent_of_admin(tmp_path):
    app, access = access_app(tmp_path)
    admin, guest = TestClient(app), TestClient(app)
    initialize(admin, access)
    assert guest.get("/protected/ordering", headers={"Authorization": "Bearer legacy-device-token"}).status_code == 401
    assert settings(admin).status_code == 422
    assert settings(admin, password=ORDERING_PASSWORD).status_code == 200
    assert login(guest, "ordering", ADMIN_PASSWORD).status_code == 401
    assert login(guest, "ordering", ORDERING_PASSWORD).status_code == 200
    assert guest.get("/protected/ordering").status_code == 200
    assert guest.get("/protected/admin").status_code == 401
    assert guest.get("/api/access/settings").status_code == 401
    guest.cookies.set(ADMIN_COOKIE, guest.cookies.get(ORDERING_COOKIE), domain="testserver.local", path="/")
    assert guest.get("/protected/admin").status_code == 401
    state = access.path.read_text()
    assert ORDERING_PASSWORD not in state


def test_admin_can_disable_unconfigured_ordering_then_enable_without_password(tmp_path):
    app, access = access_app(tmp_path)
    admin, guest = TestClient(app), TestClient(app)
    initialize(admin, access)
    assert settings(admin, enabled=False).status_code == 200
    assert guest.get("/protected/ordering").status_code == 403
    assert admin.get("/protected/ordering").status_code == 403
    assert settings(admin, enabled=True, required=False).status_code == 200
    assert guest.get("/api/access/status").json()["ordering_authenticated"] is True
    assert guest.get("/protected/ordering").status_code == 200
    assert guest.get("/protected/admin").status_code == 401
    assert settings(admin, enabled=True, required=True).status_code == 422


def test_settings_changes_revoke_ordering_sessions_and_disable_existing_devices(tmp_path):
    app, access = access_app(tmp_path)
    admin, guest = TestClient(app), TestClient(app)
    initialize(admin, access)
    assert settings(admin, password=ORDERING_PASSWORD).status_code == 200
    login(guest, "ordering", ORDERING_PASSWORD)
    assert settings(admin, password="replacement-order-password").status_code == 200
    assert guest.get("/protected/ordering").status_code == 401
    assert login(guest, "ordering", ORDERING_PASSWORD).status_code == 401
    assert login(guest, "ordering", "replacement-order-password").status_code == 200
    assert settings(admin, enabled=False).status_code == 200
    assert guest.get("/protected/ordering").status_code == 403
    assert login(guest, "ordering", "replacement-order-password").status_code == 403
    assert settings(admin).status_code == 200
    assert guest.get("/protected/ordering").status_code == 401
    assert admin.get("/protected/admin").status_code == 200


def test_logout_invalidates_replayed_cookie_and_sessions_survive_store_restart(tmp_path):
    app, access = access_app(tmp_path)
    client = TestClient(app)
    initialize(client, access)
    token = client.cookies.get(ADMIN_COOKIE)
    assert AccessControl(tmp_path).session_valid(token, "admin")
    assert client.post("/api/access/admin/logout", headers=ACCESS_HEADERS).status_code == 200
    assert client.get("/protected/admin").status_code == 401
    client.cookies.set(ADMIN_COOKIE, token, domain="testserver.local", path="/")
    assert client.get("/protected/admin").status_code == 401
    assert not AccessControl(tmp_path).session_valid(token, "admin")
    assert login(client).status_code == 200
    assert client.get("/protected/admin").status_code == 200


def test_ordering_logout_invalidates_only_ordering_cookie(tmp_path):
    app, access = access_app(tmp_path)
    admin, guest = TestClient(app), TestClient(app)
    initialize(admin, access)
    settings(admin, password=ORDERING_PASSWORD)
    login(guest, "ordering", ORDERING_PASSWORD)
    token = guest.cookies.get(ORDERING_COOKIE)
    response = guest.post("/api/access/ordering/logout", headers=ACCESS_HEADERS)
    assert response.json()["ordering_authenticated"] is False
    assert not access.session_valid(token, "ordering")
    assert admin.get("/protected/admin").status_code == 200


def test_password_rotation_renews_current_admin_and_revokes_other_sessions(tmp_path):
    app, access = access_app(tmp_path)
    current, other = TestClient(app), TestClient(app)
    initialize(current, access)
    login(other)
    old_cookie = current.cookies.get(ADMIN_COOKIE)
    failed = current.put("/api/access/admin/password", headers=ACCESS_HEADERS,
                         json={"current_password": "incorrect", "new_password": "new-admin-password"})
    assert failed.status_code == 400
    assert current.get("/protected/admin").status_code == 200
    response = current.put("/api/access/admin/password", headers=ACCESS_HEADERS,
                           json={"current_password": ADMIN_PASSWORD, "new_password": "new-admin-password"})
    assert response.status_code == 200
    assert response.json()["admin_authenticated"] is True
    assert current.cookies.get(ADMIN_COOKIE) != old_cookie
    assert current.get("/protected/admin").status_code == 200
    assert other.get("/protected/admin").status_code == 401
    assert login(other).status_code == 401
    assert login(other, password="new-admin-password").status_code == 200
    assert not access.session_valid(old_cookie, "admin")


def test_login_throttle_persists_and_bounds_expensive_password_work(tmp_path, monkeypatch):
    app, access = access_app(tmp_path)
    client = TestClient(app)
    initialize(client, access)
    calls = []
    monkeypatch.setattr(access_module, "_password_matches", lambda *args: calls.append(1) or False)
    for _ in range(access_module.LOGIN_ATTEMPTS):
        assert login(client, password="wrong-password").status_code == 401
    response = login(client, password="wrong-password")
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0
    assert len(calls) == access_module.LOGIN_ATTEMPTS
    restarted_app, _ = access_app(tmp_path)
    assert login(TestClient(restarted_app), password="wrong-password").status_code == 429
    assert len(calls) == access_module.LOGIN_ATTEMPTS


def test_session_expiry_and_local_recovery_do_not_restore_revoked_sessions(tmp_path, monkeypatch):
    app, access = access_app(tmp_path)
    client = TestClient(app)
    initialize(client, access)
    token = client.cookies.get(ADMIN_COOKIE)
    state = json.loads(access.path.read_text())
    for session in state["sessions"].values():
        session["expires_at"] = 1
    access._save(state)
    assert not access.session_valid(token, "admin")
    assert client.get("/protected/admin").status_code == 401
    access.reset_admin_password("recovered-admin-password")
    assert not access.setup_token_path.exists()
    assert login(client).status_code == 401
    assert login(client, password="recovered-admin-password").status_code == 200
    assert not access.path.with_name(access.path.name + ".bak").exists()


def test_local_ui_shares_hashed_expiring_role_sessions_and_logout(tmp_path):
    access = AccessControl(tmp_path)
    access.reset_admin_password(ADMIN_PASSWORD)
    token = access.authenticate_password("admin", ADMIN_PASSWORD, "legacy-local-admin")
    assert access.session_valid(token, "admin")
    assert not access.session_valid(token, "ordering")
    access.revoke_session(token, "ordering")
    assert access.session_valid(token, "admin")
    access.revoke_session(token, "admin")
    assert not access.session_valid(token, "admin")
    assert token not in access.path.read_text()


def test_concurrent_setup_has_one_winner(tmp_path):
    app, access = access_app(tmp_path)
    token = access.setup_token_path.read_text().strip()

    def attempt(number):
        return TestClient(app).post("/api/access/setup", headers=ACCESS_HEADERS,
                                    json={"setup_token": token, "password": f"administrator-{number}"}).status_code

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(attempt, range(2))) == [200, 409]
    assert len(json.loads(access.path.read_text())["sessions"]) == 1


def test_corrupt_configuration_never_falls_back_to_unprotected_defaults(tmp_path):
    _, access = access_app(tmp_path)
    access.path.write_text('{"ordering_password_required":false}')
    before = access.path.read_bytes()
    with pytest.raises(RuntimeError):
        AccessControl(tmp_path)
    assert access.path.read_bytes() == before


def test_ordering_session_quota_cannot_evict_admin_sessions(tmp_path, monkeypatch):
    app, access = access_app(tmp_path)
    admin, guest = TestClient(app), TestClient(app)
    initialize(admin, access)
    settings(admin, password=ORDERING_PASSWORD)
    admin_token = admin.cookies.get(ADMIN_COOKIE)
    monkeypatch.setattr(access_module, "MAX_SESSIONS_PER_ROLE", 2)
    assert login(guest, "ordering", ORDERING_PASSWORD).status_code == 200
    old_order_token = guest.cookies.get(ORDERING_COOKIE)
    assert login(guest, "ordering", ORDERING_PASSWORD).status_code == 200
    assert login(guest, "ordering", ORDERING_PASSWORD).status_code == 200
    assert not access.session_valid(old_order_token, "ordering")
    assert access.session_valid(admin_token, "admin")
    assert admin.get("/protected/admin").status_code == 200
    assert len(json.loads(access.path.read_text())["sessions"]) == 3


def test_invalid_session_expiry_or_cookie_fails_closed(tmp_path):
    from starlette.requests import Request

    app, access = access_app(tmp_path)
    client = TestClient(app)
    initialize(client, access)
    token = client.cookies.get(ADMIN_COOKIE)
    for expiry in (True, float("nan"), float("inf"), "forever"):
        state = json.loads(access.path.read_text())
        for session in state["sessions"].values():
            session["expires_at"] = expiry
        access._save(state)
        assert not access.session_valid(token, "admin")
        assert client.get("/protected/admin").status_code == 401
    assert not access.session_valid("\ud800" * 43, "admin")
    request = Request({"type": "http", "headers": [(b"cookie", (ADMIN_COOKIE + "=" + "é" * 43).encode("latin-1"))]})
    assert access._token(request, "admin") is None
