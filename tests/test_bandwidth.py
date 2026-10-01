"""Bandwidth selector tests."""
from __future__ import annotations

import numpy as np
import pytest

from app.core import bandwidth
from app.dgp import sharp_jump


def test_rot_positive_finite_and_within_support():
    d = sharp_jump(n=800)
    hl, hr = bandwidth.rule_of_thumb(d.x, d.cutoff)
    assert hl > 0 and hr > 0
    assert np.isfinite(hl) and np.isfinite(hr)
    assert hl <= (d.cutoff - d.x.min())
    assert hr <= (d.x.max() - d.cutoff)


def test_rot_deterministic_and_reproducible():
    d = sharp_jump(seed=101)
    a = bandwidth.rule_of_thumb(d.x, d.cutoff)
    b = bandwidth.rule_of_thumb(d.x, d.cutoff)
    assert a == b


def test_rot_shrinks_at_n_minus_one_fifth_rate():
    rng = np.random.default_rng(3)
    hs = []
    for n in (500, 4000):
        x = rng.uniform(-1, 1, n)
        hl, _ = bandwidth.rule_of_thumb(x, 0.0)
        hs.append(hl)
    # h(4000)/h(500) ~ (500/4000)^(1/5) = 0.574 for the same spread
    ratio = hs[1] / hs[0]
    assert 0.45 < ratio < 0.70


def test_ik_returns_finite_shared_bandwidth():
    d = sharp_jump(n=2000, tau=8.0, curvature=1.0)
    choice = bandwidth.plugin_bandwidth(d.x, d.y, d.cutoff, "triangular")
    assert choice.method == "ik"
    assert np.isfinite(choice.h_left) and np.isfinite(choice.h_right)
    assert choice.h_left == pytest.approx(choice.h_right)
    assert choice.h_left > 0


def test_ik_records_curvature_and_density_details():
    d = sharp_jump(n=2000)
    choice = bandwidth.plugin_bandwidth(d.x, d.y, d.cutoff)
    for key in ("m2_left", "m2_right", "sigma2", "density_cutoff"):
        assert key in choice.details


def test_ik_falls_back_to_rot_when_curvature_vanishes():
    # Exactly linear conditional mean with no noise -> second derivative ~ 0
    # -> the MSE plugin is undefined, must fall back to ROT and record why.
    rng = np.random.default_rng(9)
    x = rng.uniform(-1, 1, 4000)
    y = 2.0 * x
    choice = bandwidth.plugin_bandwidth(x, y, 0.0)
    assert choice.fell_back is True
    assert choice.fallback_reason
    assert np.isfinite(choice.h_left)


def test_select_bandwidth_rejects_unknown_method():
    d = sharp_jump()
    with pytest.raises(ValueError, match="unknown bandwidth"):
        bandwidth.select_bandwidth(d.x, d.y, 0.0, "magic-cv", "triangular")
