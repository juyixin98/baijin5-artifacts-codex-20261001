"""Synthetic DGP contract tests."""
from __future__ import annotations

import numpy as np

from app.dgp import DGPs, sharp_jump


def test_dgp_seeded_reproducibility():
    a = sharp_jump(seed=42)
    b = sharp_jump(seed=42)
    c = sharp_jump(seed=43)
    np.testing.assert_array_equal(a.x, b.x)
    np.testing.assert_array_equal(a.y, b.y)
    assert not np.array_equal(a.x, c.x)


def test_sharp_jump_has_actual_level_shift():
    d = sharp_jump(n=4000, noise=0.0, seed=5)
    # With zero noise, means very close to the cutoff differ by ~ tau even
    # though slopes are the same.
    near_l = d.y[(d.x < 0) & (d.x > -0.03)].mean()
    near_r = d.y[(d.x > 0) & (d.x < 0.03)].mean()
    assert abs((near_r - near_l) - d.true_tau) < 0.2


def test_no_jump_is_continuous_at_cutoff():
    d = DGPs["no_jump"](n=8000, noise=0.0, seed=6)
    near_l = d.y[(d.x < 0) & (d.x > -0.02)].mean()
    near_r = d.y[(d.x > 0) & (d.x < 0.02)].mean()
    assert abs(near_r - near_l) < 0.25


def test_sparse_boundary_has_empty_inner_gap():
    d = DGPs["sparse_boundary"](n=800, inner_gap=0.3, seed=7)
    assert np.all(np.abs(d.x) >= 0.3 - 1e-9)
    assert d.true_tau == 4.0


def test_density_discontinuity_shares():
    d = DGPs["density_discontinuity"](n=1000, right_share=0.75, seed=8)
    share_right = float(np.mean(d.x > d.cutoff))
    assert abs(share_right - 0.75) < 0.03
