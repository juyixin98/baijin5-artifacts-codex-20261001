"""Register all API routers on the FastAPI app."""

from __future__ import annotations

from fastapi import FastAPI

from .graph_routes import register_graph_routes
from .op_routes import register_op_routes
from .tensor_routes import register_tensor_routes
from .training_routes import register_training_routes
from .verify_routes import register_validation_routes


def register_routes(app: FastAPI) -> None:
    router_kwargs = {"prefix": "/api/v1"}
    from fastapi import APIRouter

    tensors = APIRouter()
    register_tensor_routes(tensors)
    app.include_router(tensors, **router_kwargs)

    ops = APIRouter()
    register_op_routes(ops)
    app.include_router(ops, **router_kwargs)

    graphs = APIRouter()
    register_graph_routes(graphs)
    app.include_router(graphs, **router_kwargs)

    training = APIRouter()
    register_training_routes(training)
    app.include_router(training, **router_kwargs)

    verification = APIRouter()
    register_validation_routes(verification)
    app.include_router(verification, **router_kwargs)
