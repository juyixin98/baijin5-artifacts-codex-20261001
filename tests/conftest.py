import pytest

from er_backend.config import Settings
from er_backend.index.store import Store
from er_backend.service import ResolutionService

from .fixtures import TOKEN_MAP


@pytest.fixture()
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    yield s
    s.close()


@pytest.fixture()
def service(store, settings) -> ResolutionService:
    return ResolutionService(store, settings, TOKEN_MAP)
