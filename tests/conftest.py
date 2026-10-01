"""Test configuration: run-identity correlated logging and shared fixtures.

Every analysis triggered from the tests carries an ``input_label`` built from
the test node id and the DGP name, and the pipeline stamps each log line with
that label, a run_id and dependency versions. A session-level log handler
also mirrors progress + the assertion basis into ``test_run.log`` so a run
can be audited after the fact. Failures are never reported as success: tests
assert on the explicit ``status`` / ``error_code`` categories.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pytest

from app import logging_setup
from app.config import Settings
from app.contract import DataPoint, RDRequest
from app.dependencies import configure, reset

LOG_PATH = Path("results/test_run.log")


class _RunIdentityFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            payload = json.loads(record.getMessage())
        except (ValueError, TypeError):
            return super().format(record)
        payload["test_node"] = getattr(record, "test_node", "?")
        return json.dumps(payload, ensure_ascii=False)


@pytest.fixture(scope="session", autouse=True)
def session_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging_setup.configure_logging("INFO")
    logger = logging.getLogger("rd")
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setFormatter(_RunIdentityFormatter())
    logger.addHandler(fh)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(_RunIdentityFormatter())
    logger.addHandler(stream)
    logger.info(
        json.dumps(
            {
                "event": "test_session_start",
                "step": "session",
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
        )
    )
    yield
    logger.info(json.dumps({"event": "test_session_end", "step": "session"}))


@pytest.fixture(autouse=True)
def _tag_node(request: object) -> None:
    """Stamp every log line emitted during a test with the test node id."""
    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        record = old_factory(*args, **kwargs)
        record.test_node = getattr(request, "node", None) and request.node.nodeid
        return record

    logging.setLogRecordFactory(factory)
    yield
    logging.setLogRecordFactory(old_factory)


@pytest.fixture
def settings() -> Settings:
    s = Settings(
        db_path=":memory:",
        min_obs_per_side=10,
        bootstrap_reps=399,
        default_alpha=0.05,
        log_level="INFO",
    )
    configure(s)
    return s


@pytest.fixture
def make_request():
    """Factory: builds a labelled request tying logs to the calling test."""

    def _make(
        x: np.ndarray,
        y: np.ndarray,
        *,
        label: str,
        nodeid: str,
        **overrides,
    ) -> RDRequest:
        overrides.setdefault("input_label", f"{nodeid}::{label}")
        overrides.setdefault("bootstrap_seed", 1234)
        return RDRequest(
            data=[DataPoint(x=float(a), y=float(b)) for a, b in zip(x, y)],
            **overrides,
        )

    return _make
