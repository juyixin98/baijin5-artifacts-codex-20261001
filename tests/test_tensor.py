"""Tensor type basics: construction, dtype policy, version counter."""

import numpy as np
import pytest

from minigrad import Tensor


def test_default_dtype_is_float64():
    t = Tensor([1, 2, 3])
    assert t.data.dtype == np.float64


def test_shape_ndim_size_views():
    t = Tensor(np.zeros((2, 3, 4)))
    assert t.shape == (2, 3, 4)
    assert t.ndim == 3
    assert t.size == 24


def test_numpy_returns_a_copy():
    t = Tensor([1.0, 2.0])
    arr = t.numpy()
    arr[0] = 99.0
    assert t.data[0] == 1.0  # mutating the copy cannot corrupt the tensor


def test_version_counter_bumps_on_tracked_inplace_ops():
    t = Tensor([1.0, 2.0, 3.0])
    assert t._version == 0
    t[0] = 5.0
    assert t._version == 1
    t.fill_(1.0)
    assert t._version == 2
    t.zero_()
    assert t._version == 3
    t += 1.0
    assert t._version == 4
    t *= 2.0
    assert t._version == 5
    t.copy_(Tensor([7.0, 8.0, 9.0]))
    assert t._version == 6
    assert t.data.tolist() == [7.0, 8.0, 9.0]


def test_detach_shares_data_but_not_grad():
    x = Tensor([1.0, 2.0], requires_grad=True)
    z = x.detach()
    assert z.requires_grad is False
    assert z._node is None
    z[0] = 10.0
    assert x.data[0] == 10.0  # documented: detach shares storage
    assert z._version == 1 and x._version == 0  # versions are independent


def test_zero_grad_resets_to_none_not_zeros():
    x = Tensor([1.0], requires_grad=True)
    (x * 2).sum().backward()
    assert x.grad is not None
    x.zero_grad()
    assert x.grad is None
