"""Shared pytest fixtures and path setup."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.main import create_app  # noqa: E402
from app.store import RunStore  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures"


@pytest.fixture()
def store(tmp_path):
    s = RunStore(tmp_path / "test_runs.db")
    yield s
    s.close()


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(
        db_path=str(tmp_path / "api_runs.db"),
        log_file=str(tmp_path / "api.log"),
    )
    return TestClient(app)


@pytest.fixture()
def load_fixture():
    def _load(name: str) -> dict:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _load
