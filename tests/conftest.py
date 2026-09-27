"""Shared test helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from defeasible.config import Settings
from defeasible.api import create_app
from defeasible.language import Term
from defeasible.logging import RunLogger
from defeasible.service import ReasoningService
from defeasible.storage import EvidenceStore
from defeasible.engine import Engine
from fastapi.testclient import TestClient

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def store() -> EvidenceStore:
    s = EvidenceStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def service(store: EvidenceStore) -> ReasoningService:
    return ReasoningService(store, Engine(), RunLogger(":memory:"))


@pytest.fixture
def client(store: EvidenceStore) -> TestClient:
    logger = RunLogger(":memory:")
    app = create_app(Settings(db_path=":memory:", log_path=":memory:"),
                     store=store, logger=logger)
    with TestClient(app) as c:
        c._logger = logger  # type: ignore[attr-defined]
        yield c


def evidence(texts: list[str]) -> list[Term]:
    return [Term.parse(t) for t in texts]
