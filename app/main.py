"""Application factory: middleware, error mapping, route registration."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings
from app.diagnostics import (
    configure_logging,
    current_request_id,
    log_decision,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from app.errors import DomainError
from app.routes import router
from app.sessions import SessionRegistry

logger = logging.getLogger("pcv.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging()
    app = FastAPI(title=settings.service_name, version="0.1.0")
    app.state.settings = settings
    app.state.registry = SessionRegistry(settings)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = request.headers.get("x-request-id") or new_request_id()
        token = set_request_id(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            reset_request_id(token)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        decision = "undecidable" if exc.code == "SESSION_NOT_FOUND" else "rejected"
        log_decision(
            logger, decision, exc.message,
            code=exc.code, path=request.url.path, **exc.detail,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "detail": exc.detail,
                    "request_id": current_request_id(),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        log_decision(
            logger, "rejected", "request schema validation failed",
            code="SCHEMA_VALIDATION", path=request.url.path,
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "SCHEMA_VALIDATION",
                    "message": "request failed schema validation",
                    "detail": {"errors": exc.errors()},
                    "request_id": current_request_id(),
                }
            },
        )

    app.include_router(router)
    return app


app = create_app()
