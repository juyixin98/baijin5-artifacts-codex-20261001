import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fixtures.generate_fixtures import generate_all  # noqa: E402
from app import fixtures_io  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def fixtures_ready():
    """Regenerate fixtures once per test run so results are reproducible."""
    generate_all()


@pytest.fixture(scope="session")
def manifest():
    return fixtures_io.load_manifest()


@pytest.fixture(scope="session")
def pair():
    def load(fid: str):
        return fixtures_io.load_fixture_pair(fid)
    return load
