from __future__ import annotations

import pytest

from app.config import AppConfig


@pytest.fixture()
def small_config(tmp_path) -> AppConfig:
    """Config with tight resource limits so exhaustion paths are cheap to hit."""
    return AppConfig(
        max_samples=4096,
        max_order=64,
        max_delay=256,
        max_matrix_cells=4096 * 64,
        default_regularization=1e-6,
        log_dir=str(tmp_path / "logs"),
    )
