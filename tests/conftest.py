from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pyramid_service.config import Settings
from pyramid_service.service import create_app
from pyramid_service.tilestore import TileStore


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        data_root=tmp_path / "data",
        tile_size=4,
        max_levels=8,
        max_image_pixels=1_000_000,
        max_region_pixels=4096,
        max_json_pixels=4096,
        log_file=str(tmp_path / "logs" / "service.jsonl"),
    )


@pytest.fixture()
def store(settings):
    return TileStore(settings.data_root)


@pytest.fixture()
def client(settings):
    return TestClient(create_app(settings))
