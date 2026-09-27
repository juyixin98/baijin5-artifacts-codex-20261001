"""Tests for the Tensor data type: storage, versions, gradient states."""
from __future__ import annotations

import numpy as np
import pytest

from autodiff import Tensor, no_grad
from autodiff.tensor import _DEFAULT_DTYPE


def test_tensor_copies_and_locks_data():
    source = np.array([1.0, 2.0])
    t = Tensor(source)
    # Copy: mutating the source must not change the tensor.
    source[0] = 99.0
    assert t.data.tolist() == [1.0, 2.0]
    # Locked: raw in-place writes are rejected by numpy itself.
    with pytest.raises(ValueError):
        t.data[0] = 5.0
    assert not t.data.flags.writeable


def test_tensor_is_float64_and_contiguous():
    t = Tensor([[1, 2], [3, 4]])
    assert t.dtype == _DEFAULT_DTYPE
    assert t.data.flags["C_CONTIGUOUS"]


def test_version_starts_zero_and_set_data_bumps():
    t = Tensor([1.0, 2.0], requires_grad=True)
    assert t.version == 0
    t.set_data([3.0, 4.0])
    assert t.version == 1
    assert t.data.tolist() == [3.0, 4.0]
    t.set_data([0.0, 0.0])
    assert t.version == 2


def test_grad_none_means_no_gradient():
    t = Tensor([1.0], requires_grad=True)
    assert t.grad is None
    assert t.is_leaf()


def test_zero_grad_is_distinct_from_no_grad():
    t = Tensor([1.0], requires_grad=True)
    t.zero_grad()
    assert t.grad is not None
    assert t.grad.shape == (1,)
    assert t.grad.tolist() == [0.0]
    t.clear_grad()
    assert t.grad is None


def test_accumulate_grad_sums_repeated_contributions():
    t = Tensor([1.0, 2.0], requires_grad=True)
    t.accumulate_grad(np.array([1.0, 0.0]))
    t.accumulate_grad(np.array([0.0, 3.0]))
    t.accumulate_grad(np.array([-1.0, -3.0]))
    assert t.grad.tolist() == [0.0, 0.0]
    # An accumulated net-zero stays an *explicit zero array*, never None.
    assert t.grad is not None
    assert not t.grad.flags.writeable


def test_accumulate_grad_rejects_shape_mismatch():
    t = Tensor(np.zeros((2, 2)), requires_grad=True)
    with pytest.raises(RuntimeError):
        t.accumulate_grad(np.zeros((2,)))


def test_detach_drops_tracking_but_keeps_values():
    t = Tensor([1.0, 2.0], requires_grad=True)
    d = t.detach()
    assert d.requires_grad is False
    assert d.data.tolist() == [1.0, 2.0]
    assert d.is_leaf()


def test_no_grad_context_forces_requires_grad_false():
    t1 = Tensor([1.0], requires_grad=True)
    assert t1.requires_grad is True
    with no_grad():
        t2 = Tensor([1.0], requires_grad=True)
        assert t2.requires_grad is False
    # Restored on exit.
    t3 = Tensor([1.0], requires_grad=True)
    assert t3.requires_grad is True


def test_no_grad_as_decorator():
    @no_grad()
    def make():
        return Tensor([1.0], requires_grad=True)

    assert make().requires_grad is False


def test_shape_ndim_size_accessors():
    t = Tensor(np.zeros((2, 3, 4)))
    assert t.shape == (2, 3, 4)
    assert t.ndim == 3
    assert t.size == 24
