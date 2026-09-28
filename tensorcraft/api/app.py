"""FastAPI application wiring: state, request identity, error mapping."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__
from ..config import Config
from ..errors import TensorCraftError
from ..state import GraphStore, TensorStore, TrainerStore
from .logging import configure_logging, set_request_id

# Category -> HTTP status. Anything unlisted defaults to 422.
_STATUS_BY_CATEGORY: dict[str, int] = {
    "INDEX_OUT_OF_BOUNDS": 400,
    "INVALID_INDEX": 400,
    "OVERLAPPING_WRITE": 409,
    "STORAGE_LIMIT_EXCEEDED": 413,
    "CONFIG_ERROR": 500,
}


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or \
            f"req-{uuid.uuid4().hex[:12]}"
        set_request_id(request_id)
        request.state.tc_request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _error_body(request: Request, category: str, message: str,
                details: dict | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"category": category, "message": message,
                  "details": details or {}},
        "request_id": request.state.tc_request_id,
    }


def create_app(config: Config) -> FastAPI:
    app = FastAPI(
        title="TensorCraft",
        version=__version__,
        description=(
            "Backend for slicing, transposing, reshaping and basic "
            "arithmetic on contiguous and non-contiguous stride-layout "
            "tensors."),
    )
    app.state.config = config
    app.state.tensors = TensorStore(
        config.storage.max_tensors, config.storage.max_elements)
    app.state.graphs = GraphStore(config.storage.max_graphs)
    app.state.trainers = TrainerStore(128)
    app.state.logger = configure_logging()
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(TensorCraftError)
    async def tensorcraft_error_handler(
            request: Request, exc: TensorCraftError) -> JSONResponse:
        status = _STATUS_BY_CATEGORY.get(exc.category, 422)
        if exc.details.get("not_found"):
            # Caller explicitly marks this as a missing resource.
            status = 404
        app.state.logger.warning(
            "%s %s -> %s %s: %s",
            request.method, request.url.path, status,
            exc.category, exc.message)
        return JSONResponse(
            status_code=status,
            content=_error_body(request, exc.category, exc.message,
                                exc.details))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
            request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(
                request, "INVALID_REQUEST",
                "request payload failed validation",
                {"errors": exc.errors()}))

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        app.state.logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error_body(request, "INTERNAL_ERROR", str(exc)))

    from .routes import register_routes
    register_routes(app)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "tensorcraft",
            "version": __version__,
            "config": config.source_path,
            "tensors": len(app.state.tensors),
            "graphs": len(app.state.graphs),
            "sessions": len(app.state.trainers),
        }

    return app
