"""Tests for the deterministic synthetic data generator."""
from __future__ import annotations

import math

import pytest

from app.synth import SyntheticConfig, generate


def test_generator_is_deterministic_by_seed():
    a = generate(SyntheticConfig(seed=5))
    b = generate(SyntheticConfig(seed=5))
    assert a["data"]["y"] == b["data"]["y"]
    c = generate(SyntheticConfig(seed=6))
    assert c["data"]["y"] != a["data"]["y"]


def test_ground_truth_structure():
    payload = generate(SyntheticConfig(n=100, tau=1.5, beta=2.0, seed=1),
                       add_leakage=False, add_constant=False)
    assert payload["ground_truth"]["tau"] == 1.5
    assert len(payload["data"]["y"]) == 100
    assert set(payload["covariates"]) == {"pre_x", "unrelated"}
    # treatment is binary
    assert set(payload["data"]["treatment"]) <= {0, 1}


def test_optional_fields_present_by_default():
    payload = generate(SyntheticConfig(n=50, seed=2))
    assert "post_spend" in payload["data"]
    assert payload["pre_treatment_covariates"]["post_spend"] is False
    assert all(v == 7.0 for v in payload["data"]["constant"])
    assert "constant" in payload["covariates"]


def test_missing_injection_uses_nulls():
    payload = generate(SyntheticConfig(n=200, seed=3),
                       add_leakage=False, add_constant=False,
                       add_missing=True, missing_fraction=0.10)
    col = payload["data"]["pre_x"]
    n_null = sum(v is None for v in col)
    assert n_null == 20
    assert all(v is None or isinstance(v, (int, float)) for v in col)


def test_known_effect_recovered_from_generator_output_at_scale():
    # Large sample: structural slope beta=4, sigma_eps=1 -> CUPED should pin
    # tau tightly; guards the generator against silent DGP breakage.
    from app.core.contracts import Settings
    from app.core.service import run_analysis
    # sigma_eps=1 irreducible noise: SE ~ sqrt(4/n); n=12000 -> ~0.0183.
    payload = generate(SyntheticConfig(n=12000, tau=2.0, beta=4.0, seed=123))
    result = run_analysis(payload, Settings())
    assert abs(result.cuped.estimate - 2.0) < 0.03
    assert result.cuped.se < 0.02
    assert result.cuped.extra["adjusted_var_control"] == pytest.approx(1.0, rel=0.1)
    # leakage/constant fields excluded, not used
    assert "post_spend" in result.dropped_covariates
    assert math.isfinite(result.cuped.estimate)
    assert math.isfinite(result.lin.estimate)
