"""Shared pytest fixtures."""
from __future__ import annotations

import numpy as np
import pytest

from autodiff.config import Config, set_config
from autodiff.diagnostics import Diagnostics


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(20260927)


@pytest.fixture
def diag() -> Diagnostics:
    # Diagnostics collected in-memory only (no stderr noise in tests).
    return Diagnostics(request_id="test-req", config=Config(log_diagnostics=False))


@pytest.fixture(autouse=True)
def _restore_config():
    """Each test starts (and ends) from the default configuration."""
    set_config(Config())
    yield
    set_config(Config())
