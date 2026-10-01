"""Shared fixtures.

Each API test gets a brand-new app with its own in-memory store injected via
the documented FastAPI dependency override; overrides are cleared afterwards.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.api import app, get_store  # noqa: E402
from app.storage import RunStore  # noqa: E402


@pytest.fixture
def store() -> RunStore:
    s = RunStore(":memory:")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client(store: RunStore) -> TestClient:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
