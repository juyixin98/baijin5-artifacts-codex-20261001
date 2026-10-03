"""FastAPI application factory, middleware and error handling."""
from __future__ import annotations

import re
import uuid

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import ALGORITHM_NAME, ALGORITHM_VERSION, MODEL_SCOPE, __version__
from ..config import get_settings
from ..logging_setup import configure_logging, get_logger, request_id_ctx, set_request_id
from ..trace.db import Database
from .routes import create_router

logger = get_logger("api")

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.-]{0,79}$")


def _resolve_request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID")
    if supplied and _REQUEST_ID_PATTERN.match(supplied):
        return supplied
    return f"req_{uuid.uuid4().hex}"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    db = Database(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("application started")
        yield
        db.close()
        logger.info("application stopped")

    app = FastAPI(
        title="Nussinov Maximum Pairing Backend",
        version=__version__,
        description=(
            "Teaching combinatorial model for maximum non-crossing canonical "
            "base-pair count (Nussinov). Not a predictor of real RNA folding."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db

    @app.middleware("http")
    async def bind_request_id(request: Request, call_next):
        request_id = _resolve_request_id(request)
        request.state.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", "req_unknown")
        logger.warning("request schema validation failed: %s", exc.errors())
        return JSONResponse(
            status_code=422,
            headers={"X-Request-ID": request_id},
            content={
                "success": False,
                "request_id": request_id,
                "error": {
                    "category": "request_schema_error",
                    "message": "request body failed schema validation",
                    "details": {"errors": exc.errors()},
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "req_unknown")
        logger.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            headers={"X-Request-ID": request_id},
            content={
                "success": False,
                "request_id": request_id,
                "error": {
                    "category": "internal_error",
                    "message": "unexpected internal error",
                    "details": {"type": type(exc).__name__},
                },
            },
        )

    app.include_router(create_router())
    return app
