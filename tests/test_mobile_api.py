from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from bili_recipe_notes.mobile_api import create_app


def test_health_pair_auth_and_asset_roundtrip(tmp_path: Path) -> None:
    app = create_app(tmp_path, start_background_indexer=False)
    store = app.state.store
    credential = store.issue_pairing_credential("http://192.168.1.2:8765")

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["server_id"] == store.server_id
        assert health.json()["indexing"]["last_success"]
        assert health.json()["indexing"]["status"] == "ok"

        paired = client.post(
            "/api/v1/pair",
            json={"schema_version": 1, "pairing_token": credential.pairing_token, "device_name": "Android"},
        )
        assert paired.status_code == 200
        headers = {"Authorization": f"Bearer {paired.json()['access_token']}"}
        assert client.get("/api/v1/index-status").status_code == 401
        assert client.get("/api/v1/index-status", headers=headers).json()["status"] == "ok"
        assert client.post("/api/v1/sync", json={"cursor": 0, "operations": []}).status_code == 401
        assert client.post("/api/v1/sync", headers=headers, json={"cursor": 0, "operations": []}).status_code == 200
        assert client.get("/api/v1/meal-history").status_code == 401
        history = client.get("/api/v1/meal-history", headers=headers)
        assert history.status_code == 200
        assert history.json() == {"meals": []}

        compressed = client.post(
            "/api/v1/sync",
            headers={**headers, "Accept-Encoding": "gzip"},
            json={"cursor": 0, "operations": [], "capabilities": ["recipe", "practice_log"]},
        )
        assert compressed.status_code == 200

        content = b"photo"
        digest = hashlib.sha256(content).hexdigest()
        uploaded = client.put(
            f"/api/v1/assets/{digest}", headers={**headers, "Content-Type": "image/jpeg"}, content=content
        )
        assert uploaded.status_code == 200
        downloaded = client.get(f"/api/v1/assets/{digest}", headers=headers)
        assert downloaded.status_code == 200
        assert downloaded.content == content


def test_static_client_is_served_after_api_routes(tmp_path: Path) -> None:
    static = tmp_path / "web"
    static.mkdir()
    (static / "index.html").write_text("<h1>shared meal</h1>", encoding="utf-8")
    app = create_app(tmp_path, static_dir=static, start_background_indexer=False)

    with TestClient(app) as client:
        assert client.get("/").text == "<h1>shared meal</h1>"
        assert client.get("/").headers["cache-control"] == "no-store"
        assert client.get("/nested/route").text == "<h1>shared meal</h1>"
        assert client.get("/api/v1/health").headers["content-type"].startswith("application/json")


def test_self_join_can_be_locked_without_revoking_existing_devices(tmp_path: Path) -> None:
    app = create_app(tmp_path, start_background_indexer=False)
    store = app.state.store

    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["self_join_enabled"] is True
        joined = client.post("/api/v1/join", json={"device_name": "客厅平板"})
        assert joined.status_code == 200
        headers = {"Authorization": f"Bearer {joined.json()['access_token']}"}

        store.set_self_join_enabled(False)

        assert client.post("/api/v1/join", json={"device_name": "陌生设备"}).status_code == 403
        assert client.post("/api/v1/sync", headers=headers, json={"cursor": 0, "operations": []}).status_code == 200


def test_api_starts_degraded_and_health_remains_available_when_index_fails(tmp_path: Path, monkeypatch) -> None:
    app = create_app(tmp_path, start_background_indexer=False)

    def failing_index():
        raise OSError("unavailable disk")

    monkeypatch.setattr(app.state.store, "index_recipes", failing_index)
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        state = response.json()["indexing"]
        assert state["status"] == "degraded"
        assert state["last_success"] is None
        assert state["consecutive_failures"] == 1
        assert "unavailable disk" in state["last_error"]


def test_importing_api_factory_does_not_create_a_database(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-B", "-c", "from bili_recipe_notes.mobile_api import create_app"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "data" / "state").exists()
