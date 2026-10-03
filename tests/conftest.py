"""Shared pytest fixtures: a small WSOLA config for hand-computed cases."""
from __future__ import annotations

import pytest

from wsola_backend.config import WsolaConfig


@pytest.fixture
def tiny_cfg() -> WsolaConfig:
    """L=8, Hs=4, D=2: small enough to compute expected offsets by hand."""
    return WsolaConfig(window_length=8, synthesis_hop=4, search_radius=2)


@pytest.fixture
def small_cfg() -> WsolaConfig:
    return WsolaConfig(window_length=64, synthesis_hop=32, search_radius=16)
