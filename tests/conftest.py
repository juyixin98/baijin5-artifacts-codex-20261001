"""Local synthetic fixtures shared by the test suite."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.repository import Repository
from app.service import FimService

# Hand-computed corpus used for exact-value assertions (see README/test docs).
CORPUS_ABC = [
    {"tid": "t1", "items": ["a", "b"]},
    {"tid": "t2", "items": ["a", "b", "c"]},
    {"tid": "t3", "items": ["a", "c"]},
    {"tid": "t4", "items": ["b", "c"]},
]

# Corpus exercising empty transactions and duplicated transaction content.
CORPUS_EMPTY_DUP = [
    {"tid": "e1", "items": []},
    {"tid": "e2", "items": []},          # identical content, separate identity
    {"tid": "x1", "items": ["x"]},
    {"tid": "x2", "items": ["x", "x"]},  # repeated item inside ONE transaction
]


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_path=str(tmp_path / "test_fim.db"),
        default_budget_nodes=1000,
        max_budget_nodes=1_000_000,
        log_level="WARNING",
    )


@pytest.fixture
def service(settings):
    return FimService(Repository(settings.database_path), settings)
