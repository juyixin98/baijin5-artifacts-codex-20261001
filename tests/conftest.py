"""Shared pytest fixtures.

Logging contract exercised here:
- at session start we log the exact package versions and a session run id;
- every test gets a unique, correlatable ``run_id`` (test name + uuid);
- a capturing handler records structured ``run_id``/``step`` fields so tests
  can assert that logs are attributable to a specific input run, and that
  failed/unknown states are never logged as success.
"""
from __future__ import annotations

import logging
import platform
import uuid
from typing import Any, Dict, List

import numpy
import pytest
import scipy

from app import __version__
from app.logging_setup import configure_logging

SESSION_RUN_ID = f"pytest-{uuid.uuid4().hex[:8]}"


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def data_for(self, run_id: str) -> List[Dict[str, Any]]:
        out = []
        for rec in self.records:
            data = getattr(rec, "data", {})
            if isinstance(data, dict) and data.get("run_id") == run_id:
                out.append({"level": rec.levelname, "message": rec.getMessage(),
                            "data": data})
        return out


@pytest.fixture(scope="session", autouse=True)
def _session_banner() -> None:
    logger = configure_logging()
    logger.info(
        "test session start | session=%s app=%s python=%s numpy=%s scipy=%s "
        "platform=%s",
        SESSION_RUN_ID, __version__, platform.python_version(),
        numpy.__version__, scipy.__version__, platform.platform(),
    )


@pytest.fixture
def caplog_handler() -> ListHandler:
    handler = ListHandler()
    logger = configure_logging()
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


@pytest.fixture
def run_id(request: pytest.FixtureRequest) -> str:
    """Deterministic, per-test correlation id."""
    rid = f"{SESSION_RUN_ID}-{request.node.name}-{uuid.uuid4().hex[:6]}"
    return rid
