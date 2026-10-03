"""Shared test fixtures and run-identity logging.

Every test logs its identity, the component versions, and the *basis* of
its judgements (which fixture, which expected values) so the test log can
be audited back to inputs -- not just "passed".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "reference"))

from msa_backend.config import load_config  # noqa: E402
from msa_backend.logging_utils import get_logger, log_judgement  # noqa: E402
from msa_backend.pipeline import component_versions  # noqa: E402
from msa_backend.provenance.db import ProvenanceStore  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "data" / "fixtures"
REFERENCE_DIR = REPO_ROOT / "tests" / "reference"

logger = get_logger("tests")


@pytest.fixture(scope="session", autouse=True)
def log_test_session_identity():
    versions = component_versions()
    logger.info(
        "test-session start versions=%s fixtures_dir=%s",
        versions, FIXTURES_DIR,
    )
    yield
    logger.info("test-session end")


@pytest.fixture()
def config(tmp_path):
    """Real config, but provenance redirected to a per-test tmp database."""
    cfg = load_config()
    return type(cfg)(
        name=cfg.name,
        database_path=tmp_path / "provenance.sqlite3",
        log_level=cfg.log_level,
        algorithm=cfg.algorithm,
    )


@pytest.fixture()
def store(config):
    s = ProvenanceStore(config.database_path)
    yield s
    s.close()


def read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def load_expected(name: str) -> dict:
    return json.loads((REFERENCE_DIR / f"expected_{name}.json").read_text(encoding="utf-8"))


def judgement(run_identity: str, step: str, basis: str) -> None:
    log_judgement(logger, run_identity, step, basis)
