"""Tests for matmul and reduction VJPs, including empty dimensions."""
from __future__ import annotations

import numpy as np
import pytest

from autodiff import Tensor, backward, ops


# ---------------------------------------------------------------------------
# Matmul: hand-computed exact values
# ---------------------------------------------------------------------------


def test_matmul_2d_exact():
    a = Tensor(np.array([[1.0, 2.0], [3.0, 4.0]]), requires_grad=True)
    b = Tensor(np.array([[0.0, 1.0], [-1.0, 0.0]]), requires_grad=True)
    loss = ops.matmul(a, b).sum()
    backward(loss)
    # C = A@B; grad output all ones.
    # ga = ones @ B^T = [[1,1],[1,1]] @ [[0,-1],[1,0]] = [[1,-1],[1,-1]]
    assert a.grad.tolist() == [[1.0, -1.0], [1.0, -1.0]]
    # gb = A^T @ ones = [[1,3],[2,4]] @ [[1,1],[1,1]] = [[4,4],[6,6]]
    assert b.grad.tolist() == [[4.0, 4.0], [6.0, 6.0]]


def test_matmul_vector_matrix_exact():
    x = Tensor(np.array([1.0, 0.0, -1.0]), requires_grad=True)
    w = Tensor(np.ones((3, 2)), requires_grad=True)
    backward(ops.matmul(x, w).sum())
    # grad x = ones @ W^T = [2,2,2]
    assert x.grad.tolist() == [2.0, 2.0, 2.0]
    # grad W = outer(x, ones)
    assert w.grad.tolist() == [[1.0, 1.0], [0.0, 0.0], [-1.0, -1.0]]


def test_matmul_matrix_vector_exact():
    a = Tensor(np.array([[1.0, 2.0], [3.0, 4.0]]), requires_grad=True)
    v = Tensor(np.array([1.0, -1.0]), requires_grad=True)
    backward(ops.matmul(a, v).sum())
    # grad a = outer(ones, v)
    assert a.grad.tolist() == [[1.0, -1.0], [1.0, -1.0]]
    # grad v = A^T @ ones = [4, 6]
    assert v.grad.tolist() == [4.0, 6.0]


def test_matmul_dot_product_exact():
    u = Tensor(np.array([1.0, 2.0, 3.0]), requires_grad=True)
    v = Tensor(np.array([-1.0, 0.0, 1.0]), requires_grad=True)
    backward(ops.matmul(u, v).sum())
    assert u.grad.tolist() == [-1.0, 0.0, 1.0]
    assert v.grad.tolist() == [1.0, 2.0, 3.0]


def test_batched_matmul_unbroadcasts_batch_axes():
    # (1,2,3) @ (2,3,2) -> (2,2,2); first operand broadcasts batch dim.
    rng = np.random.default_rng(7)
    a = Tensor(rng.normal(size=(1, 2, 3)), requires_grad=True)
    b = Tensor(rng.normal(size=(2, 3, 2)), requires_grad=True)
    backward(ops.matmul(a, b).sum())
    # a's single batch row accumulates cotangents from both output batches.
    assert a.grad.shape == (1, 2, 3)
    assert b.grad.shape == (2, 3, 2)
    # Cross-check the accumulation against direct numpy.
    g = np.ones((2, 2, 2))
    ga_full = np.matmul(g, np.swapaxes(b.data, -1, -2))  # (2,2,3)
    assert np.allclose(a.grad[0], ga_full.sum(axis=0))


# ---------------------------------------------------------------------------
# Empty dimensions
# ---------------------------------------------------------------------------


def test_empty_matmul_zero_contraction():
    p = Tensor(np.zeros((2, 0)), requires_grad=True)
    q = Tensor(np.zeros((0, 2)), requires_grad=True)
    c = ops.matmul(p, q)
    assert c.shape == (2, 2)
    assert c.data.tolist() == [[0.0, 0.0], [0.0, 0.0]]
    backward(c.sum())
    assert p.grad is not None and p.grad.shape == (2, 0)
    assert q.grad is not None and q.grad.shape == (0, 2)
    assert p.grad.size == 0 and q.grad.size == 0


def test_empty_sum_reduction():
    x = Tensor(np.zeros((0, 3)), requires_grad=True)
    s = x.sum(axis=0)
    assert s.shape == (3,)
    assert s.data.tolist() == [0.0, 0.0, 0.0]
    backward(s.sum())
    assert x.grad.shape == (0, 3)
    assert x.grad.size == 0


def test_empty_mean_axis_is_defined_zero():
    x = Tensor(np.zeros((2, 0)), requires_grad=True)
    m = x.mean(axis=1)  # per-row mean over an empty axis
    assert m.shape == (2,)
    assert np.isfinite(m.data).all() and m.data.tolist() == [0.0, 0.0]
    backward(m.sum())
    # Explicit zero cotangent with the input shape - not None.
    assert x.grad is not None
    assert x.grad.shape == (2, 0)


def test_empty_global_mean():
    x = Tensor(np.zeros((0,)), requires_grad=True)
    m = x.mean()
    assert m.shape == ()
    assert m.data == 0.0
    backward(m.sum())
    assert x.grad is not None and x.grad.shape == (0,)


# ---------------------------------------------------------------------------
# Reductions: exact gradients
# ---------------------------------------------------------------------------


def test_sum_gradient_is_ones_like():
    x = Tensor(np.arange(6, dtype=float).reshape(2, 3), requires_grad=True)
    backward(x.sum())
    assert x.grad.tolist() == np.ones((2, 3)).tolist()


def test_sum_axis_keepdims_broadcast():
    x = Tensor(np.arange(6, dtype=float).reshape(2, 3), requires_grad=True)
    backward(x.sum(axis=1, keepdims=True).sum())
    assert x.grad.tolist() == np.ones((2, 3)).tolist()


def test_mean_global_scales_by_element_count():
    x = Tensor(np.arange(6, dtype=float), requires_grad=True)
    backward(x.mean())
    assert x.grad.tolist() == pytest.approx([1.0 / 6.0] * 6)


def test_mean_axis_scales_by_reduced_dim():
    x = Tensor(np.arange(6, dtype=float).reshape(2, 3), requires_grad=True)
    backward(x.mean(axis=0).sum())
    # Each element contributes 1/2 to its column mean.
    assert np.allclose(x.grad, np.full((2, 3), 0.5))


def test_mean_multi_axis_denominator():
    x = Tensor(np.ones((2, 3, 4)), requires_grad=True)
    loss = x.mean(axis=(0, 2))  # denom 8 per output element
    backward(loss.sum())
    assert np.allclose(x.grad, np.full((2, 3, 4), 1.0 / 8.0))
