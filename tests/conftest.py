"""Shared fixtures: a Mapper and a FastAPI TestClient built from the real
local fixture files (fixtures/ at the repo root)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from txmap.main import app  # noqa: F401  (ensures main wiring imports cleanly)
from txmap.mapping import Mapper
from txmap.store import (
    Store,
    load_fixture_contigs,
    load_fixture_transcripts,
)
from txmap.service import create_app

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="session")
def mapper() -> Mapper:
    transcripts = {t.tx_id: t for t in load_fixture_transcripts(FIXTURE_DIR / "transcripts.json")}
    contigs = load_fixture_contigs(FIXTURE_DIR / "reference.fa")
    return Mapper(transcripts, contigs)


@pytest.fixture()
def client(tmp_path):
    store = Store(tmp_path / "test.db")
    store.load_transcripts(load_fixture_transcripts(FIXTURE_DIR / "transcripts.json"))
    contigs = load_fixture_contigs(FIXTURE_DIR / "reference.fa")
    m = Mapper(store.get_transcripts(), contigs)
    with TestClient(create_app(m, store)) as c:
        yield c
    store.close()
