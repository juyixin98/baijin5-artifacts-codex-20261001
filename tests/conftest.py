import numpy as np
import pytest

from dtw_service.constraints import PathConstraints
from dtw_service.settings import load_settings


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture()
def default_constraints(settings):
    return PathConstraints(
        window=settings.dtw.sakoe_chiba_window or 10,
        max_run=settings.dtw.max_consecutive_run,
    )


@pytest.fixture()
def rng():
    return np.random.default_rng(20261003)
