"""Shared pytest fixtures: fixture images, settings, log capture."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.config import Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> np.ndarray:
    with Image.open(FIXTURE_DIR / name) as img:
        return np.asarray(img.convert("L")).copy()


def fixture_b64(name: str) -> str:
    return base64.b64encode((FIXTURE_DIR / name).read_bytes()).decode("ascii")


@pytest.fixture()
def settings() -> Settings:
    return Settings().validate()


@pytest.fixture()
def step_image() -> np.ndarray:
    return load_fixture("step_5x6.png")


@pytest.fixture()
def spike_image() -> np.ndarray:
    return load_fixture("spike_4x3.png")


class LogCapture(logging.Handler):
    """Collects (event, record) pairs from the seamcarve logger."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def events(self, name: str) -> list[logging.LogRecord]:
        return [r for r in self.records if getattr(r, "event", None) == name]


@pytest.fixture()
def captured_logs():
    logger = logging.getLogger("seamcarve")
    handler = LogCapture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
