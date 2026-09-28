"""Elementwise / matmul / reduction routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..tensor import ops as tensor_ops
from .schemas import (
    BinaryOpRequest,
    MatmulRequest,
    ReduceRequest,
    ScalarOpRequest,
    UnaryOpRequest,
)

_BINARY = {
    "add", "subtract", "multiply", "divide", "floor_divide", "mod", "power",
}
_COMPARISON = {
    "equal", "not_equal", "less", "less_equal", "greater", "greater_equal",
}
_UNARY = {"neg", "abs"}


def _describe(handle: str, tensor) -> dict:
    body = tensor.describe()
    body["handle"] = handle
    return body


def register_op_routes(router: APIRouter) -> None:

    @router.post("/ops/binary", tags=["ops"])
    async def binary(payload: BinaryOpRequest, request: Request) -> dict:
        store = request.app.state.tensors
        left = store.get(payload.left)
        right = store.get(payload.right)
        if payload.op in _BINARY:
            result = tensor_ops.elementwise(left, right, payload.op)
        elif payload.op in _COMPARISON:
            result = tensor_ops.comparison(left, right, payload.op)
        else:
            from ..errors import ShapeMismatchError
            raise ShapeMismatchError(
                f"unknown binary op {payload.op!r}",
                details={"known": sorted(_BINARY | _COMPARISON)})
        handle = store.put(result.tensor)
        return {"ok": True, "handle": handle, "copied": True,
                "op": payload.op,
                "broadcast_shape": list(result.broadcast_shape),
                "tensor": _describe(handle, result.tensor)}

    @router.post("/ops/unary", tags=["ops"])
    async def unary(payload: UnaryOpRequest, request: Request) -> dict:
        tensor = request.app.state.tensors.get(payload.tensor)
        op = payload.op
        if op not in _UNARY:
            from ..errors import ShapeMismatchError
            raise ShapeMismatchError(
                f"unknown unary op {op!r}", details={"known": sorted(_UNARY)})
        result = tensor_ops.unary(tensor, op)
        handle = request.app.state.tensors.put(result.tensor)
        return {"ok": True, "handle": handle, "copied": True,
                "tensor": _describe(handle, result.tensor)}

    @router.post("/ops/scalar", tags=["ops"])
    async def scalar(payload: ScalarOpRequest, request: Request) -> dict:
        tensor = request.app.state.tensors.get(payload.tensor)
        if payload.op not in _BINARY:
            from ..errors import ShapeMismatchError
            raise ShapeMismatchError(
                f"unknown scalar op {payload.op!r}", details={"known": sorted(_BINARY)})
        result = tensor_ops.scalar_op(tensor, payload.value, payload.op)
        handle = request.app.state.tensors.put(result.tensor)
        return {"ok": True, "handle": handle, "copied": True,
                "tensor": _describe(handle, result.tensor)}

    @router.post("/ops/matmul", tags=["ops"])
    async def matmul(payload: MatmulRequest, request: Request) -> dict:
        store = request.app.state.tensors
        result = tensor_ops.matmul(store.get(payload.left),
                                   store.get(payload.right))
        handle = store.put(result.tensor)
        return {"ok": True, "handle": handle, "copied": True,
                "tensor": _describe(handle, result.tensor)}

    @router.post("/ops/reduce_sum", tags=["ops"])
    async def reduce_sum(payload: ReduceRequest, request: Request) -> dict:
        tensor = request.app.state.tensors.get(payload.tensor)
        result = tensor_ops.reduce_sum(tensor, axis=payload.axis,
                                       keepdims=payload.keepdims)
        handle = request.app.state.tensors.put(result.tensor)
        return {"ok": True, "handle": handle, "copied": True,
                "tensor": _describe(handle, result.tensor)}
