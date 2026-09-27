"""Empty-dimension behavior (shapes containing 0)."""

import numpy as np
import pytest

from minigrad import Tensor


def test_empty_sum_forward_and_backward():
    x = Tensor(np.zeros((0, 3)), requires_grad=True)
    b = Tensor([1.0, 2.0, 3.0], requires_grad=True)
    loss = (x * b).sum() + (b * b).sum()
    assert loss.data == 14.0  # 0 + (1 + 4 + 9)
    loss.backward()
    assert x.grad is not None
    assert x.grad.shape == (0, 3)  # empty gradient, correct shape
    np.testing.assert_allclose(b.grad, [2.0, 4.0, 6.0])


def test_empty_matmul_inner_dim():
    a = Tensor(np.zeros((2, 0)), requires_grad=True)
    b = Tensor(np.zeros((0, 3)), requires_grad=True)
    out = a @ b
    np.testing.assert_allclose(out.data, np.zeros((2, 3)))
    out.sum().backward()
    assert a.grad.shape == (2, 0)
    assert b.grad.shape == (0, 3)


def test_empty_mean_is_nan_documented():
    # Boundary semantics: mean over an empty axis follows NumPy -> NaN.
    x = Tensor(np.zeros((0, 3)), requires_grad=True)
    with pytest.warns(RuntimeWarning):
        out = x.mean()
    assert np.isnan(out.data)


def test_empty_max_raises_like_numpy():
    x = Tensor(np.zeros((0, 3)))
    with pytest.raises(ValueError):
        x.max()
