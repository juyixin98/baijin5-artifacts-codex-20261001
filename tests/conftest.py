"""Shared pytest fixtures.

Each test session writes into a temporary SQLite database and log directory
pointed at by a generated YAML config, so tests never touch real data and
every log line can be tied to the run id under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.core.logging_setup import _configured  # noqa: F401
import app.core.logging_setup as log_setup


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    cfg = {
        "service": {"name": "cuped-backend-test", "host": "127.0.0.1", "port": 8000},
        "storage": {"sqlite_path": str(tmp_path / "test.db")},
        "estimation": {
            "hc1_correction": True,
            "z_alpha": 1.959963984540054,
        },
        "synthetic": {"seed": 20260927},
        "logging": {"dir": str(tmp_path / "logs"), "level": "DEBUG"},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


@pytest.fixture()
def client(config_path: Path, tmp_path: Path):
    app = create_app(db_path=str(tmp_path / "test.db"), config_path=str(config_path))
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def log_file(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "cuped.log"


@pytest.fixture(autouse=True)
def _reset_logging():
    # Pytest runs in one process; the module-level singleton must be reset so
    # each test's configure_logging call re-points handlers at its tmp dir.
    yield
    if log_setup._configured:
        root = __import__("logging").getLogger()
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)
        log_setup._configured = False
