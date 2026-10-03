import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(provenance_db_path=str(tmp_path / "provenance.db"))


@pytest.fixture()
def client(settings) -> TestClient:
    return TestClient(create_app(settings))
