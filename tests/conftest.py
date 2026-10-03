import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from limiter.config import LimiterConfig  # noqa: E402
from limiter.runlog import RunLogger, environment_fingerprint, new_run_id  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


@pytest.fixture(scope="session")
def run_logger():
    RESULTS_DIR.mkdir(exist_ok=True)
    run_id = new_run_id()
    logger = RunLogger(RESULTS_DIR / f"{run_id}.jsonl", run_id)
    logger.log("run_start", **environment_fingerprint())
    yield logger
    logger.log("run_end")
    logger.close()
    print(f"\n[runlog] {logger.path}")


@pytest.fixture
def tlog(request, run_logger):
    """Per-test structured logger: records inputs, metrics, thresholds, verdict."""

    def log(step: str, **fields):
        run_logger.log("test_step", test=request.node.nodeid, step=step, **fields)

    return log


@pytest.fixture(scope="session")
def cfg() -> LimiterConfig:
    return LimiterConfig()
