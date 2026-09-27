"""Forward-pass correctness with hand-computed expected values.

Expected arrays below are written literally in the test (computed by hand /
by pure NumPy semantics), not produced by the autodiff engine.
"""

import numpy as np
import pytest

from minigrad import Tensor
from minigrad import ops


def test_add_broadcast_forward():
    x = Tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    b = Tensor([10.0, 20.0, 30.0])
    out = x + b
    np.testing.assert_allclose(out.data, [[11, 22, 33], [14, 25, 36]])


def test_mul_broadcast_forward():
    a = Tensor([[1.0, 2.0, 3.0]])       # (1, 3)
    b = Tensor([[1.0], [2.0]])          # (2, 1)
    out = a * b
    np.testing.assert_allclose(out.data, [[1, 2, 3], [2, 4, 6]])


def test_matmul_forward_2d():
    a = Tensor([[1.0, 2.0], [3.0, 4.0]])
    b = Tensor([[5.0, 6.0], [7.0, 8.0]])
    np.testing.assert_allclose((a @ b).data, [[19, 22], [43, 50]])


def test_matmul_forward_vector():
    a = Tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    v = Tensor([1.0, 0.0, -1.0])
    np.testing.assert_allclose((a @ v).data, [-2.0, -2.0])
    np.testing.assert_allclose((v @ v).data, 2.0)


def test_reductions_forward():
    x = Tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    assert x.sum().data == 21.0
    np.testing.assert_allclose(x.sum(axis=0).data, [5, 7, 9])
    np.testing.assert_allclose(x.mean(axis=1).data, [2.0, 5.0])
    np.testing.assert_allclose(x.max(axis=1).data, [3.0, 6.0])
    assert x.max().data == 6.0


def test_activations_forward():
    np.testing.assert_allclose(ops.sigmoid(Tensor([0.0])).data, [0.5])
    np.testing.assert_allclose(ops.tanh(Tensor([0.0])).data, [0.0])
    np.testing.assert_allclose(ops.relu(Tensor([-1.0, 0.5, 2.0])).data, [0, 0.5, 2.0])
    np.testing.assert_allclose(ops.exp(Tensor([0.0])).data, [1.0])
    np.testing.assert_allclose(ops.log(Tensor([1.0])).data, [0.0])


def test_reshape_transpose_forward():
    x = Tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    assert x.reshape((3, 2)).shape == (3, 2)
    np.testing.assert_allclose(x.T.data, [[1, 4], [2, 5], [3, 6]])


def test_div_pow_forward():
    a = Tensor([2.0, 4.0])
    b = Tensor([2.0, 2.0])
    np.testing.assert_allclose((a / b).data, [1.0, 2.0])
    np.testing.assert_allclose((a ** 3).data, [8.0, 64.0])
