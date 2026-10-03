from __future__ import annotations

import json
from pathlib import Path

import pytest

from kds.service import DerivationTreeService
from kds.state import StateStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def root_key(fixtures_dir: Path) -> bytes:
    return bytes.fromhex(fixtures_dir.joinpath("root_key.hex").read_text().strip())


@pytest.fixture(scope="session")
def tenant_fixtures(fixtures_dir: Path) -> dict:
    return json.loads(fixtures_dir.joinpath("tenants.json").read_text())


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    s = StateStore(str(tmp_path / "kds.sqlite3"))
    yield s
    s.close()


@pytest.fixture()
def service(root_key: bytes, store: StateStore) -> DerivationTreeService:
    return DerivationTreeService(root_key, store)
