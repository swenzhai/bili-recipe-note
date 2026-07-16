from __future__ import annotations

import hashlib
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

        paired = client.post(
            "/api/v1/pair",
            json={"schema_version": 1, "pairing_token": credential.pairing_token, "device_name": "Android"},
        )
        assert paired.status_code == 200
        headers = {"Authorization": f"Bearer {paired.json()['access_token']}"}
        assert client.post("/api/v1/sync", json={"cursor": 0, "operations": []}).status_code == 401
        assert client.post("/api/v1/sync", headers=headers, json={"cursor": 0, "operations": []}).status_code == 200

        content = b"photo"
        digest = hashlib.sha256(content).hexdigest()
        uploaded = client.put(
            f"/api/v1/assets/{digest}", headers={**headers, "Content-Type": "image/jpeg"}, content=content
        )
        assert uploaded.status_code == 200
        downloaded = client.get(f"/api/v1/assets/{digest}", headers=headers)
        assert downloaded.status_code == 200
        assert downloaded.content == content
