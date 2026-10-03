"""Shared pytest fixtures: load the local synthetic sample data."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def expected() -> dict:
    return json.loads((FIXTURES / "expected.json").read_text())


@pytest.fixture(scope="session")
def ar_signal() -> dict:
    data = np.load(FIXTURES / "ar_signal.npz")
    return {"samples": data["samples"], "a_true": data["a_true"], "fs": int(data["fs"])}


@pytest.fixture(scope="session")
def sine_signal() -> dict:
    data = np.load(FIXTURES / "sine.npz")
    return {"samples": data["samples"], "freq": float(data["freq"]), "fs": int(data["fs"])}


@pytest.fixture(scope="session")
def silence() -> np.ndarray:
    return np.load(FIXTURES / "silence.npz")["samples"]


@pytest.fixture(scope="session")
def noise() -> np.ndarray:
    return np.load(FIXTURES / "noise.npz")["samples"]
