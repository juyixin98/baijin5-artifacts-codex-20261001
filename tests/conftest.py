"""Shared pytest fixtures: service client and structured-log capture."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.logging_setup import get_logger
from app.main import create_app


@pytest.fixture()
def settings() -> Settings:
    # Small pixel cap so the 413 path is testable without megabyte payloads.
    return Settings(max_image_pixels=4096, default_chunk_size=4, log_level="DEBUG")


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), raise_server_exceptions=False)


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def log_capture():
    """Capture structured records emitted on the ``watershed`` logger."""
    logger = get_logger()
    handler = _ListHandler()
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


def events(handler: _ListHandler, event: str) -> list[dict]:
    """All structured field dicts for one event name."""
    return [
        getattr(record, "fields", {})
        for record in handler.records
        if record.getMessage() == event
    ]
