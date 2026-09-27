"""No-gradient (None) vs zero-gradient (zeros) distinction, and grad mode."""

import numpy as np
import pytest

from minigrad import BackwardError, Tensor, enable_grad, no_grad
from minigrad import ops


def test_unreachable_leaf_has_no_gradient():
    x = Tensor([1.0, 2.0], requires_grad=True)
    y = Tensor([3.0, 4.0], requires_grad=True)  # never used
    (x * 2.0).sum().backward()
    assert x.grad is not None
    assert y.grad is None  # "no gradient", not zeros


def test_zero_gradient_is_not_none():
    x = Tensor([1.0, -2.0], requires_grad=True)
    loss = (x * 0.0).sum()  # gradient flows but is exactly zero
    loss.backward()
    assert x.grad is not None
    np.testing.assert_allclose(x.grad, [0.0, 0.0])


def test_relu_dead_unit_gives_zero_grad_not_none():
    x = Tensor([-3.0], requires_grad=True)
    ops.relu(x).sum().backward()
    assert x.grad is not None
    np.testing.assert_allclose(x.grad, [0.0])


def test_requires_grad_false_never_gets_grad():
    x = Tensor([1.0, 2.0])  # requires_grad=False
    y = Tensor([3.0, 4.0], requires_grad=True)
    (x * y).sum().backward()
    assert x.grad is None
    np.testing.assert_allclose(y.grad, [1.0, 2.0])


def test_no_grad_context_records_nothing():
    x = Tensor([1.0, 2.0], requires_grad=True)
    with no_grad():
        y = x * x
    assert y.requires_grad is False
    assert y._node is None
    with pytest.raises(BackwardError):
        y.sum().backward()


def test_enable_grad_nested_inside_no_grad():
    x = Tensor(2.0, requires_grad=True)
    with no_grad():
        with enable_grad():
            y = x * x
    assert y.requires_grad is True
    y.backward()
    np.testing.assert_allclose(x.grad, 4.0)


def test_grad_mode_is_restored_after_exception():
    x = Tensor(1.0, requires_grad=True)
    with pytest.raises(RuntimeError):
        with no_grad():
            raise RuntimeError("boom")
    y = x * x  # grad mode restored: recording works again
    assert y.requires_grad is True
