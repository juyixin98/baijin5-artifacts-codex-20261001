"""Test-run logging: every test is tied to a run identity and its inputs.

A session-scoped JSONL log at ``logs/test_run_<run_id>.jsonl`` records
versions, per-test progress (setup/call/teardown outcomes), and — via the
``runlog`` fixture — judgment-basis notes emitted by individual tests
(e.g. "expected values are hand-computed literals"). A failing or erroring
test is logged with its outcome; nothing is rewritten to "passed".
"""

from __future__ import annotations

import json
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy
import pytest

RUN_ID = uuid.uuid4().hex[:12]
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_PATH = LOG_DIR / f"test_run_{RUN_ID}.jsonl"

_VERSIONS = {
    "python": platform.python_version(),
    "numpy": numpy.__version__,
    "pytest": pytest.__version__,
}


def _emit(record: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": RUN_ID,
        "ts": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def pytest_sessionstart(session):  # noqa: ARG001
    _emit({"event": "session_start", "versions": _VERSIONS})


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    _emit({"event": "session_finish", "exitstatus": int(exitstatus)})


def pytest_runtest_logreport(report):
    if report.when == "call":
        _emit(
            {
                "event": "test_outcome",
                "nodeid": report.nodeid,
                "outcome": report.outcome,
                "duration_s": round(report.duration, 6),
            }
        )


class RunLogger:
    """Per-test step logger; entries are correlated by run id + nodeid."""

    def __init__(self, nodeid: str):
        self.nodeid = nodeid

    def step(self, name: str, **fields) -> None:
        _emit({"event": "step", "nodeid": self.nodeid, "step": name, **fields})


@pytest.fixture
def runlog(request) -> RunLogger:
    logger = RunLogger(request.node.nodeid)
    logger.step("test_start")
    return logger
