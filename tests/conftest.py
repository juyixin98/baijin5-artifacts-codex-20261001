"""Shared fixtures: deterministic DGPs for all four misspecification scenarios.

Reference answers (true ATE = 0.5 on every scenario) are properties of the DGP,
not numbers produced by the package under test. Independent numerical answers
come from ``tests/oracle.py`` (scipy L-BFGS-B + lstsq + explicit loops).
"""
from __future__ import annotations

import numpy as np
import pytest

from aipw.contract import Config
from aipw.crossfit import make_stratified_folds
from aipw.simulation import KNOWN_TAU, make_dataset, make_clustered_dataset


@pytest.fixture
def config() -> Config:
    return Config.default()


@pytest.fixture
def known_tau() -> float:
    return KNOWN_TAU


@pytest.fixture(params=["both_correct", "ps_only", "outcome_only", "both_wrong"])
def scenario(request):
    return make_dataset(seed=1234, n=4000, scenario=request.param)


@pytest.fixture
def both_correct():
    return make_dataset(seed=1234, n=4000, scenario="both_correct")


@pytest.fixture
def ps_only():
    return make_dataset(seed=1234, n=4000, scenario="ps_only")


@pytest.fixture
def outcome_only():
    return make_dataset(seed=1234, n=4000, scenario="outcome_only")


@pytest.fixture
def both_wrong():
    return make_dataset(seed=1234, n=4000, scenario="both_wrong")


@pytest.fixture
def clustered():
    return make_clustered_dataset(seed=42, n_clusters=80, members_per_cluster=10)


@pytest.fixture
def split_5(scenario, config):
    """Deterministic 5-fold split shared between package and oracle."""
    return make_stratified_folds(
        scenario.dataset, 5, np.random.default_rng(2024))
