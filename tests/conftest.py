import json

import pytest

from sensitive_layer.config import Settings
from sensitive_layer.service import SensitiveService

# Fixed test-only key material (never used outside tests).
ENC_KEY = bytes.fromhex("053c2743457ff13a16d3ace29e3cc3e501d825dc48364d1a5dde0d4e74e8bff2")
INDEX_KEY_V1 = bytes.fromhex("520e455d90a99a8b4e35ca4f42184cf5c8ad5630400be761330fce015251a0ed")

PURPOSES = {
    "lookup:email": "email",
    "alias:email": "email",
    "lookup:phone": "phone",
    "lookup:name": "name",
}


def make_settings(tmp_path, index_bits=32):
    return Settings(
        database_path=str(tmp_path / "test.db"),
        audit_log_path=str(tmp_path / "audit.log"),
        allow_plaintext_read=True,
        index_bits=index_bits,
        norm_version="nfkc-casefold-v1",
        purposes=dict(PURPOSES),
        seed_keys={"enc": {1: ENC_KEY}, "index": {1: INDEX_KEY_V1}},
        active_enc_version=1,
        active_index_version=1,
    )


@pytest.fixture
def make_service(tmp_path):
    def _make(index_bits=32):
        return SensitiveService.from_settings(make_settings(tmp_path, index_bits))
    return _make


@pytest.fixture
def service(make_service):
    return make_service()


@pytest.fixture
def client(tmp_path):
    """FastAPI TestClient backed by a temp config file."""
    from fastapi.testclient import TestClient

    from sensitive_layer.api.main import create_app

    settings = make_settings(tmp_path)
    cfg = {
        "database_path": settings.database_path,
        "audit_log_path": settings.audit_log_path,
        "allow_plaintext_read": True,
        "index_bits": settings.index_bits,
        "norm_version": settings.norm_version,
        "purposes": PURPOSES,
        "keys": {"enc": {"1": ENC_KEY.hex()}, "index": {"1": INDEX_KEY_V1.hex()}},
        "active_enc_version": 1,
        "active_index_version": 1,
    }
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps(cfg))
    return TestClient(create_app(str(cfg_path)))
