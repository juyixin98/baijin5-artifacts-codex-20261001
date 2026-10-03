"""Shared fixtures: isolated log dir, app under test, HTTP client."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(log_dir=str(tmp_path / "logs"))


@pytest.fixture()
def client(settings) -> TestClient:
    os.environ["LMS_LOG_DIR"] = settings.log_dir
    return TestClient(create_app(settings))
