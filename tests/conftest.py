"""Shared pytest fixtures and per-run test logging.

Every pytest run writes a log file under ``test_logs/`` named with a run id;
fixtures log the rng seed and key intermediate values so a failing case can
be replayed exactly.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pytest

from graphcut.config import Settings
from graphcut.contracts import Seed, build_spec, validate_pairwise

TEST_LOG_DIR = Path("test_logs")


@pytest.fixture(scope="session", autouse=True)
def test_run_logging():
    TEST_LOG_DIR.mkdir(exist_ok=True)
    run_id = time.strftime("run-%Y%m%dT%H%M%S")
    path = TEST_LOG_DIR / f"{run_id}.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    log = logging.getLogger("graphcut.tests")
    log.info("test_run.started run_id=%s log_file=%s", run_id, path)
    yield path
    log.info("test_run.finished run_id=%s log_file=%s", run_id, path)
    root.removeHandler(handler)


@pytest.fixture
def test_log():
    return logging.getLogger("graphcut.tests")


@pytest.fixture
def spec_factory():
    """Build a validated SegmentationSpec with sensible defaults."""

    def make(*, height, width, unary0, unary1, pairwise=None, seeds=(),
             settings=None):
        pw = pairwise if pairwise is not None else validate_pairwise(0.0, 1.0, 1.0, 0.0)
        return build_spec(
            height=height, width=width,
            unary0=np.asarray(unary0, dtype=np.float64),
            unary1=np.asarray(unary1, dtype=np.float64),
            pairwise=pw,
            seeds=[Seed(*s) if isinstance(s, tuple) else s for s in seeds],
            settings=settings or Settings(),
        )

    return make


@pytest.fixture
def seeded_rng(test_log, request):
    """Deterministic rng per test; the seed is logged for replay."""
    seed = abs(hash(request.node.nodeid)) % (2**31)
    test_log.info("rng.seed test=%s seed=%d", request.node.nodeid, seed)
    return np.random.default_rng(seed)
