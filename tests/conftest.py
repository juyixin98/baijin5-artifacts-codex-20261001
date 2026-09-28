"""Shared pytest fixtures."""

from __future__ import annotations

import numpy as np
import pytest

from tensorcraft.api import create_app
from tensorcraft.config import load_config
from tensorcraft.tensor import Tensor


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_2x3() -> Tensor:
    return Tensor.from_nested([[1, 2, 3], [4, 5, 6]], "int64")


@pytest.fixture
def rng():
    return np.random.default_rng(2026)
