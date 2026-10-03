"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from txmap.config import Settings  # noqa: E402
from txmap.mapping import CoordinateMapper  # noqa: E402
from txmap.parsing import parse_fixture  # noqa: E402
from txmap.service import MappingService  # noqa: E402
from txmap.storage import Repository  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "transcripts.json"


@pytest.fixture(scope="session")
def reference():
    chromosomes, transcripts = parse_fixture(FIXTURE)
    return chromosomes, transcripts


@pytest.fixture(scope="session")
def patterns():
    import json

    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {c["name"]: c["pattern"] for c in raw["chromosomes"]}


@pytest.fixture
def t1(reference):
    return reference[1]["T1_PLUS"]


@pytest.fixture
def t2(reference):
    return reference[1]["T2_MINUS"]


@pytest.fixture
def t3(reference):
    return reference[1]["T3_ADJACENT"]


@pytest.fixture
def m1(t1):
    return CoordinateMapper(t1)


@pytest.fixture
def m2(t2):
    return CoordinateMapper(t2)


@pytest.fixture
def m3(t3):
    return CoordinateMapper(t3)


@pytest.fixture
def repo(tmp_path, reference, patterns):
    r = Repository(tmp_path / "test.sqlite3")
    r.replace_reference(reference[0], patterns, reference[1])
    yield r
    r.close()


@pytest.fixture
def service(repo):
    return MappingService(repo)


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from txmap.api.app import create_app

    settings = Settings(
        fixture_path=FIXTURE,
        db_path=tmp_path / "api.sqlite3",
        log_level="WARNING",
        db_echo=False,
    )
    app = create_app(settings=settings)
    with TestClient(app) as c:
        yield c
