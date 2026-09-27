"""matmul backward variants: dot, mat-vec, vec-mat, batched.

Hand-derived expectations:
* dot:  a=[1,2,3], b=[4,5,6] -> L=32, da=b, db=a
* mat@vec: A=[[1,2],[3,4]], v=[1,1], L=sum(A@v)
    dA = outer(ones, v) = [[1,1],[1,1]], dv = A.T @ ones = [4,6]
* vec@mat: v=[1,2], A=[[1,2],[3,4]], L=sum(v@A)
    dv = [A[0,0]+A[0,1], A[1,0]+A[1,1]] = [3,7], dA_ij = v_i = [[1,1],[2,2]]
* batched: A,B all-ones (2,2,3)@(2,3,2), L=sum -> every dA, dB entry = 2
"""

import numpy as np

from minigrad import Tensor


def test_dot_product_backward():
    a = Tensor([1.0, 2.0, 3.0], requires_grad=True)
    b = Tensor([4.0, 5.0, 6.0], requires_grad=True)
    loss = a @ b
    assert loss.data == 32.0
    loss.backward()
    np.testing.assert_allclose(a.grad, [4, 5, 6])
    np.testing.assert_allclose(b.grad, [1, 2, 3])


def test_mat_vec_backward():
    a = Tensor([[1.0, 2.0], [3.0, 4.0]], requires_grad=True)
    v = Tensor([1.0, 1.0], requires_grad=True)
    (a @ v).sum().backward()
    np.testing.assert_allclose(a.grad, [[1, 1], [1, 1]])
    np.testing.assert_allclose(v.grad, [4, 6])


def test_vec_mat_backward():
    v = Tensor([1.0, 2.0], requires_grad=True)
    a = Tensor([[1.0, 2.0], [3.0, 4.0]], requires_grad=True)
    (v @ a).sum().backward()
    np.testing.assert_allclose(v.grad, [3, 7])
    np.testing.assert_allclose(a.grad, [[1, 1], [2, 2]])


def test_batched_matmul_backward():
    a = Tensor(np.ones((2, 2, 3)), requires_grad=True)
    b = Tensor(np.ones((2, 3, 2)), requires_grad=True)
    out = a @ b
    np.testing.assert_allclose(out.data, np.full((2, 2, 2), 3.0))
    out.sum().backward()
    np.testing.assert_allclose(a.grad, np.full((2, 2, 3), 2.0))
    np.testing.assert_allclose(b.grad, np.full((2, 3, 2), 2.0))
