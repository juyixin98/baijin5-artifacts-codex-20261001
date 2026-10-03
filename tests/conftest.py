"""Test-session infrastructure: run identity, versions, JSONL test log.

Every test run writes tests/logs/test_run_<run_id>.jsonl. Each record
carries the run id, the test node id, the input identity (kind + sha1 of
the sample bytes), the computation step, the verdict and its rationale —
so a log line can always be traced back to the exact input and decision.
"""

from __future__ import annotations

import hashlib
import json
import platform
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

LOG_DIR = Path(__file__).parent / "logs"
RUN_ID = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
LOG_PATH = LOG_DIR / f"test_run_{RUN_ID}.jsonl"


def _versions() -> dict:
    import scipy
    import fastapi

    import mfcc_backend

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "fastapi": fastapi.__version__,
        "mfcc_backend": mfcc_backend.__version__,
        "pytest": pytest.__version__,
    }


def _append(record: dict) -> None:
    record.setdefault("run_id", RUN_ID)
    record["ts"] = time.time()
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def signal_id(samples) -> str:
    """Stable identity for an input signal: length + sha1 of its bytes."""
    arr = np.asarray(samples, dtype=np.float64)
    return f"n={arr.size}:sha1={hashlib.sha1(arr.tobytes()).hexdigest()[:16]}"


def pytest_sessionstart(session):  # noqa: ARG001
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _append({"event": "session_start", "versions": _versions()})
    print(f"\n[test-run] id={RUN_ID}")
    print(f"[test-run] versions={json.dumps(_versions())}")
    print(f"[test-run] log={LOG_PATH}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        _append(
            {
                "event": "test_outcome",
                "test": item.nodeid,
                "outcome": report.outcome,
                "duration_s": round(report.duration, 6),
            }
        )


@pytest.fixture
def run_log(request):
    """Function-scoped step logger bound to the current test node."""

    def log(step: str, **fields):
        _append({"event": "step", "test": request.node.nodeid, "step": step, **fields})

    return log
