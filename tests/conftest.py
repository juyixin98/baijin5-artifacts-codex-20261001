"""Shared reusable fixtures for the keytree test-suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keytree.identity import KeyIdentity
from keytree.service import KeyTreeService
from keytree.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def root_hex() -> str:
    return (FIXTURES / "test_root.hex").read_text().strip()


@pytest.fixture(scope="session")
def rfc5869() -> list[dict]:
    data = json.loads((FIXTURES / "rfc5869.json").read_text())
    return data["cases"]


@pytest.fixture(scope="session")
def golden_tree() -> list[dict]:
    data = json.loads((FIXTURES / "golden_tree_vectors.json").read_text())
    return data["vectors"]


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "state.db")
    yield s
    s.close()


@pytest.fixture()
def log_file(tmp_path):
    return tmp_path / "run.log"


@pytest.fixture()
def service(store, log_file, root_hex):
    stream = open(log_file, "a", encoding="utf-8")
    svc = KeyTreeService(store, log_stream=stream, root_hex=root_hex)
    yield svc
    stream.close()


@pytest.fixture()
def identity() -> KeyIdentity:
    return KeyIdentity(tenant="acme", purpose="encryption", version=1, context=b"")


def read_log(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
