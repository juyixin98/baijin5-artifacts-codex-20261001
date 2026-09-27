"""Tests for Tensor operator sugar and scalar coercion."""
from __future__ import annotations

import numpy as np
import pytest

from autodiff import Tensor, backward, exp, log


@pytest.mark.parametrize("op,ref", [
    (lambda a, b: a + b, lambda a, b: a + b),
    (lambda a, b: a - b, lambda a, b: a - b),
    (lambda a, b: a * b, lambda a, b: a * b),
    (lambda a, b: a / b, lambda a, b: a / b),
])
def test_binary_operators_against_numpy(op, ref, rng):
    av = rng.normal(size=(2, 3))
    bv = rng.normal(size=(2, 3))
    a, b = Tensor(av, requires_grad=True), Tensor(bv, requires_grad=True)
    out = op(a, b)
    assert np.allclose(out.data, ref(av, bv))
    backward(out.sum())
    # Independent numeric check on this small expression.
    eps = 1e-6
    ga = np.zeros_like(av)
    for i in np.ndindex(av.shape):
        up, dn = av.copy(), av.copy()
        up[i] += eps; dn[i] -= eps
        ga[i] = (float(ref(up, bv).sum()) - float(ref(dn, bv).sum())) / (2 * eps)
    assert np.allclose(a.grad, ga, atol=1e-6)


@pytest.mark.parametrize("dunder,ref_deriv", [
    ("__radd__", 1.0),
    ("__rsub__", -1.0),
    ("__rmul__", 2.0),
    ("__rtruediv__", -2.0),
])
def test_reverse_operators_with_scalar_left(dunder, ref_deriv):
    # scalar-op-tensor routes through the reverse dunders; x at a known point.
    x = Tensor(np.array([2.0, 2.0]), requires_grad=True)
    if dunder == "__radd__":
        out = 1.0 + x; deriv = np.ones(2)
    elif dunder == "__rsub__":
        out = 1.0 - x; deriv = -np.ones(2)
    elif dunder == "__rmul__":
        out = 2.0 * x; deriv = 2 * np.ones(2)
    else:
        out = 2.0 / x; deriv = -2.0 / (x.data ** 2)
    backward(out.sum())
    assert np.allclose(x.grad, deriv)


def test_method_sum_mean_and_matmul_sugar():
    x = Tensor(np.arange(6, dtype=float).reshape(2, 3), requires_grad=True)
    w = Tensor(np.ones((3, 2)), requires_grad=True)
    out = (x @ w).sum() + x.mean()
    backward(out)
    # (x@w).sum(): grad x = ones(2,2) @ W^T = 2 everywhere; mean adds 1/6.
    assert np.allclose(x.grad, np.full((2, 3), 2.0 + 1.0 / 6.0))
    # grad W = X^T @ ones(2,2)
    assert np.allclose(w.grad, x.data.T @ np.ones((2, 2)))


def test_scalar_tensor_arithmetic():
    s = Tensor(2.0, requires_grad=True)
    backward((s * s * s).sum())  # 3 s^2 at s=2
    assert float(s.grad) == 12.0
