"""Composition-root smoke test: build a real service from settings/env."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.api.server import create_app
from app.core.crypto import CryptographyBackend
from app.core.sender import encode_message
from app.db.store import RELEASED

pytestmark = pytest.mark.integration


def test_composition_root_builds_working_app(tmp_path, monkeypatch):
    keys_path = tmp_path / "keys.json"
    from app.core.keyring import generate_key_bundle, save_key_file
    bundle = generate_key_bundle("env-kid")
    save_key_file(keys_path, bundle)

    monkeypatch.setenv("SSEA_KEY_FILE", str(keys_path))
    monkeypatch.setenv("SSEA_DB_PATH", str(tmp_path / "s.sqlite3"))
    monkeypatch.setenv("SSEA_STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setenv("SSEA_RELEASE_DIR", str(tmp_path / "released"))
    monkeypatch.setenv("SSEA_AEAD_BACKEND", "cryptography")
    monkeypatch.setenv("SSEA_LOG_LEVEL", "WARNING")

    # Reset lru_cached singletons so the env is picked up.
    deps.get_settings.cache_clear()
    deps.get_bundle.cache_clear()
    deps.get_store.cache_clear()
    deps.get_service.cache_clear()

    service = deps.get_service()
    assert service._bundle.key_id == "env-kid"

    plaintext = b"composition root end-to-end" * 3
    sealed = encode_message(bundle, CryptographyBackend(),
                            "comp-1", plaintext, 17)
    app = create_app()  # uses get_service() dependency
    client = TestClient(app)
    for f in sealed.frames:
        r = client.post("/v1/segments",
                        json={"frame_b64": base64.b64encode(f).decode()})
        assert r.status_code == 200, r.text
    assert client.post("/v1/streams/comp-1/finalize").json()["complete"]
    got = client.get("/v1/streams/comp-1/result").content
    assert got == plaintext
    assert service.status("comp-1")["status"] == RELEASED

    audit = client.get("/v1/audit", params={"message_id": "comp-1"}).json()
    assert any(e["category"] == "stream_released" for e in audit)
    deps.get_store().close()
    deps.get_settings.cache_clear()
    deps.get_bundle.cache_clear()
    deps.get_store.cache_clear()
    deps.get_service.cache_clear()
