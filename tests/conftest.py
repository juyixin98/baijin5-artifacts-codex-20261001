"""Pytest fixtures and reporting.

Each test runs against an isolated temporary SQLite DB and log directory. A
machine-readable report is written to reports/test_report.json so a run can be
audited after the fact, correlating test names with any run/validation ids and
the service version. Failures remain failures (no success coercion).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from app import __version__
from app.config import Settings
from app.storage.store import DigestStore

REPORTS_DIR = Path("reports")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    db_path = tmp_path / "test.db"
    log_dir = tmp_path / "logs"
    return Settings(
        db_path=str(db_path),
        log_dir=str(log_dir),
        log_level="DEBUG",
        log_to_file=True,
    )


@pytest.fixture
def store(settings: Settings) -> DigestStore:
    return DigestStore(settings.db_path)


def pytest_configure(config: pytest.Config) -> None:
    config._digest_report: dict[str, Any] = {
        "service_version": __version__,
        "started_at": time.time(),
        "results": [],
    }


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        entry = {
            "nodeid": item.nodeid,
            "outcome": report.outcome,
            "duration_ms": round(report.duration * 1000, 3),
        }
        if report.outcome == "failed":
            entry["longrepr"] = str(report.longrepr)
        item.session.config._digest_report["results"].append(entry)


def pytest_sessionfinish(session: pytest.Session, exitstatus) -> None:
    data = session.config._digest_report
    data["finished_at"] = time.time()
    data["elapsed_ms"] = round((data["finished_at"] - data["started_at"]) * 1000, 3)
    data["total"] = len(data["results"])
    data["passed"] = sum(1 for r in data["results"] if r["outcome"] == "passed")
    data["failed"] = sum(1 for r in data["results"] if r["outcome"] == "failed")
    data["exit_status"] = exitstatus
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / "test_report.json"
    report_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
