"""Computation graph routes: register, inspect, execute with a trace."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..graph import Graph, execute_graph
from .schemas import GraphCreate, GraphExecuteRequest


def register_graph_routes(router: APIRouter) -> None:

    @router.post("/graphs", status_code=201, tags=["graphs"])
    async def create_graph(payload: GraphCreate, request: Request) -> dict:
        graph = Graph.from_dict({
            "nodes": [node.model_dump() for node in payload.nodes],
            "outputs": payload.outputs,
            "inputs": payload.inputs,
        })
        handle = request.app.state.graphs.put(graph, handle=payload.handle)
        return {"ok": True, "handle": handle,
                "node_count": len(graph.nodes),
                "graph": graph.to_dict()}

    @router.get("/graphs", tags=["graphs"])
    async def list_graphs(request: Request) -> dict:
        handles = request.app.state.graphs.list_handles()
        return {"ok": True, "handles": handles, "count": len(handles)}

    @router.get("/graphs/{handle}", tags=["graphs"])
    async def get_graph(handle: str, request: Request) -> dict:
        graph = request.app.state.graphs.get(handle)
        return {"ok": True, "handle": handle, "graph": graph.to_dict()}

    @router.delete("/graphs/{handle}", tags=["graphs"])
    async def delete_graph(handle: str, request: Request) -> dict:
        request.app.state.graphs.delete(handle)
        return {"ok": True, "deleted": handle}

    @router.post("/graphs/{handle}/execute", tags=["graphs"])
    async def execute(handle: str, payload: GraphExecuteRequest,
                      request: Request) -> dict:
        graph = request.app.state.graphs.get(handle)
        store = request.app.state.tensors
        bindings = {
            name: store.get(tensor_handle)
            for name, tensor_handle in payload.bindings.items()
        }
        execution = execute_graph(
            graph, bindings, output_names=payload.outputs)

        output_handles: dict[str, str] = {}
        output_values: dict[str, object] = {}
        for name, tensor in execution.outputs.items():
            out_handle = store.put(tensor)
            output_handles[name] = out_handle
            if payload.include_values:
                output_values[name] = tensor.to_nested()

        request.app.state.logger.info(
            "graph %s executed: %d nodes, outputs=%s",
            handle, len(execution.traces), list(output_handles))
        return {
            "ok": True,
            "outputs": output_handles,
            "values": output_values,
            "trace": execution.trace_dicts(),
            "node_count": len(execution.traces),
        }
