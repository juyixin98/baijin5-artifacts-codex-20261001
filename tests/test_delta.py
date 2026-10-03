"""Delta stage: explicit boundary-extension rule and analytic cases."""

import numpy as np
import pytest

from mfcc_backend import InputContractError, compute_delta, compute_delta_delta

from reference_impl import ref_delta


def test_delta_of_constant_is_zero(tlog):
    feat = np.full((10, 4), 3.5)
    got = compute_delta(feat, 2)
    tlog.step("delta_constant", basis="edge replication -> symmetric diffs cancel",
              max_abs=float(np.max(np.abs(got))))
    np.testing.assert_allclose(got, 0.0, atol=1e-15)


def test_delta_of_ramp_equals_slope_in_interior(tlog):
    # feat[t] = 2.5 * t (per coefficient); delta of a linear ramp is the
    # slope wherever the window doesn't hit a boundary.
    t = np.arange(12, dtype=np.float64)
    feat = np.outer(t, [2.5, -1.0, 0.25])
    got = compute_delta(feat, 2)
    tlog.step("delta_ramp", basis="interior delta == slope; boundary rows differ "
              "due to edge replication (documented rule)",
              interior_row=got[5].tolist())
    np.testing.assert_allclose(
        got[2:-2], np.tile([2.5, -1.0, 0.25], (8, 1)), atol=1e-12
    )


def test_delta_boundary_uses_edge_replication_exactly(tlog):
    # Hand-computed: feat = [[0],[1],[2]] (one coeff), width 1, denom = 2.
    # t=0: (c1 - c0_replicated)/2 = (1-0)/2 = 0.5
    # t=1: (c2 - c0)/2 = 1.0
    # t=2: (c2_replicated - c1)/2 = (2-1)/2 = 0.5
    feat = np.array([[0.0], [1.0], [2.0]])
    got = compute_delta(feat, 1)
    tlog.step("delta_boundary", basis="hand-computed edge replication values",
              got=got.ravel().tolist(), want=[0.5, 1.0, 0.5])
    np.testing.assert_allclose(got.ravel(), [0.5, 1.0, 0.5])


def test_delta_matches_reference_on_random_matrix(tlog):
    rng = np.random.default_rng(42)
    feat = rng.standard_normal((17, 6))
    got = compute_delta(feat, 3)
    want = np.array(ref_delta(feat.tolist(), 3))
    tlog.step("delta_vs_reference", basis="literal-loop reference, width=3",
              max_abs_err=float(np.max(np.abs(got - want))))
    np.testing.assert_allclose(got, want, atol=1e-12)


def test_delta_delta_of_ramp_is_zero_in_interior(tlog):
    t = np.arange(16, dtype=np.float64)
    feat = np.outer(t, [1.0, -2.0])
    got = compute_delta_delta(feat, 2)
    tlog.step("delta2_ramp", basis="second difference of linear signal is 0 "
              "away from boundaries",
              interior_max=float(np.max(np.abs(got[4:-4]))))
    np.testing.assert_allclose(got[4:-4], 0.0, atol=1e-12)


def test_delta_empty_and_invalid_inputs(tlog):
    empty = compute_delta(np.empty((0, 13)), 2)
    tlog.step("delta_empty", basis="0 frames -> (0, 13), not an error",
              shape=empty.shape)
    assert empty.shape == (0, 13)
    with pytest.raises(InputContractError):
        compute_delta(np.zeros((4, 4)), 0)
    with pytest.raises(InputContractError):
        compute_delta(np.zeros(5), 2)  # not 2-D
    tlog.step("delta_invalid", basis="width<1 and non-2D raise InputContractError")
