"""Tests for elementwise ops, broadcasting, matmul and reductions."""

from __future__ import annotations

import numpy as np
import pytest

from tensorcraft.errors import (
    BroadcastError,
    DTypeMismatchError,
    NumericError,
    ShapeMismatchError,
)
from tensorcraft.tensor import Tensor, ops


class TestElementwise:
    @pytest.mark.parametrize("op,np_op", [
        ("add", np.add), ("subtract", np.subtract),
        ("multiply", np.multiply), ("floor_divide", np.floor_divide),
        ("mod", np.mod), ("power", np.power),
    ])
    def test_binary_matches_numpy(self, op, np_op):
        a = np.array([[6, 8], [10, 12]], dtype=np.int64)
        b = np.array([[3, 2], [5, 3]], dtype=np.int64)
        ta, tb = Tensor.from_nested(a.tolist(), "int64"), \
            Tensor.from_nested(b.tolist(), "int64")
        out = ops.elementwise(ta, tb, op).tensor
        np.testing.assert_array_equal(out.to_numpy(), np_op(a, b))

    def test_broadcast_add(self):
        a = Tensor.from_nested([[1], [2], [3]], "int64")
        b = Tensor.from_nested([[10, 20]], "int64")
        out = ops.elementwise(a, b, "add").tensor
        assert out.to_numpy().tolist() == [[11, 21], [12, 22], [13, 23]]

    def test_broadcast_error_category(self):
        a = Tensor.from_nested([1, 2, 3], "int64")
        b = Tensor.from_nested([1, 2], "int64")
        with pytest.raises(BroadcastError):
            ops.elementwise(a, b, "add")

    def test_dtype_mismatch_rejected(self):
        a = Tensor.from_nested([1, 2], "int64")
        b = Tensor.from_nested([1.0, 2.0], "float64")
        with pytest.raises(DTypeMismatchError) as exc:
            ops.elementwise(a, b, "add")
        assert exc.value.details["op"] == "add"

    def test_integer_true_divide_promotes_to_float64(self):
        a = Tensor.from_nested([5, 7], "int64")
        b = Tensor.from_nested([2, 2], "int64")
        out = ops.elementwise(a, b, "divide").tensor
        assert out.dtype.name == "float64"
        np.testing.assert_allclose(out.to_numpy(), [2.5, 3.5])

    def test_integer_division_by_zero_is_numeric_error(self):
        a = Tensor.from_nested([5], "int64")
        b = Tensor.from_nested([0], "int64")
        with pytest.raises(NumericError):
            ops.elementwise(a, b, "floor_divide")

    def test_unary_ops(self):
        a = Tensor.from_nested([-1, 2, -3], "int64")
        assert ops.unary(a, "neg").tensor.to_numpy().tolist() == [1, -2, 3]
        assert ops.unary(a, "abs").tensor.to_numpy().tolist() == [1, 2, 3]

    def test_comparison_returns_uint8_mask(self):
        a = Tensor.from_nested([1, 2, 3], "int64")
        mask = ops.comparison(a, a, "greater").tensor
        assert mask.dtype.name == "uint8"
        assert mask.to_numpy().tolist() == [0, 0, 0]
        eq = ops.comparison(a, a, "equal").tensor
        assert eq.to_numpy().tolist() == [1, 1, 1]

    def test_unknown_op_rejected(self):
        a = Tensor.from_nested([1], "int64")
        with pytest.raises(ShapeMismatchError):
            ops.unary(a, "sqrt")

    def test_operates_on_non_contiguous_view(self):
        # Arithmetic reads through arbitrary strides without forcing a copy
        # of the input (result itself is freshly allocated).
        base = Tensor.from_nested(np.arange(6).tolist(), "int64")
        view = base.reshape((2, 3), "C").transpose()  # (3,2), non-C
        doubled = ops.scalar_op(view, 2, "multiply").tensor
        assert doubled.to_numpy().tolist() == (
            (np.arange(6).reshape(2, 3).T * 2).tolist())


class TestMatmul:
    def test_2d_matmul_matches_numpy(self):
        a = np.arange(6).reshape(2, 3)
        b = np.arange(3, 9).reshape(3, 2)
        out = ops.matmul(Tensor.from_nested(a.tolist(), "int64"),
                         Tensor.from_nested(b.tolist(), "int64")).tensor
        np.testing.assert_array_equal(out.to_numpy(), a @ b)

    def test_matmul_accepts_transposed_operand(self):
        arr = np.arange(6).reshape(2, 3)
        a = Tensor.from_nested(arr.tolist(), "int64")
        b = Tensor.from_nested(arr.tolist(), "int64")
        # (2,3) @ (3,2): right operand is a transposed view.
        out = ops.matmul(a, b.transpose()).tensor
        np.testing.assert_array_equal(out.to_numpy(), arr @ arr.T)

    def test_1d_dot_product(self):
        a = Tensor.from_nested([1, 2, 3], "int64")
        b = Tensor.from_nested([4, 5, 6], "int64")
        out = ops.matmul(a, b).tensor
        assert out.to_numpy().reshape(()).item() == 32

    def test_shape_mismatch(self):
        a = Tensor.from_nested(np.zeros((2, 3)).tolist(), "int64")
        b = Tensor.from_nested(np.zeros((4, 2)).tolist(), "int64")
        with pytest.raises(ShapeMismatchError):
            ops.matmul(a, b)

    def test_batch_matmul(self):
        a = Tensor.from_nested(np.ones((4, 2, 3)).tolist(), "float64")
        b = Tensor.from_nested(np.ones((4, 3, 5)).tolist(), "float64")
        out = ops.matmul(a, b)
        assert out.tensor.shape == (4, 2, 5)

    def test_batch_mismatch_rejected(self):
        a = Tensor.from_nested(np.ones((4, 2, 3)).tolist(), "float64")
        b = Tensor.from_nested(np.ones((5, 3, 2)).tolist(), "float64")
        with pytest.raises(ShapeMismatchError):
            ops.matmul(a, b)


class TestReduction:
    def test_full_sum(self):
        a = Tensor.from_nested(np.arange(6).reshape(2, 3).tolist(), "int64")
        assert ops.reduce_sum(a).tensor.to_numpy().reshape(()).item() == 15

    def test_axis_sum_matches_numpy(self):
        arr = np.arange(12).reshape(3, 4)
        a = Tensor.from_nested(arr.tolist(), "int64")
        np.testing.assert_array_equal(
            ops.reduce_sum(a, axis=0).tensor.to_numpy(), arr.sum(axis=0))

    def test_keepdims(self):
        a = Tensor.from_nested(np.arange(6).reshape(2, 3).tolist(), "int64")
        out = ops.reduce_sum(a, axis=1, keepdims=True).tensor
        assert out.shape == (2, 1)

    def test_invalid_axis(self):
        a = Tensor.from_nested([[1, 2]], "int64")
        with pytest.raises(ShapeMismatchError):
            ops.reduce_sum(a, axis=5)
