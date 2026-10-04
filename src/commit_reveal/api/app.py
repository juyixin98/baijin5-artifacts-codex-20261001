"""FastAPI application factory.

Wires the store, clock, audit trail and state machine together, installs the
request-id middleware, and maps typed protocol errors to HTTP responses that
carry the failure *category* so clients can branch on it.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from commit_reveal.clock import Clock, SystemClock
from commit_reveal.config import Settings
from commit_reveal.protocol.errors import ProtocolError
from commit_reveal.state.audit import Audit
from commit_reveal.state.service import RoundService
from commit_reveal.state.store import Store


def create_app(settings: Settings, clock: Clock | None = None) -> FastAPI:
    logging.basicConfig(level=settings.log_level)
    app = FastAPI(title="commit-reveal-draw", version="0.1.0")

    db_path = Path(settings.database_path)
    if db_path.parent != Path(""):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    app.state.clock = clock or SystemClock()
    app.state.audit = Audit(store, app.state.clock)
    app.state.service = RoundService(store, app.state.clock, app.state.audit)
    app.state.settings = settings

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ProtocolError)
    async def protocol_error_handler(request: Request, exc: ProtocolError):
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "category": exc.category,
                    "message": str(exc),
                    "request_id": request.state.request_id,
                }
            },
        )

    from commit_reveal.api.routes import router

    app.include_router(router)
    return app


def get_service(request: Request) -> RoundService:
    return request.app.state.service


def get_request_id(request: Request) -> str:
    return request.state.request_id
