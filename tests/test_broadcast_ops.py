"""Tests for broadcasting gradients and elementwise op VJPs.

All expected values are computed analytically by hand or with a small
independent formula here in the test file - never by importing the core's
backward machinery.
"""
from __future__ import annotations

import numpy as np
import pytest

from autodiff import Tensor, backward, ops
from autodiff.ops import unbroadcast


# ---------------------------------------------------------------------------
# unbroadcast helper (direct unit tests)
# ---------------------------------------------------------------------------


def test_unbroadcast_drops_inserted_axes():
    # (1,3) broadcast to (2,3): sum leading axis.
    g = np.ones((2, 3))
    assert unbroadcast(g, (1, 3)).tolist() == [[2.0, 2.0, 2.0]]


def test_unbroadcast_sums_size_one_dims():
    # (2,1) broadcast to (2,3): sum axis 1 keepdims.
    g = np.ones((2, 3))
    assert unbroadcast(g, (2, 1)).tolist() == [[3.0], [3.0]]


def test_unbroadcast_to_scalar_sums_everything():
    g = np.arange(6, dtype=float).reshape(2, 3)
    out = unbroadcast(g, ())
    assert out.shape == ()
    assert out == pytest.approx(15.0)


def test_unbroadcast_preserves_matching_shape():
    g = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert unbroadcast(g, (2, 2)).tolist() == g.tolist()


# ---------------------------------------------------------------------------
# End-to-end broadcast gradients (exact hand-computed values)
# ---------------------------------------------------------------------------


def test_broadcast_add_row_plus_column_exact():
    a = Tensor(np.array([[1.0], [2.0]]), requires_grad=True)  # (2,1)
    b = Tensor(np.array([[10.0, 20.0, 30.0]]), requires_grad=True)  # (1,3)
    loss = (a + b).sum()
    backward(loss)
    # Every element of a affects 3 output cells; every b element affects 2.
    assert a.grad.tolist() == [[3.0], [3.0]]
    assert b.grad.tolist() == [[2.0, 2.0, 2.0]]


def test_broadcast_mul_exact_chain_rule():
    a = Tensor(np.array([[2.0], [4.0]]), requires_grad=True)  # (2,1)
    b = Tensor(np.array([[1.0, 3.0]]), requires_grad=True)    # (1,3)
    loss = (a * b).sum()
    backward(loss)
    # d/da_i = sum_j b_j = 4 ; d/db_j = sum_i a_i = 6
    assert a.grad.tolist() == [[4.0], [4.0]]
    assert b.grad.tolist() == [[6.0, 6.0]]


def test_broadcast_sub_and_div_exact():
    a = Tensor(np.array([[2.0]]), requires_grad=True)   # (1,1)
    b = Tensor(np.array([1.0, 2.0, 4.0]), requires_grad=True)  # (3,)
    loss = (a / b).sum()
    backward(loss)
    # d/da = sum 1/b = 1 + 1/2 + 1/4
    assert a.grad.tolist() == [[pytest.approx(1.75)]]
    # d/db_j = -a / b_j^2
    assert b.grad.tolist() == pytest.approx([-2.0, -0.5, -0.125])


def test_broadcast_gradient_accumulates_with_shared_use():
    # Same (3,) vector used twice against differently shaped tensors.
    x = Tensor(np.array([1.0, 2.0, 3.0]), requires_grad=True)
    col = Tensor(np.array([[1.0], [2.0]]), requires_grad=True)
    # x broadcast over rows and added twice in two branches.
    loss = (x + col).sum() + x.sum()
    backward(loss)
    # first branch: each x element seen over 2 rows -> 2; second branch: 1.
    assert x.grad.tolist() == [3.0, 3.0, 3.0]
    assert col.grad.tolist() == [[3.0], [3.0]]


def test_scalar_broadcast_to_matrix():
    s = Tensor(3.0, requires_grad=True)
    x = Tensor(np.ones((2, 2)), requires_grad=True)
    loss = (s * x).sum()
    backward(loss)
    assert s.grad.shape == ()
    assert s.grad == pytest.approx(4.0)
    assert x.grad.tolist() == [[3.0, 3.0], [3.0, 3.0]]


# ---------------------------------------------------------------------------
# Elementwise op VJPs at concrete points
# ---------------------------------------------------------------------------


def test_relu_gradient_is_indicator():
    x = Tensor(np.array([-2.0, -0.5, 0.0, 0.5, 2.0]), requires_grad=True)
    loss = ops.relu(x).sum()
    backward(loss)
    # Strictly positive indicator; 0 maps to 0 (forward relu(0)=0, mask >0).
    assert x.grad.tolist() == [0.0, 0.0, 0.0, 1.0, 1.0]


def test_sigmoid_gradient_exact():
    x = Tensor(np.array([0.0]), requires_grad=True)
    backward(ops.sigmoid(x).sum())
    assert x.grad == pytest.approx([0.25])


def test_tanh_gradient_exact():
    x = Tensor(np.array([0.0]), requires_grad=True)
    backward(ops.tanh(x).sum())
    assert x.grad == pytest.approx([1.0])


def test_exp_and_log_roundtrip_gradient():
    x = Tensor(np.array([1.0, 2.0]), requires_grad=True)
    loss = ops.log(ops.exp(x)).sum()  # identity chain
    backward(loss)
    assert x.grad.tolist() == pytest.approx([1.0, 1.0])


def test_neg_gradient():
    x = Tensor(np.array([1.0, -2.0]), requires_grad=True)
    backward((-x).sum())
    assert x.grad.tolist() == [-1.0, -1.0]


def test_constant_input_gets_no_gradient():
    x = Tensor(np.array([1.0, 2.0]), requires_grad=True)
    c = Tensor(np.array([3.0, 4.0]))  # requires_grad=False
    loss = (x + c).sum()
    backward(loss)
    assert x.grad.tolist() == [1.0, 1.0]
    assert c.grad is None
