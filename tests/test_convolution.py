"""Convolution matrix tests.

Reference answers here are hand-computed literals or come from
numpy.convolve — never from the module under test.
"""

import numpy as np
import pytest

from fir_backend.convolution import convolution_matrix, predict
from fir_backend.errors import InputValidationError


def test_zero_pad_small_case_hand_computed():
    # x = [1, 2, 3], order 2. Row t is [x[t], x[t-1]] with x[-1] = 0.
    matrix = convolution_matrix(np.array([1.0, 2.0, 3.0]), 2, mode="zero_pad")
    expected = np.array([[1.0, 0.0], [2.0, 1.0], [3.0, 2.0]])
    np.testing.assert_array_equal(matrix, expected)


def test_zero_pad_shape_matches_observation_length():
    x = np.arange(1.0, 11.0)
    for order in (1, 3, 7):
        matrix = convolution_matrix(x, order, mode="zero_pad")
        assert matrix.shape == (x.size, order)


def test_valid_mode_drops_boundary_rows():
    # x = [1, 2, 3, 4], order 2, valid rows correspond to t = 1, 2, 3.
    matrix = convolution_matrix(np.array([1.0, 2.0, 3.0, 4.0]), 2, mode="valid")
    expected = np.array([[2.0, 1.0], [3.0, 2.0], [4.0, 3.0]])
    np.testing.assert_array_equal(matrix, expected)


def test_matrix_times_taps_equals_numpy_convolve():
    rng = np.random.default_rng(7)
    x = rng.standard_normal(64)
    h = rng.standard_normal(9)
    matrix = convolution_matrix(x, h.size, mode="zero_pad")
    reference = np.convolve(x, h, mode="full")[: x.size]
    np.testing.assert_allclose(matrix @ h, reference, rtol=1e-12, atol=1e-12)


def test_predict_matches_matrix_convention():
    rng = np.random.default_rng(11)
    x = rng.standard_normal(50)
    h = rng.standard_normal(5)
    matrix = convolution_matrix(x, h.size, mode="zero_pad")
    np.testing.assert_allclose(predict(x, h), matrix @ h, rtol=1e-12, atol=1e-12)
    assert predict(x, h).shape == (x.size,)


def test_unknown_mode_rejected():
    with pytest.raises(InputValidationError):
        convolution_matrix(np.ones(8), 2, mode="circular")


def test_valid_mode_requires_order_within_samples():
    with pytest.raises(InputValidationError):
        convolution_matrix(np.ones(3), 5, mode="valid")
