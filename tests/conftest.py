from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Must be set before Streamlit imports PyArrow. The macOS/Python 3.14 mimalloc
# path can segfault from Streamlit's script thread during dataframe IPC writes.
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolate_data_environment(monkeypatch):
    # Explicit project_root fixtures must never be redirected into a user's
    # live data directory. Tests can still opt into their own temporary root.
    # setenv also registers restoration when the variable was originally absent.
    monkeypatch.setenv("BILI_RECIPE_DATA_DIR", "")


def authenticate_business_client(client, project_root=None, *, ordering=False):
    """Opt-in real authentication for business regressions, never a bypass."""
    from bili_recipe_notes.access_control import AccessControl, create_access_router

    app = client.app
    access = getattr(app.state, "access_control", None)
    if access is None:
        assert project_root is not None
        access = AccessControl(project_root)
        app.state.access_control = access
        app.include_router(create_access_router(access))
    headers = {"X-Access-Request": "1"}
    password = "synthetic-admin-password"
    if access.setup_token_path.exists():
        response = client.post("/api/access/setup", headers=headers, json={
            "setup_token": access.setup_token_path.read_text(encoding="utf-8").strip(),
            "password": password,
        })
    else:
        response = client.post("/api/access/admin/login", headers=headers, json={"password": password})
    assert response.status_code == 200, response.text
    if ordering:
        # Exercise the device protocol independently of the password gate by
        # configuring that choice as a real administrator, then logging out.
        response = client.put("/api/access/settings", headers=headers, json={
            "ordering_enabled": True, "ordering_password_required": False,
        })
        assert response.status_code == 200, response.text
        response = client.post("/api/access/admin/logout", headers=headers)
        assert response.status_code == 200, response.text
    else:
        client.headers.update({"X-Admin-Request": "1"})
    return client


@pytest.fixture
def authorize_client():
    """Explicit opt-in; unauthenticated tests retain production defaults."""
    return authenticate_business_client


def authenticate_streamlit_app(app, project_root=None):
    """Give an isolated component test a real, persisted administrator session."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    with TestClient(FastAPI()) as client:
        authenticate_business_client(client, project_root or Path.cwd())
        app.session_state["_legacy_admin_session"] = client.cookies.get("chef_admin_session")
    return app
