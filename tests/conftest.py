"""Shared pytest fixtures: run logging + fixture loading.

Every test run writes structured JSON lines to ``artifacts/test-run.log``
correlating each test with its inputs (fixture sha256), component
versions and the decision basis asserted. The log is a reviewable
artefact of the run, not just console noise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seamcarve.runlog import RunLogger, component_versions, new_run_id  # noqa: E402

FIXTURE_DIR = ROOT / "fixtures" / "data"
ARTIFACTS = ROOT / "artifacts"


@pytest.fixture(scope="session")
def run_logger() -> RunLogger:
    ARTIFACTS.mkdir(exist_ok=True)
    log = RunLogger(run_id=f"pytest-{new_run_id()}", log_path=ARTIFACTS / "test-run.log")
    log.emit("test_session_start", step="init", versions=component_versions())
    yield log
    log.emit("test_session_end", step="done", records=len(log.records))


@pytest.fixture(autouse=True)
def _log_test_outcome(request, run_logger):
    run_logger.emit("test_start", step="test", test=request.node.nodeid)
    yield
    outcome = getattr(request.node, "rep_call", None)
    run_logger.emit(
        "test_end",
        step="test",
        test=request.node.nodeid,
        outcome=outcome.outcome if outcome else "unknown",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)


def load_fixture(name: str) -> tuple[np.ndarray, np.ndarray | None, dict]:
    """Load a generated fixture: (image, mask-or-None, descriptor)."""
    descriptor = json.loads((FIXTURE_DIR / f"{name}.json").read_text())
    image = np.asarray(Image.open(FIXTURE_DIR / descriptor["png"]))
    if image.ndim == 2:
        image = image.astype(np.uint8)
    mask = None
    if "mask" in descriptor:
        mask = np.asarray(json.loads((FIXTURE_DIR / descriptor["mask"]).read_text()), dtype=bool)
    return image, mask, descriptor


@pytest.fixture()
def fixture_loader():
    return load_fixture
