"""Pytest fixtures + run logging.

Every test run gets a run id and a log file under reports/ capturing:
  - versions (python / phe / fastapi / cryptography / pytest)
  - per-test progress and outcome
  - key test inputs/expectations logged by the tests themselves
Failures and skips are recorded, never silently dropped.
"""

from __future__ import annotations

import logging
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cryptography
import fastapi
import phe
import pytest

from app.config import Settings
from app.encoding import EncodingParams
from app.main import create_app
from app.service import AggregationService
from app.storage import Storage

RUN_ID = uuid.uuid4().hex[:12]
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
LOG_PATH: Path | None = None

# Small-but-real key for fast tests; production default remains 2048.
TEST_KEY_SIZE = 1024
TEST_PARAMS = EncodingParams(
    max_plaintext_abs=1_000,
    max_coefficient_abs=100,
    max_aggregate_abs=1_000_000,
)


def _make_run_logger() -> logging.Logger:
    global LOG_PATH
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    LOG_PATH = REPORTS_DIR / f"test_run_{stamp}_{RUN_ID}.log"
    logger = logging.getLogger("paillier.testrun")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


run_logger = _make_run_logger()


def pytest_sessionstart(session):
    run_logger.info(
        "RUN START run_id=%s python=%s pytest=%s phe=%s fastapi=%s cryptography=%s",
        RUN_ID,
        platform.python_version(),
        pytest.__version__,
        phe.__version__,
        fastapi.__version__,
        cryptography.__version__,
    )


def pytest_runtest_logstart(nodeid):
    run_logger.info("TEST START run_id=%s node=%s", RUN_ID, nodeid)


def pytest_runtest_logreport(report):
    if report.when == "call":
        run_logger.info(
            "TEST %s run_id=%s node=%s duration=%.3fs",
            report.outcome.upper(),
            RUN_ID,
            report.nodeid,
            report.duration,
        )
    elif report.when == "setup" and report.skipped:
        run_logger.info("TEST SKIPPED run_id=%s node=%s", RUN_ID, report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    run_logger.info(
        "RUN END run_id=%s exitstatus=%s passed=%s failed=%s skipped=%s",
        RUN_ID,
        exitstatus,
        session.testscollected - session.testsfailed,
        session.testsfailed,
        getattr(session, "testsskipped", "n/a"),
    )
    print(f"\n[test-run] run_id={RUN_ID} log={LOG_PATH}")


@pytest.fixture(scope="session")
def run_id() -> str:
    return RUN_ID


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        database_path=str(tmp_path / "test.db"),
        key_size=TEST_KEY_SIZE,
        accept_plaintext_fixtures=True,
        encoding=TEST_PARAMS,
    )


@pytest.fixture()
def storage(settings) -> Storage:
    store = Storage(settings.database_path)
    yield store
    store.close()


@pytest.fixture()
def service(settings, storage) -> AggregationService:
    return AggregationService(settings, storage)


@pytest.fixture()
def api_client(settings):
    from fastapi.testclient import TestClient

    app = create_app(settings)
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def batch(service, run_id):
    """An OPEN batch with the shared test key/params."""
    return service.create_batch(run_id, label="test-batch", params=TEST_PARAMS)
