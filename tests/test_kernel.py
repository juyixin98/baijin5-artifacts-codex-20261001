"""Numerical kernel tests.

Three independent oracles are used:
1. hand-computed impulse responses (pin anchor semantics exactly),
2. a naive pure-Python loop written for this test file,
3. scipy.ndimage with native boundary modes (via kernel.direct_reference).

The engine under test is the shift-and-add correlate + index-mapped padding.
"""

from __future__ import annotations

import numpy as np
import pytest

from tileconv.contract import BoundaryMode, KernelSpec
from tileconv.kernel import (
    correlate_valid,
    direct_reference,
    filter_full,
    filter_window,
)


def naive_filter(img, weights, anchor, mode, cval=0.0):
    """Naive per-pixel oracle (convolution form):
    out[i,j] = sum_{u,v} f(i-u+ar, j-v+ac) * K[u,v]
    where f resolves out-of-range samples per the documented index maps."""
    img = np.asarray(img, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    h, w = img.shape
    kh, kw = weights.shape
    ar, ac = anchor

    def resolve(i, n):
        if 0 <= i < n:
            return i
        if mode == BoundaryMode.PERIODIC:
            return i % n
        if mode == BoundaryMode.MIRROR:
            if n == 1:
                return 0
            m = i % (2 * n - 2)
            return m if m < n else 2 * n - 2 - m
        return None  # constant

    out = np.zeros((h, w))
    for i in range(h):
        for j in range(w):
            acc = 0.0
            for u in range(kh):
                for v in range(kw):
                    si, sj = i - u + ar, j - v + ac
                    ri = resolve(si, h)
                    rj = resolve(sj, w)
                    sample = cval if (ri is None or rj is None) else img[ri, rj]
                    acc += weights[u, v] * sample
            out[i, j] = acc
    return out


MODES = [BoundaryMode.MIRROR, BoundaryMode.CONSTANT, BoundaryMode.PERIODIC]


class TestCorrelateValid:
    def test_matches_naive_on_random_window(self):
        rng = np.random.default_rng(0)
        padded = rng.standard_normal((9, 11))
        weights = rng.standard_normal((3, 4))
        got = correlate_valid(padded, weights)
        # naive valid correlation
        expected = np.zeros((9 - 3 + 1, 11 - 4 + 1))
        for i in range(expected.shape[0]):
            for j in range(expected.shape[1]):
                expected[i, j] = float(np.sum(padded[i : i + 3, j : j + 4] * weights))
        np.testing.assert_allclose(got, expected, atol=1e-12)

    def test_rejects_window_smaller_than_kernel(self):
        from tileconv.errors import InvalidSpecError

        with pytest.raises(InvalidSpecError):
            correlate_valid(np.zeros((2, 2)), np.ones((3, 3)))


class TestImpulseAnchorSemantics:
    """Hand-computed impulse responses pin the anchor convention exactly."""

    def test_odd_kernel_impulse_response(self):
        img = np.zeros((7, 7))
        img[3, 3] = 1.0
        k = KernelSpec.dense([[1.0, 2.0, 3.0],
                              [4.0, 5.0, 6.0],
                              [7.0, 8.0, 9.0]])  # anchor (1,1)
        out = filter_full(img, k, BoundaryMode.CONSTANT, cval=0.0)
        # kernel appears verbatim with its anchor on the impulse
        np.testing.assert_array_equal(out[2:5, 2:5], k.dense_weights())
        assert out.sum() == 45.0

    def test_even_kernel_impulse_response_anchor_left_of_center(self):
        img = np.zeros((8, 8))
        img[4, 4] = 1.0
        weights = np.array([[1.0, 2.0, 3.0, 4.0],
                            [5.0, 6.0, 7.0, 8.0],
                            [9.0, 10.0, 11.0, 12.0],
                            [13.0, 14.0, 15.0, 16.0]])
        k = KernelSpec.dense(weights)  # default anchor (1, 1)
        out = filter_full(img, k, BoundaryMode.CONSTANT, cval=0.0)
        # anchor (1,1) on impulse at (4,4): kernel occupies rows 3..6, cols 3..6
        np.testing.assert_array_equal(out[3:7, 3:7], weights)
        assert out[2, :].sum() == 0.0 and out[7, :].sum() == 0.0

    def test_even_kernel_explicit_anchor(self):
        img = np.zeros((8, 8))
        img[4, 4] = 1.0
        weights = np.array([[1.0, 2.0], [3.0, 4.0]])
        k = KernelSpec.dense(weights, anchor=(0, 1))
        out = filter_full(img, k, BoundaryMode.CONSTANT, cval=0.0)
        # anchor (0,1): kernel occupies rows 4..5, cols 3..4
        np.testing.assert_array_equal(out[4:6, 3:5], weights)


class TestEngineVsNaiveOracle:
    @pytest.mark.parametrize("mode", MODES)
    def test_random_image_odd_kernel(self, mode):
        rng = np.random.default_rng(42)
        img = rng.standard_normal((11, 9))
        weights = rng.standard_normal((3, 5))
        k = KernelSpec.dense(weights)
        got = filter_full(img, k, mode, cval=0.25)
        expected = naive_filter(img, weights, (1, 2), mode, cval=0.25)
        np.testing.assert_allclose(got, expected, atol=1e-10)

    @pytest.mark.parametrize("mode", MODES)
    def test_random_image_even_kernel(self, mode):
        rng = np.random.default_rng(7)
        img = rng.standard_normal((10, 12))
        weights = rng.standard_normal((4, 2))
        k = KernelSpec.dense(weights)  # anchor (1, 0)
        got = filter_full(img, k, mode, cval=-1.0)
        expected = naive_filter(img, weights, (1, 0), mode, cval=-1.0)
        np.testing.assert_allclose(got, expected, atol=1e-10)

    @pytest.mark.parametrize("mode", MODES)
    def test_kernel_larger_than_image(self, mode):
        rng = np.random.default_rng(3)
        img = rng.standard_normal((3, 2))
        weights = rng.standard_normal((5, 5))
        k = KernelSpec.dense(weights)
        got = filter_full(img, k, mode, cval=0.0)
        expected = naive_filter(img, weights, (2, 2), mode, cval=0.0)
        np.testing.assert_allclose(got, expected, atol=1e-10)


class TestEngineVsScipy:
    """scipy.ndimage is an independent implementation of the same contract."""

    @pytest.mark.parametrize("mode", MODES)
    def test_dense_matches_scipy_reference(self, mode):
        rng = np.random.default_rng(11)
        img = rng.standard_normal((13, 10))
        k = KernelSpec.dense(rng.standard_normal((4, 3)))  # even x odd
        got = filter_full(img, k, mode, cval=0.5)
        expected = direct_reference(img, k, mode, cval=0.5)
        np.testing.assert_allclose(got, expected, atol=1e-10)

    @pytest.mark.parametrize("mode", MODES)
    def test_separable_matches_scipy_reference(self, mode):
        rng = np.random.default_rng(13)
        img = rng.standard_normal((12, 14))
        k = KernelSpec.separable(rng.standard_normal(5), rng.standard_normal(4))
        got = filter_full(img, k, mode, cval=0.0)
        expected = direct_reference(img, k, mode, cval=0.0)
        np.testing.assert_allclose(got, expected, atol=1e-10)

    def test_separable_equals_dense_outer_product(self):
        rng = np.random.default_rng(17)
        img = rng.standard_normal((9, 9))
        col = rng.standard_normal(3)
        row = rng.standard_normal(4)
        ks = KernelSpec.separable(col, row)
        kd = KernelSpec.dense(np.outer(col, row))
        for mode in MODES:
            np.testing.assert_allclose(
                filter_full(img, ks, mode), filter_full(img, kd, mode), atol=1e-12)


class TestFilterWindow:
    def test_window_equals_full_image_slice(self):
        rng = np.random.default_rng(23)
        img = rng.standard_normal((20, 18))
        k = KernelSpec.dense(rng.standard_normal((3, 3)))
        full = filter_full(img, k, BoundaryMode.MIRROR)
        block = filter_window(img, 5, 11, 4, 13, k, BoundaryMode.MIRROR)
        np.testing.assert_allclose(block, full[5:11, 4:13], atol=1e-12)
