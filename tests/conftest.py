"""Shared fixtures: run identity, structured test logging, signal fixtures.

Every test receives a ``tlog`` logger that emits one JSON line per step to
both the console and ``test_logs/session-<run_id>.jsonl``.  Each record
carries the session run id, the test name, the input fixture identity, the
computation step, and the decision basis of the assertion — so a failure can
be traced back to the exact input and reasoning that produced it.
"""

from __future__ import annotations

import json
import platform
import uuid
from pathlib import Path

import numpy as np
import pytest
import scipy

LOG_DIR = Path(__file__).resolve().parent.parent / "test_logs"


class TestLogger:
    def __init__(self, run_id: str, test_name: str, fh) -> None:
        self.run_id = run_id
        self.test_name = test_name
        self._fh = fh

    def step(self, step: str, *, input_id: str = "", basis: str = "", **data) -> None:
        record = {
            "run_id": self.run_id,
            "test": self.test_name,
            "step": step,
            "input": input_id,
            "basis": basis,
            **{k: _jsonable(v) for k, v in data.items()},
        }
        line = json.dumps(record, ensure_ascii=False)
        self._fh.write(line + "\n")
        self._fh.flush()
        print(f"[{self.run_id}] {self.test_name} :: {step} :: {line}")


def _jsonable(value):
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


@pytest.fixture(scope="session")
def run_id() -> str:
    return uuid.uuid4().hex[:12]


@pytest.fixture(scope="session", autouse=True)
def session_banner(run_id):
    LOG_DIR.mkdir(exist_ok=True)
    banner = {
        "run_id": run_id,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pytest": pytest.__version__,
    }
    print(f"\n=== mfcc-backend test session {run_id} ===\n"
          + json.dumps(banner, indent=2))
    yield
    print(f"\n=== session {run_id} finished; logs in {LOG_DIR} ===")


@pytest.fixture()
def tlog(request, run_id):
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"session-{run_id}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        yield TestLogger(run_id, request.node.name, fh)


# -- synthetic signal fixtures (local, deterministic) ------------------------


@pytest.fixture()
def sine_440():
    sr = 16000
    t = np.arange(sr) / sr  # 1 s
    return np.sin(2.0 * np.pi * 440.0 * t), sr, "sine_440hz_16k_1s"


@pytest.fixture()
def white_noise():
    sr = 16000
    rng = np.random.default_rng(seed=20261003)
    return rng.standard_normal(sr) * 0.1, sr, "white_noise_seed20261003_16k_1s"


@pytest.fixture()
def silence():
    sr = 16000
    return np.zeros(sr), sr, "silence_16k_1s"


@pytest.fixture()
def short_input():
    sr = 16000
    n = 120  # < frame_length (400) at 16 kHz
    rng = np.random.default_rng(seed=7)
    return rng.standard_normal(n) * 0.05, sr, f"short_{n}samples_16k"
