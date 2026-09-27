"""Gradient accumulation for shared (reused) nodes.

* y = x*x + x           -> dy/dx = 2x + 1
* z = (x*x) * x         -> dz/dx = 3x^2  (x used by two mul nodes)
* L = sum(x*y) + sum(x+y) -> dL/dx = y + 1, dL/dy = x + 1
"""

import numpy as np

from minigrad import Tensor


def test_shared_leaf_two_consumers():
    x = Tensor(2.0, requires_grad=True)
    y = x * x + x
    y.backward()
    np.testing.assert_allclose(x.grad, 5.0)  # 2*2 + 1


def test_shared_intermediate_node():
    x = Tensor(2.0, requires_grad=True)
    x2 = x * x          # shared intermediate
    z = (x2 * x).sum()
    z.backward()
    np.testing.assert_allclose(x.grad, 12.0)  # 3 * 2^2


def test_shared_vector_multi_branch():
    x = Tensor([1.0, 2.0, 3.0], requires_grad=True)
    y = Tensor([4.0, 5.0, 6.0], requires_grad=True)
    loss = (x * y).sum() + (x + y).sum()
    loss.backward()
    np.testing.assert_allclose(x.grad, [5.0, 6.0, 7.0])  # y + 1
    np.testing.assert_allclose(y.grad, [2.0, 3.0, 4.0])  # x + 1


def test_repeated_backward_with_retain_accumulates():
    x = Tensor(3.0, requires_grad=True)
    y = x * x
    y.backward(retain_graph=True)
    y.backward(retain_graph=True)
    np.testing.assert_allclose(x.grad, 12.0)  # 2 * (2*3)
