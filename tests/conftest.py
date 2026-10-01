"""Pytest fixtures: isolated temp DB and budget-controlled settings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Default (exact-capable) settings with an isolated temp database."""
    return Settings(
        db_path=tmp_path / "test.db",
        exact_budget=65_536,
        crossing_budget=2_000_000,
        mc_draws=20_000,
        mc_seed=20260927,
        inversion_grid=2_048,
        inversion_refine=35,
    )


@pytest.fixture
def small_budget_settings(tmp_path: Path) -> Settings:
    """Settings that force Monte Carlo / grid paths on small datasets."""
    return Settings(
        db_path=tmp_path / "approx.db",
        exact_budget=16,
        crossing_budget=100,
        mc_draws=40_000,
        mc_seed=42,
        inversion_grid=3_000,
        inversion_refine=38,
    )
