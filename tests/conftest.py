import pytest

from config.settings import SolverSettings


@pytest.fixture(scope="session")
def settings() -> SolverSettings:
    return SolverSettings()
