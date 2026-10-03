"""Delta / delta-delta: exact hand-computed boundary values plus reference
cross-checks. The edge-replication rule is asserted coefficient-by-
coefficient, not just "runs without error"."""

import numpy as np

from mfcc_backend.dsp import compute_delta

import reference_impl as ref


def test_constant_features_have_zero_delta():
    feats = np.full((7, 13), 3.5)
    np.testing.assert_array_equal(compute_delta(feats, 2), np.zeros((7, 13)))


def test_ramp_delta_exact_boundary_values(run_log):
    # c[t] = t (single coefficient): interior slope must be exactly 1,
    # edges exactly 0.5 / 0.8 under edge replication with width N=2.
    feats = np.arange(10, dtype=np.float64)[:, None]
    d = compute_delta(feats, 2)[:, 0]
    expected = np.array([0.5, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.8, 0.5])
    np.testing.assert_allclose(d, expected, rtol=0, atol=1e-15)
    run_log("delta_ramp_boundaries", expected=expected.tolist(), actual=d.tolist(),
            verdict="pass",
            rationale="edge replication: d[0]=0.5, d[1]=0.8, interior=1.0 for unit ramp")


def test_delta2_of_ramp_is_zero_in_interior(run_log):
    feats = np.arange(12, dtype=np.float64)[:, None]
    d2 = compute_delta(compute_delta(feats, 2), 2)[:, 0]
    # delta of a ramp is exactly 1 only for t in [2, T-3]; delta2 needs two
    # more frames of margin, so the exact-zero region is t in [4, T-5].
    np.testing.assert_allclose(d2[4:-4], 0.0, atol=1e-15)
    assert d2[0] != 0.0  # boundary region is genuinely non-zero
    run_log("delta2_ramp_interior", interior=d2[4:-4].tolist(), verdict="pass",
            rationale="second derivative of a linear ramp vanishes for t in [4, T-5]")


def test_single_frame_delta_is_zero():
    # T=1: every neighbour clamps to the only frame -> delta must be 0
    feats = np.array([[1.0, -2.0, 0.5]])
    np.testing.assert_array_equal(compute_delta(feats, 2), np.zeros((1, 3)))


def test_empty_input_returns_empty():
    out = compute_delta(np.empty((0, 4)), 2)
    assert out.shape == (0, 4)


def test_matches_reference_on_random_matrix(run_log):
    rng = np.random.default_rng(99)
    feats = rng.standard_normal((37, 13))
    for width in (1, 2, 3):
        np.testing.assert_allclose(compute_delta(feats, width), ref.delta(feats, width),
                                   rtol=1e-12, atol=1e-12)
    run_log("delta_vs_reference", shape=[37, 13], widths=[1, 2, 3], verdict="pass",
            rationale="loop-based reference agrees for widths 1..3")
