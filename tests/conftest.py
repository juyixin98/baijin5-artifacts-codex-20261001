"""Pytest configuration: isolated storage/logging before app import.

Environment variables are set *before* any ``app`` module is imported so that
:class:`app.config.Settings` picks up test-only paths.
"""
from __future__ import annotations

import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="rct-tests-")
os.environ.setdefault("RCT_DB_PATH", os.path.join(_TEST_DIR, "test.sqlite3"))
os.environ.setdefault("RCT_LOG_PATH", os.path.join(_TEST_DIR, "test.log"))
os.environ.setdefault("RCT_EXACT_BUDGET_FLIPS", "100000")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import app  # noqa: E402
from app.storage import reset_database_for_tests  # noqa: E402


@pytest.fixture()
def client():
    reset_database_for_tests(os.path.join(_TEST_DIR, "test.sqlite3"))
    with TestClient(app) as test_client:
        yield test_client
