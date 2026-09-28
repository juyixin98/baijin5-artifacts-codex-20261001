"""Basic elementwise and reduction operations on stride-layout tensors.

Every op reports whether it allocated fresh storage (``copied=True``) or
returned a view alias, so the verification layer and API can assert the
copy/alias contract. Elementwise ops accept broadcasting inputs; the result
is always freshly allocated (NumPy semantics: ``a + b`` never aliases an
operand), but operands with zero-stride axes are handled with no expansion.

Integer division by zero and overflow raise :class:`NumericError`; floats
follow IEEE-754 and produce inf/nan as NumPy does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..errors import (
    DTypeMismatchError,
    NumericError,
    ShapeMismatchError,
)
from .dtypes import assert_same_dtype, resolve_dtype
from .layout import (
    Layout,
    Order,
    broadcast_shape,
    broadcast_strides,
    default_strides,
)
from .storage import Storage
from .tensor import Tensor

_SCALAR_OPS: dict[str, Callable[[np.ndarray, np.ndarray], np.ndarray]] = {
    "add": np.add,
    "subtract": np.subtract,
    "multiply": np.multiply,
    "divide": np.true_divide,
    "floor_divide": np.floor_divide,
    "mod": np.mod,
    "power": np.power,
}

_UNARY_OPS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "neg": np.negative,
    "abs": np.absolute,
}

_COMPARISON_OPS: dict[str, Callable[[np.ndarray, np.ndarray], np.ndarray]] = {
    "equal": np.equal,
    "not_equal": np.not_equal,
    "less": np.less,
    "less_equal": np.less_equal,
    "greater": np.greater,
    "greater_equal": np.greater_equal,
}


@dataclass(frozen=True)
class OpResult:
    tensor: Tensor
    copied: bool
    op: str
    broadcast_shape: tuple[int, ...]


def _broadcast_inputs(
    left: Tensor, right: Tensor, op: str
) -> tuple[tuple[int, ...], np.ndarray, np.ndarray, Layout, Layout]:
    target = broadcast_shape(left.shape, right.shape)
    left_strides = broadcast_strides(left.shape, left.strides, target)
    right_strides = broadcast_strides(right.shape, right.strides, target)
    left_layout = Layout(target, left_strides, left.storage_offset)
    right_layout = Layout(target, right_strides, right.storage_offset)
    left_view = Tensor(left.storage, left_layout, history=f"{op}.broadcast_lhs")
    right_view = Tensor(right.storage, right_layout, history=f"{op}.broadcast_rhs")
    return target, left_view.numpy_view(), right_view.numpy_view(), left_layout, right_layout


def elementwise(left: Tensor, right: Tensor, op: str) -> OpResult:
    """Binary elementwise op with broadcasting and identical-dtype rule."""
    assert_same_dtype(left.dtype, right.dtype, op=op)
    fn = _SCALAR_OPS.get(op)
    if fn is None:
        raise ShapeMismatchError(
            f"unknown elementwise op {op!r}; known: {sorted(_SCALAR_OPS)}")
    target, left_arr, right_arr, _, _ = _broadcast_inputs(left, right, op)

    if op in ("divide",) and left.dtype.kind in ("int", "uint"):
        # NumPy promotes int/int true-divide to float64; do the same.
        out_dtype = resolve_dtype("float64")
        left_arr = left_arr.astype(out_dtype.np_dtype, copy=False)
        right_arr = right_arr.astype(out_dtype.np_dtype, copy=False)
    else:
        out_dtype = left.dtype

    result = _run_numpy(fn, left_arr, right_arr, op)
    storage = Storage(np.ascontiguousarray(result).reshape(-1), out_dtype)
    layout = Layout(target, default_strides(target, Order.C), 0)
    return OpResult(
        Tensor(storage, layout, history=f"{op}{target}"),
        copied=True, op=op, broadcast_shape=target)


def _run_numpy(fn, left_arr, right_arr, op: str) -> np.ndarray:
    with np.errstate(over="raise", divide="raise", invalid="raise"):
        try:
            return fn(left_arr, right_arr)
        except FloatingPointError as exc:
            raise NumericError(
                f"operation {op!r} failed numerically: {exc}",
                details={"op": op}) from exc
        except ZeroDivisionError as exc:
            raise NumericError(
                f"operation {op!r} hit integer division by zero",
                details={"op": op}) from exc


def unary(tensor: Tensor, op: str) -> OpResult:
    fn = _UNARY_OPS.get(op)
    if fn is None:
        raise ShapeMismatchError(
            f"unknown unary op {op!r}; known: {sorted(_UNARY_OPS)}")
    with np.errstate(over="raise", invalid="raise"):
        try:
            result = fn(tensor.numpy_view())
        except FloatingPointError as exc:
            raise NumericError(f"operation {op!r} failed numerically: {exc}",
                               details={"op": op}) from exc
    storage = Storage(np.ascontiguousarray(result).reshape(-1), tensor.dtype)
    return OpResult(
        Tensor(storage,
               Layout(tensor.shape, default_strides(tensor.shape, Order.C), 0),
               history=f"{op}"),
        copied=True, op=op, broadcast_shape=tensor.shape)


def comparison(left: Tensor, right: Tensor, op: str) -> OpResult:
    """Elementwise comparison; results are ``uint8`` (0/1), explicitly."""
    if left.dtype.name != right.dtype.name:
        raise DTypeMismatchError(
            f"comparison {op!r} requires identical dtypes, got "
            f"{left.dtype.name!r} and {right.dtype.name!r}",
            details={"op": op, "left": left.dtype.name,
                     "right": right.dtype.name})
    fn = _COMPARISON_OPS.get(op)
    if fn is None:
        raise ShapeMismatchError(
            f"unknown comparison op {op!r}; known: {sorted(_COMPARISON_OPS)}")
    target, left_arr, right_arr, _, _ = _broadcast_inputs(left, right, op)
    result = fn(left_arr, right_arr).astype(np.uint8)
    info = resolve_dtype("uint8")
    storage = Storage(np.ascontiguousarray(result).reshape(-1), info)
    layout = Layout(target, default_strides(target, Order.C), 0)
    return OpResult(
        Tensor(storage, layout, history=f"{op}{target}"),
        copied=True, op=op, broadcast_shape=target)


def scalar_op(tensor: Tensor, value, op: str) -> OpResult:
    """Tensor op Python scalar; the scalar is cast to the tensor dtype."""
    fn = _SCALAR_OPS.get(op)
    if fn is None:
        raise ShapeMismatchError(
            f"unknown elementwise op {op!r}; known: {sorted(_SCALAR_OPS)}")
    scalar = np.asarray(value, dtype=tensor.dtype.np_dtype)
    out_dtype = tensor.dtype
    arr = tensor.numpy_view()
    work = arr
    if op == "divide" and tensor.dtype.kind in ("int", "uint"):
        out_dtype = resolve_dtype("float64")
        work = arr.astype(out_dtype.np_dtype, copy=False)
        scalar = scalar.astype(out_dtype.np_dtype, copy=False)
    result = _run_numpy(fn, work, scalar, op)
    storage = Storage(np.ascontiguousarray(result).reshape(-1), out_dtype)
    return OpResult(
        Tensor(storage,
               Layout(tensor.shape, default_strides(tensor.shape, Order.C), 0),
               history=f"{op}.scalar"),
        copied=True, op=op, broadcast_shape=tensor.shape)


def matmul(left: Tensor, right: Tensor) -> OpResult:
    """Matrix products: 1-D dot, 2-D @ 2-D, and batched 2-D @ 2-D.

    No broadcasting of batch dimensions (kept explicit: a mismatch raises
    rather than silently aligning).
    """
    assert_same_dtype(left.dtype, right.dtype, op="matmul")
    if left.ndim < 1 or right.ndim < 1:
        raise ShapeMismatchError("matmul operands must be at least 1-D")
    contracting = right.shape[0] if right.ndim == 1 else right.shape[-2]
    if left.shape[-1] != contracting:
        raise ShapeMismatchError(
            f"matmul shape mismatch: {left.shape} @ {right.shape}",
            details={"left": left.shape, "right": right.shape})
    if left.ndim >= 3 or right.ndim >= 3:
        if left.ndim != right.ndim or left.shape[:-2] != right.shape[:-2]:
            raise ShapeMismatchError(
                "batch broadcasting in matmul is not supported; batch shapes "
                f"must match exactly: {left.shape[:-2]} vs {right.shape[:-2]}")
    with np.errstate(over="raise"):
        try:
            result = np.matmul(left.numpy_view(), right.numpy_view())
        except FloatingPointError as exc:
            raise NumericError(f"matmul overflow: {exc}",
                               details={"op": "matmul"}) from exc
    target = tuple(result.shape)
    storage = Storage(np.ascontiguousarray(result).reshape(-1), left.dtype)
    layout = Layout(target, default_strides(target, Order.C), 0)
    return OpResult(
        Tensor(storage, layout, history=f"matmul{target}"),
        copied=True, op="matmul", broadcast_shape=target)


def reduce_sum(tensor: Tensor, axis: int | None = None,
               keepdims: bool = False) -> OpResult:
    """Sum along one axis or all axes. Zero-stride axes count per NumPy."""
    axes: int | tuple[int, ...] | None
    if axis is None:
        axes = None
    else:
        axes = axis
        if not -tensor.ndim <= axis < tensor.ndim:
            raise ShapeMismatchError(
                f"reduce axis {axis} out of range for ndim {tensor.ndim}")
    with np.errstate(over="raise"):
        try:
            result = np.sum(tensor.numpy_view(), axis=axes, keepdims=keepdims)
        except FloatingPointError as exc:
            raise NumericError(f"reduce_sum overflow: {exc}",
                               details={"op": "reduce_sum"}) from exc
    target = tuple(np.shape(result))
    storage = Storage(np.ascontiguousarray(result).reshape(-1), tensor.dtype)
    layout = Layout(target, default_strides(target, Order.C), 0)
    return OpResult(
        Tensor(storage, layout, history=f"reduce_sum(axis={axis},keepdims={keepdims})"),
        copied=True, op="reduce_sum", broadcast_shape=target)
