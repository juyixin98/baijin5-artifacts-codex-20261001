"""Tests for structured, request-correlated logging."""

from __future__ import annotations

import json
import logging

import pytest

from stft_backend.config import SERVICE_NAME, SERVICE_VERSION
from stft_backend.logging_setup import (
    JsonFormatter,
    bind_request_id,
    configure_logging,
    request_id_var,
)

pytestmark = pytest.mark.unit


def test_formatter_emits_json_with_request_id_and_version() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name=SERVICE_NAME,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    bind_request_id("req-log-1")
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "req-log-1"
    assert payload["service"] == SERVICE_NAME
    assert payload["version"] == SERVICE_VERSION
    assert payload["level"] == "INFO"
    assert "ts" in payload


def test_extra_fields_are_included() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name=SERVICE_NAME,
        level=logging.WARNING,
        pathname=__file__,
        lineno=2,
        msg="failed",
        args=(),
        exc_info=None,
    )
    record.error_code = "NOLA_VIOLATION"
    record.stage = "nola_check"
    payload = json.loads(formatter.format(record))
    assert payload["error_code"] == "NOLA_VIOLATION"
    assert payload["stage"] == "nola_check"


def test_configure_returns_same_logger_and_binds_contextvar() -> None:
    logger = configure_logging("DEBUG")
    assert logger.name == SERVICE_NAME
    bind_request_id("req-log-2")
    assert request_id_var.get() == "req-log-2"
