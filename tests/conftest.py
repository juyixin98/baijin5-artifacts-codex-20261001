"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_SETTINGS, Settings
from app.main import create_app
from app.state.channels import ChannelStore


@pytest.fixture()
def settings() -> Settings:
    return DEFAULT_SETTINGS


@pytest.fixture()
def store(settings: Settings) -> ChannelStore:
    return ChannelStore(settings)


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
