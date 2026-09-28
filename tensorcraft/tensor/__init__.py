"""Tensor type package: layout, storage, indexing, tensor, ops."""

from .dtypes import DTypeInfo, resolve_dtype, SUPPORTED
from .indexing import parse_indexer, newaxis
from .layout import (
    ADDRESS_LIMIT,
    Layout,
    Order,
    ReshapePlan,
    broadcast_shape,
    broadcast_strides,
    c_strides,
    checked_size,
    default_strides,
    f_strides,
    infer_unknown_dimension,
    reshape_plan,
    try_nocopy_reshape,
)
from .ops import (
    OpResult,
    comparison,
    elementwise,
    matmul,
    reduce_sum,
    scalar_op,
    unary,
)
from .storage import Storage
from .tensor import Tensor, WriteReport

__all__ = [
    "ADDRESS_LIMIT", "DTypeInfo", "Layout", "OpResult", "Order",
    "ReshapePlan", "Storage", "Tensor", "WriteReport",
    "broadcast_shape", "broadcast_strides", "c_strides", "checked_size",
    "comparison", "default_strides", "elementwise", "f_strides",
    "infer_unknown_dimension", "matmul", "newaxis", "parse_indexer",
    "reduce_sum", "reshape_plan", "resolve_dtype", "scalar_op",
    "try_nocopy_reshape", "unary", "SUPPORTED",
]
