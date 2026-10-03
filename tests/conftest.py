import numpy as np
import pytest

from app.config import KernelConfig
from app.fixtures import build_fixtures


@pytest.fixture(scope="session")
def cfg() -> KernelConfig:
    return KernelConfig()


@pytest.fixture(scope="session")
def fixtures():
    """In-memory fixtures (float64, no PNG quantisation)."""
    return {fx.name: fx for fx in build_fixtures()}


@pytest.fixture()
def rng():
    return np.random.default_rng(42)
