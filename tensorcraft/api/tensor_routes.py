"""Tensor lifecycle and view routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..tensor import Layout, Tensor
from .schemas import (
    AssignScalarRequest,
    AssignTensorRequest,
    AstypeRequest,
    BroadcastRequest,
    MaterializeRequest,
    ReshapeRequest,
    SliceRequest,
    StridedViewRequest,
    TensorCreate,
    TransposeRequest,
)


def _store(request: Request):
    return request.app.state.tensors


def _describe(handle: str, tensor: Tensor) -> dict[str, Any]:
    body = tensor.describe()
    body["handle"] = handle
    return body


def register_tensor_routes(router: APIRouter) -> None:

    @router.post("/tensors", status_code=201, tags=["tensors"])
    async def create_tensor(payload: TensorCreate, request: Request) -> dict:
        tensor = Tensor.from_nested(payload.data, payload.dtype)
        handle = _store(request).put(tensor, handle=payload.handle)
        request.app.state.logger.info(
            "created tensor %s shape=%s dtype=%s",
            handle, tensor.shape, tensor.dtype.name)
        return {"ok": True, "handle": handle, "tensor": _describe(handle, tensor)}

    @router.get("/tensors", tags=["tensors"])
    async def list_tensors(request: Request) -> dict:
        handles = _store(request).list_handles()
        return {"ok": True, "handles": handles, "count": len(handles)}

    @router.get("/tensors/{handle}", tags=["tensors"])
    async def get_tensor(handle: str, request: Request) -> dict:
        tensor = _store(request).get(handle)
        return {"ok": True, "tensor": _describe(handle, tensor)}

    @router.get("/tensors/{handle}/values", tags=["tensors"])
    async def get_values(handle: str, request: Request) -> dict:
        tensor = _store(request).get(handle)
        return {"ok": True, "handle": handle,
                "values": tensor.to_nested(),
                "order": "C"}

    @router.delete("/tensors/{handle}", status_code=200, tags=["tensors"])
    async def delete_tensor(handle: str, request: Request) -> dict:
        _store(request).delete(handle)
        return {"ok": True, "deleted": handle}

    @router.post("/tensors/{handle}/transpose", status_code=201, tags=["tensors"])
    async def transpose(handle: str, payload: TransposeRequest,
                        request: Request) -> dict:
        source = _store(request).get(handle)
        result = source.transpose(payload.axes)
        new_handle = _store(request).put(result)
        return {"ok": True, "handle": new_handle,
                "copied": not result.shares_storage_with(source),
                "tensor": _describe(new_handle, result)}

    @router.post("/tensors/{handle}/reshape", status_code=201, tags=["tensors"])
    async def reshape(handle: str, payload: ReshapeRequest,
                      request: Request) -> dict:
        source = _store(request).get(handle)
        result = source.reshape(payload.shape, payload.order,
                                allow_copy=payload.allow_copy)
        copied = not result.shares_storage_with(source)
        new_handle = _store(request).put(result)
        return {"ok": True, "handle": new_handle, "copied": copied,
                "history": result.history,
                "tensor": _describe(new_handle, result)}

    @router.post("/tensors/{handle}/slice", status_code=201, tags=["tensors"])
    async def slice_tensor(handle: str, payload: SliceRequest,
                           request: Request) -> dict:
        source = _store(request).get(handle)
        from .routes_common import decode_index
        result = source.getitem(decode_index(payload.index))
        new_handle = _store(request).put(result)
        return {"ok": True, "handle": new_handle, "copied": False,
                "tensor": _describe(new_handle, result)}

    @router.post("/tensors/{handle}/broadcast", status_code=201, tags=["tensors"])
    async def broadcast(handle: str, payload: BroadcastRequest,
                        request: Request) -> dict:
        source = _store(request).get(handle)
        result = source.broadcast_to(payload.shape)
        new_handle = _store(request).put(result)
        return {"ok": True, "handle": new_handle, "copied": False,
                "tensor": _describe(new_handle, result)}

    @router.post("/tensors/{handle}/strided-view", status_code=201, tags=["tensors"])
    async def strided_view(handle: str, payload: StridedViewRequest,
                           request: Request) -> dict:
        """Attach an explicit (shape, strides, offset) layout to storage.

        Mirrors ``np.lib.stride_tricks.as_strided``: the same buffer is
        reused, so overlapping views can be built and the write policies
        exercised over HTTP. Out-of-range layouts are rejected before the
        view is registered.
        """
        source = _store(request).get(handle)
        layout = Layout(tuple(payload.shape), tuple(payload.strides),
                        payload.offset)
        result = Tensor(source.storage, layout, history="strided_view")
        new_handle = _store(request).put(result)
        return {"ok": True, "handle": new_handle, "copied": False,
                "self_overlapping": result.is_self_overlapping()
                if result.size <= 4096 else None,
                "tensor": _describe(new_handle, result)}

    @router.post("/tensors/{handle}/materialize", status_code=201, tags=["tensors"])
    async def materialize(handle: str, payload: MaterializeRequest,
                          request: Request) -> dict:
        source = _store(request).get(handle)
        result = source.materialize(payload.order)
        new_handle = _store(request).put(result)
        return {"ok": True, "handle": new_handle, "copied": True,
                "tensor": _describe(new_handle, result)}

    @router.post("/tensors/{handle}/astype", status_code=201, tags=["tensors"])
    async def astype(handle: str, payload: AstypeRequest,
                     request: Request) -> dict:
        source = _store(request).get(handle)
        result = source.astype(payload.dtype)
        copied = not result.shares_storage_with(source)
        new_handle = _store(request).put(result)
        return {"ok": True, "handle": new_handle, "copied": copied,
                "tensor": _describe(new_handle, result)}

    @router.post("/tensors/{handle}/assign/scalar", tags=["tensors"])
    async def assign_scalar(handle: str, payload: AssignScalarRequest,
                            request: Request) -> dict:
        tensor = _store(request).get(handle)
        report = tensor.assign_scalar(payload.value, policy=payload.policy)
        return {"ok": True, "report": report.__dict__,
                "tensor": _describe(handle, tensor)}

    @router.post("/tensors/{handle}/assign/tensor", tags=["tensors"])
    async def assign_tensor(handle: str, payload: AssignTensorRequest,
                            request: Request) -> dict:
        tensor = _store(request).get(handle)
        source = _store(request).get(payload.source)
        report = tensor.assign(source, policy=payload.policy)
        return {"ok": True, "report": report.__dict__,
                "tensor": _describe(handle, tensor)}
