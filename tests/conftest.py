"""Pytest fixtures: each test gets an isolated SQLite database."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NUSSINOV_DB_PATH", str(tmp_path / "test_lineage.db"))
    monkeypatch.setenv("NUSSINOV_MAX_SEQUENCE_LENGTH", "64")

    from nussinov_backend.config import get_settings

    get_settings.cache_clear()
    from nussinov_backend.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    app.state.db.close()
    get_settings.cache_clear()
