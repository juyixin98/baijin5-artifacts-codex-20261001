from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from limiter import LimiterConfig

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
DEFAULT_CONFIG_PATH = ROOT / "config" / "limiter.default.json"


@pytest.fixture(scope="session")
def default_config() -> LimiterConfig:
    data = json.loads(DEFAULT_CONFIG_PATH.read_text())
    return LimiterConfig.from_dict(data)


def load_fixture(name: str) -> tuple[np.ndarray, dict]:
    pcm = np.load(FIXTURES / f"{name}.npz")["pcm"]
    meta = json.loads((FIXTURES / f"{name}.json").read_text())
    return pcm, meta


@pytest.fixture(scope="session")
def short_impulse():
    return load_fixture("short_impulse")


@pytest.fixture(scope="session")
def stereo_imbalance():
    return load_fixture("stereo_imbalance")


@pytest.fixture(scope="session")
def sustained_peaks():
    return load_fixture("sustained_peaks")


@pytest.fixture(scope="session")
def block_boundary_burst():
    return load_fixture("block_boundary_burst")


@pytest.fixture(scope="session")
def gain_recovery():
    return load_fixture("gain_recovery")


@pytest.fixture(scope="session")
def true_peak_rich():
    return load_fixture("true_peak_rich")
