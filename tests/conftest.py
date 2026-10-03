"""Pytest fixtures: deterministic seeds and a replayable run log.

Every test session gets a ``run_id``; tests can emit structured records
(seeds, tolerances, intermediate norms, assertion rationale) via the
``runlog`` fixture. Records land in tests/logs/run_<run_id>.jsonl so a
failure can be reproduced from the logged seed and inputs.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

LOG_DIR = Path(__file__).parent / "logs"


@pytest.fixture(scope="session")
def run_id() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def rng() -> np.random.Generator:
    # Fixed seed: failures are reproducible by re-running the same test.
    return np.random.default_rng(20261003)


@pytest.fixture()
def runlog(request, run_id):
    records: list[dict] = []

    def log(step: str, **data):
        records.append({"step": step, **data})

    yield log

    LOG_DIR.mkdir(exist_ok=True)
    outcome = getattr(
        request.node, "rep_call", None
    )
    record = {
        "run_id": run_id,
        "test": request.node.nodeid,
        "outcome": getattr(outcome, "outcome", "unknown"),
        "records": records,
    }
    with (LOG_DIR / f"run_{run_id}.jsonl").open("a") as fh:
        fh.write(json.dumps(record, default=_json_default) + "\n")


def _json_default(obj):
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return str(obj)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
