"""Shared fixtures: reproducible run logging for every test.

Each test that requests ``run_log`` gets a logger writing one JSON line per
record to ``test_logs/run-<run_id>.jsonl``. The run id, RNG seeds, key
intermediate states (e.g. max error, pole radii, versions) and the verdict
rationale are recorded so a failure can be replayed offline.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest

LOG_DIR = Path(__file__).resolve().parent.parent / "test_logs"


class RunLogger:
    def __init__(self, run_id: str, test_name: str) -> None:
        self.run_id = run_id
        self.test_name = test_name
        LOG_DIR.mkdir(exist_ok=True)
        self._path = LOG_DIR / f"run-{run_id}.jsonl"

    def log(self, event: str, **fields: object) -> None:
        record = {
            "run_id": self.run_id,
            "test": self.test_name,
            "event": event,
            "ts": time.time(),
            **fields,
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


@pytest.fixture()
def run_log(request: pytest.FixtureRequest) -> RunLogger:
    logger = RunLogger(uuid.uuid4().hex[:12], request.node.name)
    logger.log("test_start")
    yield logger
    logger.log("test_end", outcome="passed")
