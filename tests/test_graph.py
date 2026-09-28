"""Layer-2 tests: aggregation, clipping mode separation, optimizer kernels.

Numeric oracles are hand-computed or produced by the independent dense
reference, never by the kernels under test.
"""

from __future__ import annotations

import numpy as np
import pytest

from sparse_embeddings.config import ClippingConfig
from sparse_embeddings.graph import (
    aggregate_duplicate_indices,
    clip_gradients,
    momentum_sgd_step,
    sgd_step,
)


def test_aggregate_sums_duplicate_indices_before_use():
    idx = np.array([3, 3, 1, 3])
    val = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0, 0.0],
            [0.0, 4.0, 0.0, 0.0],
            [10.0, 0.0, 0.0, 0.0],
        ]
    )
    touched, agg, counts = aggregate_duplicate_indices(
        idx, val, scale=4.0, dim=4
    )
    np.testing.assert_array_equal(touched, [1, 3])
    np.testing.assert_array_equal(counts, [1, 3])
    # Row 3: (1+2+10)/4 = 3.25 ; row 1: 4/4 = 1.0
    np.testing.assert_allclose(agg[0], [0.0, 1.0, 0.0, 0.0])
    np.testing.assert_allclose(agg[1], [3.25, 0.0, 0.0, 0.0])


def test_aggregate_order_of_duplicates_does_not_change_result():
    rng = np.random.default_rng(0)
    idx = rng.integers(0, 5, size=50)
    val = rng.normal(size=(50, 3))
    t1, a1, _ = aggregate_duplicate_indices(idx, val, scale=50.0, dim=3)
    perm = rng.permutation(50)
    t2, a2, _ = aggregate_duplicate_indices(idx[perm], val[perm], scale=50.0, dim=3)
    np.testing.assert_array_equal(t1, t2)
    np.testing.assert_allclose(a1, a2, rtol=1e-12, atol=1e-12)


def test_aggregate_empty_returns_empty():
    t, a, c = aggregate_duplicate_indices(
        np.empty(0, np.int64), np.empty((0, 4)), scale=0.0, dim=4
    )
    assert t.shape == (0,) and a.shape == (0, 4) and c.shape == (0,)


def test_global_clipping_uses_single_uniform_scale():
    g = np.array([[3.0, 4.0], [0.0, 0.0]])  # global norm = 5
    res = clip_gradients(g, ClippingConfig("global", 2.5))
    assert res.clipped is True
    np.testing.assert_allclose(res.scales, [0.5, 0.5])
    np.testing.assert_allclose(res.gradients, [[1.5, 2.0], [0.0, 0.0]])
    np.testing.assert_allclose(res.pre_norm, 5.0)


def test_global_clipping_leaves_small_norm_untouched():
    g = np.array([[1.0, 0.0]])
    res = clip_gradients(g, ClippingConfig("global", 5.0))
    assert res.clipped is False
    np.testing.assert_allclose(res.scales, [1.0])
    np.testing.assert_array_equal(res.gradients, g)


def test_row_clipping_scales_each_row_independently():
    # Row 0 norm 3 -> clipped to 2 (factor 2/3); row 1 norm 1 -> untouched.
    g = np.array([[3.0, 0.0], [0.0, 1.0]])
    res = clip_gradients(g, ClippingConfig("row", 2.0))
    assert res.clipped is True
    np.testing.assert_allclose(res.scales, [2.0 / 3.0, 1.0])
    np.testing.assert_allclose(res.gradients[0], [2.0, 0.0])
    np.testing.assert_allclose(res.gradients[1], [0.0, 1.0])


def test_row_and_global_modes_give_differing_results_and_do_not_mix():
    # Global norm exceeds threshold but each individual row is under it.
    g = np.array([[3.0, 0.0], [4.0, 0.0]])  # rows 3,4 ; global 5
    glob = clip_gradients(g, ClippingConfig("global", 4.5))
    row = clip_gradients(g, ClippingConfig("row", 4.5))
    assert glob.clipped is True and row.clipped is False
    # Row mode leaves rows exactly as input.
    np.testing.assert_array_equal(row.gradients, g)
    # Global scales both rows by the same factor 4.5/5.
    np.testing.assert_allclose(glob.scales, [0.9, 0.9])
    np.testing.assert_allclose(glob.gradients, [[2.7, 0.0], [3.6, 0.0]])


def test_zero_rows_survive_row_clipping_with_scale_one():
    g = np.array([[0.0, 0.0], [10.0, 0.0]])
    res = clip_gradients(g, ClippingConfig("row", 1.0))
    np.testing.assert_allclose(res.scales[0], 1.0)
    np.testing.assert_array_equal(res.gradients[0], [0.0, 0.0])
    np.testing.assert_allclose(res.gradients[1], [1.0, 0.0])


def test_momentum_step_touches_only_selected_rows():
    V, D = 4, 2
    w = np.arange(V * D, dtype=np.float64).reshape(V, D)
    v = np.ones((V, D))
    g = np.array([[1.0, 1.0]])
    w2, v2 = momentum_sgd_step(
        w, v, np.array([2]), g, learning_rate=0.1, momentum=0.9
    )
    # Touched row 2: v2 = 0.9*1 + 1 = 1.9 ; w2 = w - 0.1*1.9
    np.testing.assert_allclose(v2[2], [1.9, 1.9])
    np.testing.assert_allclose(
        w2[2], w[2] - 0.1 * 1.9
    )
    # Untouched rows: buffers and weights identical.
    for i in [0, 1, 3]:
        np.testing.assert_array_equal(v2[i], v[i])
        np.testing.assert_array_equal(w2[i], w[i])


def test_momentum_zero_gradient_decays_buffer_and_moves_weight():
    # The zero-gradient touched-row rule: v <- mu*v (+0), w still changes.
    w = np.array([[1.0, 1.0]])
    v = np.array([[2.0, 2.0]])
    w2, v2 = momentum_sgd_step(
        w, v, np.array([0]), np.zeros((1, 2)), learning_rate=0.5, momentum=0.5
    )
    np.testing.assert_allclose(v2[0], [1.0, 1.0])          # decayed, not zeroed
    np.testing.assert_allclose(w2[0], [0.5, 0.5])          # 1 - 0.5*1.0


def test_sgd_zero_gradient_is_weight_noop_but_still_selected():
    w = np.array([[7.0, 7.0], [8.0, 8.0]])
    w2 = sgd_step(w, np.array([0]), np.zeros((1, 2)), learning_rate=0.1)
    np.testing.assert_array_equal(w2[0], w[0])
    np.testing.assert_array_equal(w2[1], w[1])
