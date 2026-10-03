"""Shared fixtures: an app instance backed by a throwaway SQLite database."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(db_path=str(tmp_path / "test-motifscan.db"))


@pytest.fixture()
def client(settings) -> TestClient:
    return TestClient(create_app(settings))
