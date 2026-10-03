"""Shared pytest fixtures: isolated workspace/log dirs and reusable specs."""
from __future__ import annotations

import pytest

from app.config import Settings


@pytest.fixture()
def settings(tmp_path):
    return Settings(workspace_dir=tmp_path / "ws", log_dir=tmp_path / "logs")


@pytest.fixture()
def noise_spec_dict():
    return {
        "image": {"kind": "noise", "shape": [70, 54], "seed": 5},
        "kernel": {"kind": "gaussian", "k": 5, "sigma": 1.2},
        "boundary": "mirror",
        "cval": 0.0,
        "tile": [16, 16],
    }
