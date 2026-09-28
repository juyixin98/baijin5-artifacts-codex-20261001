"""Graph executor: schedules nodes and applies real tensor operations.

Every supported op is a thin, explicit adapter over the core
:mod:`tensorcraft.tensor` layer -- nothing is hardcoded: node ids only
select which adapter runs; all values come from bindings or earlier nodes.

Supported ops
-------------
View (zero copy when the layout allows):
    transpose   params: axes? (list)
    reshape     params: shape (list, -1 allowed), order ("C"/"F"),
                allow_copy (bool, default true)
    slice       params: index (list of entries); each entry is one of
                [start, stop, step] (nulls allowed), an int, "newaxis",
                "ellipsis"
    squeeze     params: axis? (int)
    broadcast_to params: shape (list)
Materializing:
    materialize params: order?
    astype      params: dtype
Arithmetic:
    add subtract multiply divide floor_divide mod power
    neg abs
    equal not_equal less less_equal greater greater_equal
    scalar_*    params: value  (e.g. op "scalar_add")
    matmul
    reduce_sum  params: axis?, keepdims?
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..errors import GraphError
from ..tensor import Tensor
from ..tensor import ops as tensor_ops
from .graph import Graph, NodeSpec

_BINARY_OPS = {
    "add", "subtract", "multiply", "divide", "floor_divide", "mod", "power",
}
_COMPARISON_OPS = {
    "equal", "not_equal", "less", "less_equal", "greater", "greater_equal",
}
_UNARY_OPS = {"neg", "abs"}
_VIEW_OPS = {
    "transpose", "reshape", "slice", "squeeze", "broadcast_to",
}
_MATERIALIZING_OPS = {"materialize", "astype"}


@dataclass(frozen=True)
class NodeTrace:
    node_id: str
    op: str
    input_tokens: tuple[int, ...]
    output_token: int
    copied: bool
    aliases_input: int | None
    shape: tuple[int, ...]
    strides: tuple[int, ...]
    detail: str
    elapsed_us: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "op": self.op,
            "input_tokens": list(self.input_tokens),
            "output_token": self.output_token,
            "copied": self.copied,
            "aliases_input": self.aliases_input,
            "shape": list(self.shape),
            "strides": list(self.strides),
            "detail": self.detail,
            "elapsed_us": round(self.elapsed_us, 3),
        }


@dataclass(frozen=True)
class GraphExecution:
    outputs: dict[str, Tensor]
    values: dict[str, Tensor]
    traces: tuple[NodeTrace, ...]

    def trace_dicts(self) -> list[dict[str, Any]]:
        return [trace.to_dict() for trace in self.traces]


def _decode_index_entry(entry: Any):
    if isinstance(entry, str):
        if entry == "newaxis":
            return None
        if entry == "ellipsis":
            return Ellipsis
        raise GraphError(
            f"unsupported index entry {entry!r}; use a list, int, "
            "'newaxis' or 'ellipsis'")
    if isinstance(entry, bool):
        raise GraphError(
            f"boolean scalar index is not supported, got {entry!r}")
    if isinstance(entry, int):
        return entry
    if isinstance(entry, (list, tuple)):
        if len(entry) != 3:
            raise GraphError(
                f"slice entry must be [start, stop, step], got {entry!r}")
        return slice(entry[0], entry[1], entry[2])
    raise GraphError(f"unsupported index entry {entry!r}")


def _require(params: dict[str, Any], key: str, node: NodeSpec) -> Any:
    if key not in params:
        raise GraphError(
            f"node {node.id!r} op {node.op!r} requires param {key!r}",
            details={"node": node.id, "op": node.op, "missing": key})
    return params[key]


def _check_arity(node: NodeSpec, expected: int) -> None:
    if len(node.inputs) != expected:
        raise GraphError(
            f"node {node.id!r} op {node.op!r} expects {expected} inputs, "
            f"got {len(node.inputs)}",
            details={"node": node.id, "op": node.op,
                     "expected": expected, "got": len(node.inputs)})


def _alias_of(result: Tensor, operands: list[Tensor]) -> int | None:
    for operand in operands:
        if result.shares_storage_with(operand):
            return operand.token
    return None


def execute_graph(
    graph: Graph,
    bindings: dict[str, Tensor],
    *,
    output_names: list[str] | None = None,
) -> GraphExecution:
    """Execute ``graph`` with external tensors in ``bindings``.

    Nodes are scheduled in topological order (pruned to the requested
    outputs); each node produces one tensor and one trace record.
    """
    values: dict[str, Tensor] = dict(bindings)
    for name in graph.inputs:
        if name not in bindings:
            raise GraphError(
                f"missing binding for declared graph input {name!r}",
                details={"missing": name})
    for name, value in bindings.items():
        if not isinstance(value, Tensor):
            raise GraphError(f"binding {name!r} must be a Tensor")

    wanted = output_names if output_names is not None else list(graph.outputs)
    if not wanted:
        wanted = [node.id for node in graph.nodes]
    for name in wanted:
        if name not in {n.id for n in graph.nodes} and name not in bindings:
            raise GraphError(f"requested output {name!r} does not exist")

    scheduled = graph.topological_order(wanted)
    traces: list[NodeTrace] = []

    for node in scheduled:
        started = time.perf_counter()
        operands = [values[ref] for ref in node.inputs]
        result, copied, detail = _apply_node(node, operands)
        elapsed = (time.perf_counter() - started) * 1e6
        values[node.id] = result
        traces.append(NodeTrace(
            node_id=node.id,
            op=node.op,
            input_tokens=tuple(operand.token for operand in operands),
            output_token=result.token,
            copied=copied,
            aliases_input=_alias_of(result, operands),
            shape=result.shape,
            strides=result.strides,
            detail=detail,
            elapsed_us=elapsed,
        ))

    outputs = {name: values[name] for name in wanted}
    return GraphExecution(outputs=outputs, values=values,
                          traces=tuple(traces))


def _apply_node(
    node: NodeSpec, operands: list[Tensor]
) -> tuple[Tensor, bool, str]:
    op = node.op
    params = node.params

    if op in _VIEW_OPS:
        return _apply_view(node, operands)
    if op in _MATERIALIZING_OPS:
        return _apply_materialize(node, operands)
    if op in _BINARY_OPS:
        _check_arity(node, 2)
        result = tensor_ops.elementwise(operands[0], operands[1], op)
        return result.tensor, True, f"broadcast -> {result.broadcast_shape}"
    if op in _COMPARISON_OPS:
        _check_arity(node, 2)
        result = tensor_ops.comparison(operands[0], operands[1], op)
        return result.tensor, True, f"uint8 mask, shape {result.broadcast_shape}"
    if op in _UNARY_OPS:
        _check_arity(node, 1)
        result = tensor_ops.unary(operands[0], op)
        return result.tensor, True, "fresh storage"
    if op.startswith("scalar_"):
        _check_arity(node, 1)
        inner = op[len("scalar_"):]
        value = _require(params, "value", node)
        result = tensor_ops.scalar_op(operands[0], value, inner)
        return result.tensor, True, f"value={value!r}"
    if op == "matmul":
        _check_arity(node, 2)
        result = tensor_ops.matmul(operands[0], operands[1])
        return result.tensor, True, f"shape {result.broadcast_shape}"
    if op == "reduce_sum":
        _check_arity(node, 1)
        axis = params.get("axis")
        keepdims = bool(params.get("keepdims", False))
        result = tensor_ops.reduce_sum(operands[0], axis=axis,
                                       keepdims=keepdims)
        return result.tensor, True, f"axis={axis}, keepdims={keepdims}"
    raise GraphError(
        f"node {node.id!r}: unknown op {op!r}",
        details={"node": node.id, "op": op,
                 "known": sorted(_VIEW_OPS | _MATERIALIZING_OPS | _BINARY_OPS
                                 | _COMPARISON_OPS | _UNARY_OPS
                                 | {"matmul", "reduce_sum"})
                 + ["scalar_<op>"]})


def _apply_view(
    node: NodeSpec, operands: list[Tensor]
) -> tuple[Tensor, bool, str]:
    op = node.op
    params = node.params
    if op == "transpose":
        _check_arity(node, 1)
        axes = params.get("axes")
        result = operands[0].transpose(axes)
        return result, False, f"axes={tuple(axes) if axes is not None else 'reversed'}"
    if op == "reshape":
        _check_arity(node, 1)
        shape = _require(params, "shape", node)
        order = params.get("order", "C")
        allow_copy = bool(params.get("allow_copy", True))
        before = operands[0]
        result = before.reshape(shape, order, allow_copy=allow_copy)
        copied = not result.shares_storage_with(before)
        return result, copied, (
            f"order={order}, allow_copy={allow_copy}, "
            f"{'COPY' if copied else 'view'}")
    if op == "slice":
        _check_arity(node, 1)
        raw_index = _require(params, "index", node)
        if not isinstance(raw_index, list):
            raise GraphError(
                f"node {node.id!r}: slice 'index' must be a list")
        indexer = tuple(_decode_index_entry(entry) for entry in raw_index)
        result = operands[0].getitem(indexer)
        return result, False, f"index={raw_index}"
    if op == "squeeze":
        _check_arity(node, 1)
        axis = params.get("axis")
        result = operands[0].squeeze(None if axis is None else int(axis))
        return result, False, f"axis={axis}"
    if op == "broadcast_to":
        _check_arity(node, 1)
        shape = _require(params, "shape", node)
        result = operands[0].broadcast_to(shape)
        return result, False, f"shape={tuple(shape)}"
    raise GraphError(f"unhandled view op {op!r}")  # pragma: no cover


def _apply_materialize(
    node: NodeSpec, operands: list[Tensor]
) -> tuple[Tensor, bool, str]:
    if node.op == "materialize":
        _check_arity(node, 1)
        order = node.params.get("order", "C")
        result = operands[0].materialize(order)
        return result, True, f"contiguous copy ({order})"
    if node.op == "astype":
        _check_arity(node, 1)
        dtype = _require(node.params, "dtype", node)
        before = operands[0]
        result = before.astype(dtype)
        copied = not result.shares_storage_with(before)
        return result, copied, f"dtype={dtype}"
    raise GraphError(f"unhandled materializing op {node.op!r}")  # pragma: no cover
