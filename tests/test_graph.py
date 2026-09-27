"""Tests for the graph engine: accumulation, versions, release/retain."""
from __future__ import annotations

import gc
import sys

import numpy as np
import pytest

from autodiff import (
    Tensor,
    backward,
    ops,
    GraphReleasedError,
    StaleGraphError,
    NonScalarLossError,
)


# ---------------------------------------------------------------------------
# Multi-branch sharing / repeated-node gradient accumulation
# ---------------------------------------------------------------------------


def test_shared_intermediate_accumulates_two_branches_exact():
    x = Tensor(np.array([2.0, 3.0]), requires_grad=True)
    y = x * x          # branch 1 contribution: 2x
    z = x * 3.0        # branch 2 contribution: 3
    loss = y.sum() + z.sum()
    backward(loss)
    assert x.grad.tolist() == [7.0, 9.0]  # 2x + 3


def test_diamond_graph_accumulates_at_join():
    #       x
    #      / \
    #   x*x   x*x      (same expression, two explicit consumers of x)
    #      \ /
    #       +
    x = Tensor(np.array([1.0, 2.0]), requires_grad=True)
    a = x * x
    b = x * x
    loss = (a + b).sum()
    backward(loss)
    assert x.grad.tolist() == [4.0, 8.0]  # 4x


def test_shared_weight_in_two_matmul_branches():
    w = Tensor(np.eye(2), requires_grad=True)
    x1 = Tensor(np.array([[1.0, 0.0]]), requires_grad=True)
    x2 = Tensor(np.array([[0.0, 1.0]]), requires_grad=True)
    loss = ops.matmul(x1, w).sum() + ops.matmul(x2, w).sum()
    backward(loss)
    # Each branch passes ones; W grad gets both outer-product contributions.
    assert w.grad.tolist() == [[1.0, 1.0], [1.0, 1.0]]


def test_second_backward_accumulates_when_retained():
    w = Tensor(np.array([2.0]), requires_grad=True)
    loss = (w * w).sum()
    backward(loss, retain_graph=True)
    assert w.grad.tolist() == [4.0]
    backward(loss, retain_graph=True)
    # Gradients accumulate across backwards; graph not cleared automatically.
    assert w.grad.tolist() == [8.0]


def test_unreached_leaf_keeps_none_while_reached_zero_is_array():
    used = Tensor(np.array([1.0, 2.0]), requires_grad=True)
    unused = Tensor(np.array([5.0, 6.0]), requires_grad=True)
    loss = (used * 0.0).sum()
    backward(loss)
    assert used.grad is not None
    assert used.grad.tolist() == [0.0, 0.0]
    assert unused.grad is None


# ---------------------------------------------------------------------------
# Version detection / in-place mutation (acceptance rule 2)
# ---------------------------------------------------------------------------


def test_set_data_after_capture_is_rejected_with_stale_error():
    x = Tensor(np.array([1.0, 2.0]), requires_grad=True)
    loss = (x * x).sum()
    x.set_data(np.array([9.0, 8.0]))
    with pytest.raises(StaleGraphError) as exc:
        backward(loss)
    # Diagnostic-grade message identifies the node and the versions.
    assert "stale graph" in str(exc.value)
    assert "version 0->1" in str(exc.value)


def test_version_rejection_leaves_gradients_untouched():
    x = Tensor(np.array([1.0]), requires_grad=True)
    y = Tensor(np.array([2.0]), requires_grad=True)
    loss = (x * y).sum()
    x.set_data(np.array([3.0]))
    with pytest.raises(StaleGraphError):
        backward(loss)
    assert x.grad is None
    assert y.grad is None


def test_deep_mutation_still_detected():
    x = Tensor(np.array([1.0, 2.0]), requires_grad=True)
    h = ops.relu(x * 2.0)
    loss = (h * h).sum()
    x.set_data(np.array([-1.0, -2.0]))  # flips relu masks, yet must be rejected
    with pytest.raises(StaleGraphError):
        backward(loss)


def test_raw_inplace_write_blocked_by_readonly_storage():
    x = Tensor(np.array([1.0, 2.0]), requires_grad=True)
    with pytest.raises(ValueError):
        x.data[0] = 42.0


def test_sanctioned_set_data_then_rebuild_backwards_fine():
    # After an optimizer-style update, rebuilding the forward graph works:
    # the new graph captures the new version, so no stale rejection.
    x = Tensor(np.array([1.0]), requires_grad=True)
    loss = (x * x).sum()
    backward(loss)
    x.set_data(x.data - 0.1 * x.grad)
    loss2 = (x * x).sum()
    x.clear_grad()
    backward(loss2)
    assert x.grad.tolist() == pytest.approx([2.0 * 0.8])


# ---------------------------------------------------------------------------
# Graph release / retain (acceptance rule 4)
# ---------------------------------------------------------------------------


def test_default_backward_releases_graph():
    w = Tensor(np.array([1.0]), requires_grad=True)
    loss = (w * w).sum()
    backward(loss)
    with pytest.raises(GraphReleasedError):
        backward(loss)


def test_released_output_cannot_enter_new_graph_without_detach():
    w = Tensor(np.array([1.0]), requires_grad=True)
    loss = (w * w).sum()
    backward(loss)
    with pytest.raises(GraphReleasedError):
        loss + 1.0
    # Explicit opt-in: treat the freed output as a constant.
    fresh = loss.detach() + 1.0
    assert float(fresh.data) == 2.0


def test_release_breaks_reference_cycles():
    w = Tensor(np.array([1.0, 2.0, 3.0]), requires_grad=True)
    loss = (w * w).sum()
    backward(loss)
    gc.collect()
    # The producing node dropped its input/output lists.
    assert loss.is_graph_detached()
    assert w._node is None


def test_retain_graph_keeps_node_inputs():
    w = Tensor(np.array([1.0, 2.0]), requires_grad=True)
    loss = (w * w).sum()
    backward(loss, retain_graph=True)
    assert not loss.is_graph_detached()
    assert loss._node is not None
    assert len(loss._node.inputs) >= 1


# ---------------------------------------------------------------------------
# Seed / shape validation
# ---------------------------------------------------------------------------


def test_non_scalar_loss_without_grad_output_rejected():
    x = Tensor(np.ones((2, 3)), requires_grad=True)
    with pytest.raises(NonScalarLossError):
        backward(x * 2.0)


def test_grad_output_shape_mismatch_rejected():
    x = Tensor(np.ones((2,)), requires_grad=True)
    with pytest.raises(RuntimeError):
        backward(x * 2.0, grad_output=np.ones((3,)))


def test_external_grad_output_exact():
    x = Tensor(np.array([[1.0, 2.0], [3.0, 4.0]]), requires_grad=True)
    y = x * x
    g = np.array([[1.0, 0.0], [0.0, 1.0]])
    backward(y, grad_output=g)
    assert x.grad.tolist() == (2 * x.data * g).tolist()


def test_backward_on_constant_is_noop():
    c = Tensor([1.0, 2.0])
    backward(c.sum())  # must not raise
    assert c.grad is None


def test_backward_on_leaf_seeds_itself():
    x = Tensor(3.0, requires_grad=True)
    assert x.is_leaf()
    backward(x)
    assert x.grad.shape == ()
    assert x.grad == pytest.approx(1.0)
