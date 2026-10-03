"""Tests for the boundary-consistent convolution design matrix.

Reference answers here come from an explicit index-level double loop and
from scipy.signal.convolve — independent of the implementation under test
(which uses stride tricks).
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import convolve

from app.errors import InputError
from app.signal_processing.convolution import (
    build_convolution_matrix,
    build_design_and_observation,
)


def reference_matrix_valid(x: np.ndarray, order: int) -> np.ndarray:
    """Independent reference: explicit loops, no stride tricks."""
    n = x.size
    rows = n - order + 1
    ref = np.zeros((rows, order))
    for i in range(rows):
        for k in range(order):
            ref[i, k] = x[i + order - 1 - k]
    return ref


def test_valid_matrix_matches_explicit_reference():
    x = np.array([3.0, -1.0, 2.0, 0.5, 4.0, -2.0, 1.0])
    order = 3
    matrix = build_convolution_matrix(x, order, boundary="valid")
    np.testing.assert_array_equal(matrix, reference_matrix_valid(x, order))
    assert matrix.shape == (x.size - order + 1, order)


def test_valid_matrix_row_times_taps_equals_convolution():
    rng = np.random.default_rng(42)
    x = rng.standard_normal(200)
    h = rng.standard_normal(7)
    matrix = build_convolution_matrix(x, h.size, boundary="valid")
    # scipy.signal.convolve is an independent implementation of the same math.
    expected = convolve(x, h, mode="valid")
    np.testing.assert_allclose(matrix @ h, expected, rtol=1e-12, atol=1e-12)


def test_zero_pad_matrix_matches_full_convolution_prefix():
    rng = np.random.default_rng(7)
    x = rng.standard_normal(120)
    h = rng.standard_normal(5)
    matrix = build_convolution_matrix(x, h.size, boundary="zero_pad")
    expected = convolve(x, h, mode="full")[: x.size]
    np.testing.assert_allclose(matrix @ h, expected, rtol=1e-12, atol=1e-12)
    assert matrix.shape == (x.size, h.size)


def test_observation_length_matches_matrix_rows_valid():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(100)
    h = np.array([0.5, -0.25, 0.1])
    y = convolve(x, h, mode="full")[: x.size]
    matrix, observation = build_design_and_observation(x, y, h.size, "valid")
    assert matrix.shape[0] == observation.shape[0] == x.size - h.size + 1
    # rows of the valid matrix predict y[order-1:]; residual is nonzero only
    # in the first order-1 samples of y, which 'valid' excludes.
    np.testing.assert_allclose(matrix @ h, observation, atol=1e-12)


def test_observation_length_matches_matrix_rows_zero_pad():
    rng = np.random.default_rng(2)
    x = rng.standard_normal(80)
    h = np.array([1.0, 0.5])
    y = convolve(x, h, mode="full")[: x.size]
    matrix, observation = build_design_and_observation(x, y, h.size, "zero_pad")
    assert matrix.shape[0] == observation.shape[0] == x.size
    np.testing.assert_allclose(matrix @ h, observation, atol=1e-12)


def test_order_larger_than_signal_rejected():
    with pytest.raises(InputError, match="order"):
        build_convolution_matrix(np.ones(4), 5)


def test_unknown_boundary_rejected():
    with pytest.raises(InputError, match="boundary"):
        build_convolution_matrix(np.ones(8), 2, boundary="circular")


def test_length_mismatch_rejected():
    with pytest.raises(InputError, match="length"):
        build_design_and_observation(np.ones(10), np.ones(9), 3, "valid")
