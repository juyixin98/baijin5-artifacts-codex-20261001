"""Broadcast gradient semantics: expanded axes are summed over.

All expected gradients below are derived by hand:

* L = sum(x * b), x ones (2,3), b = [1,2,3]
    dL/db_j = sum_i x_ij = 2            -> [2, 2, 2]
    dL/dx_ij = b_j                      -> rows of [1, 2, 3]
* L = sum(a * b), a (1,3) = [1,2,3], b (2,1) = [[1],[2]]
    dL/da_j = sum_i b_i = 3             -> [[3, 3, 3]]
    dL/db_i = sum_j a_j = 6             -> [[6], [6]]
* L = sum(x + c), x (2,3), scalar c
    dL/dc = 6 (one contribution per output element)
"""

import numpy as np

from minigrad import Tensor


def test_broadcast_grad_sums_over_batch_axis():
    x = Tensor(np.ones((2, 3)), requires_grad=True)
    b = Tensor([1.0, 2.0, 3.0], requires_grad=True)
    loss = (x * b).sum()
    loss.backward()
    np.testing.assert_allclose(b.grad, [2.0, 2.0, 2.0])
    np.testing.assert_allclose(x.grad, [[1, 2, 3], [1, 2, 3]])


def test_broadcast_grad_both_directions():
    a = Tensor([[1.0, 2.0, 3.0]], requires_grad=True)   # (1, 3)
    b = Tensor([[1.0], [2.0]], requires_grad=True)      # (2, 1)
    loss = (a * b).sum()
    loss.backward()
    np.testing.assert_allclose(a.grad, [[3.0, 3.0, 3.0]])
    np.testing.assert_allclose(b.grad, [[6.0], [6.0]])


def test_broadcast_grad_scalar_operand():
    x = Tensor(np.ones((2, 3)), requires_grad=True)
    c = Tensor(0.5, requires_grad=True)
    loss = (x + c).sum()
    loss.backward()
    np.testing.assert_allclose(c.grad, 6.0)
    np.testing.assert_allclose(x.grad, np.ones((2, 3)))


def test_broadcast_grad_leading_dims():
    # (2,1,3) * (3,) -> (2,1,3): grad for b sums over both leading axes
    x = Tensor(np.ones((2, 1, 3)), requires_grad=True)
    b = Tensor([1.0, 2.0, 3.0], requires_grad=True)
    loss = (x * b).sum()
    loss.backward()
    np.testing.assert_allclose(b.grad, [2.0, 2.0, 2.0])
    np.testing.assert_allclose(x.grad, np.broadcast_to([1, 2, 3], (2, 1, 3)))


def test_broadcast_to_op_grad():
    b = Tensor([1.0, 2.0, 3.0], requires_grad=True)
    from minigrad import ops
    loss = ops.broadcast_to(b, (4, 3)).sum()
    loss.backward()
    np.testing.assert_allclose(b.grad, [4.0, 4.0, 4.0])
