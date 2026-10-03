"""Shared pytest fixtures and path setup.

The package is importable without installation: ``src`` is prepended to
``sys.path`` here so the suite runs from a clean checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tileconv.config import Settings  # noqa: E402
from tileconv.storage import ImageStore  # noqa: E402


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    s = Settings(workspace=tmp_path / "workspace", default_tile_shape=(16, 16))
    s.ensure_dirs()
    return s


@pytest.fixture()
def store(settings: Settings) -> ImageStore:
    return ImageStore(settings.workspace)
