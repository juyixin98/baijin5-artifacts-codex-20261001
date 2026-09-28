"""Shared pytest fixtures.

Reference answers here are built from :mod:`aipw_backend.reference` (true
DGP nuisance functions), never from the kernel under test.
"""

from __future__ import annotations

import numpy as np
import pytest

from aipw_backend.config import AipwConfig, FoldConfig, ModelConfig
from aipw_backend.dgp import DGPParams, generate_sample
from aipw_backend.reference import reference_estimate


@pytest.fixture
def folds_cfg() -> FoldConfig:
    return FoldConfig(n_splits=5, seed=7301, stratified=True)


@pytest.fixture
def default_config(folds_cfg) -> AipwConfig:
    return AipwConfig(
        folds=folds_cfg,
        treatment_model=ModelConfig("logistic_ridge", penalty=1e-6),
        outcome_model=ModelConfig("ols_ridge", penalty=1e-6),
    )


@pytest.fixture
def dgp_params() -> DGPParams:
    return DGPParams()


@pytest.fixture
def large_sample():
    """n=16000 iid sample, tau known = 2."""
    return generate_sample(16_000, seed=424242, tau=2.0)


@pytest.fixture
def oracle(large_sample):
    return reference_estimate(
        large_sample.x, large_sample.a, large_sample.y, large_sample.params
    )


def scenario_config(name: str, folds: FoldConfig) -> AipwConfig:
    g = {
        "both_correct": ModelConfig("logistic_ridge"),
        "propensity_only": ModelConfig("logistic_ridge"),
        "outcome_only": ModelConfig("wrong_constant"),
        "both_wrong": ModelConfig("wrong_constant"),
    }[name]
    m = {
        "both_correct": ModelConfig("ols_ridge"),
        "propensity_only": ModelConfig("wrong_constant"),
        "outcome_only": ModelConfig("ols_ridge"),
        "both_wrong": ModelConfig("wrong_constant"),
    }[name]
    return AipwConfig(folds=folds, treatment_model=g, outcome_model=m)
