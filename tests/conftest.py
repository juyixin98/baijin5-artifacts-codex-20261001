from __future__ import annotations

import pytest

from sae.config import Settings
from sae.service import ChunkedCryptoService

# Test-only master key (matches scripts/make_fixtures.py).
TEST_MASTER_KEY = bytes(range(32))


@pytest.fixture()
def settings(tmp_path):
    return Settings(master_key=TEST_MASTER_KEY, db_path=str(tmp_path / "sae-test.db"))


@pytest.fixture()
def service(settings):
    svc = ChunkedCryptoService(settings)
    yield svc
    svc.close()
