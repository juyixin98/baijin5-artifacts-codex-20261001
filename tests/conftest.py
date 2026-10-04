"""Pytest configuration: isolate DB/logs into a temp directory before app import.

Environment variables are set *before* any ``app`` import so the process-wide
settings point at throwaway local fixtures.
"""

import os
import tempfile

_TMP_ROOT = tempfile.mkdtemp(prefix="digest-tests-")
os.environ.setdefault("DIGEST_DB_PATH", os.path.join(_TMP_ROOT, "test.db"))
os.environ.setdefault("DIGEST_LOG_DIR", os.path.join(_TMP_ROOT, "logs"))
os.environ.setdefault("DIGEST_LOG_LEVEL", "DEBUG")

import dataclasses  # noqa: E402

import pytest  # noqa: E402

from app.config import Settings as _Settings  # noqa: E402


@pytest.fixture()
def isolated_settings(tmp_path) -> _Settings:
    """Fresh settings with an isolated SQLite DB per test."""
    base = _Settings.from_env()
    return dataclasses.replace(
        base,
        db_path=str(tmp_path / "isolated.db"),
        log_dir=str(tmp_path / "logs"),
    )


@pytest.fixture()
def service(isolated_settings):
    from app.services.digest_service import DigestService

    return DigestService(isolated_settings)
