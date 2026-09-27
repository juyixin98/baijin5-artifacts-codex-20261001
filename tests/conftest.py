"""Pytest configuration: ensure the repository root is importable and
provide shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cfim.config import Settings  # noqa: E402
from cfim.service import create_app  # noqa: E402
from cfim.store import Store  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        log_level="DEBUG",
        default_budget=50,
        max_transactions=10_000,
        max_items_per_transaction=256,
        max_item_length=128,
        max_corpus_name_length=200,
        max_advance_budget=100_000,
    )


@pytest.fixture
def store(settings: Settings) -> Store:
    s = Store(settings.db_path)
    yield s
    s.close()


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings=settings, store=Store(settings.db_path))
    with TestClient(app) as c:
        yield c
    app.state.store.close()
