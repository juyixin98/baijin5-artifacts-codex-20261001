"""FastAPI transport: thin routers over the service state machine.

All blocking cryptography / SQLite / file work runs in the worker threadpool;
routers only validate transport shapes and translate domain errors into the
diagnostic envelope.
"""

from __future__ import annotations

import base64
import binascii
import time
from collections import deque
from functools import partial
from typing import Any, Deque

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from ..core.errors import ProtocolCodingError, SSEAError, StateError
from ..core.service import SegmentedAEADService
from . import schemas
from .deps import get_service

REQUEST_ID_HEADER = "x-request-id"


# ---------------------------------------------------------------------------
# Minimal per-client fixed-window rate limiter (local, in-process).
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, max_requests: int = 600, window_s: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_s = window_s
        self._hits: dict[str, Deque[float]] = {}
        self._calls = 0

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        bucket = self._hits.setdefault(client, deque())
        while bucket and now - bucket[0] > self.window_s:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            return False
        bucket.append(now)
        # Periodically evict empty buckets so the map cannot grow forever.
        self._calls += 1
        if self._calls % 256 == 0:
            stale = [k for k, v in self._hits.items()
                     if not v or now - v[-1] > self.window_s]
            for k in stale:
                self._hits.pop(k, None)
        return True


def _error_body(exc: SSEAError, request_id: str | None) -> dict[str, Any]:
    if exc.request_id is not None:
        request_id = exc.request_id
    return {
        "category": exc.category,
        "error": exc.message,
        "request_id": request_id,
        "state": exc.state,
    }


def create_app(service: SegmentedAEADService | None = None,
               rate_limiter: RateLimiter | None = None) -> FastAPI:
    app = FastAPI(
        title="Segmented AEAD Service",
        version="1.0.0",
        description="Authenticated, ordered, tamper-evident segment intake.",
    )
    limiter = rate_limiter or RateLimiter()

    def svc() -> SegmentedAEADService:
        return service if service is not None else get_service()

    @app.middleware("http")
    async def correlation_and_limits(request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or _new_request_id()
        request.state.request_id = request_id
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            client = request.client.host if request.client else "local"
            if not limiter.allow(client):
                return JSONResponse(
                    {"category": "rate_limited",
                     "error": "too many requests",
                     "request_id": request_id, "state": {}},
                    status_code=429,
                    headers={REQUEST_ID_HEADER: request_id})
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(SSEAError)
    async def domain_error_handler(request: Request, exc: SSEAError):
        return JSONResponse(
            _error_body(exc, getattr(request.state, "request_id", None)),
            status_code=exc.http_status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request,
                                  exc: RequestValidationError):
        return JSONResponse(
            {"category": "protocoding",
             "error": "request validation failed",
             "request_id": getattr(request.state, "request_id", None),
             "state": {"errors": exc.errors()}},
            status_code=422)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/streams/begin", response_model=schemas.StatusResponse,
              status_code=201)
    async def begin(req: schemas.BeginStreamRequest,
                    request: Request,
                    service: SegmentedAEADService = Depends(svc)):
        rid = request.state.request_id
        await anyio.to_thread.run_sync(
            service.begin_stream, req.message_id, req.total_segments,
            req.total_len, rid)
        return await anyio.to_thread.run_sync(service.status, req.message_id)

    @app.post("/v1/segments", response_model=schemas.AcceptanceResponse)
    async def submit_segment(req: schemas.SegmentRequest, request: Request,
                             service: SegmentedAEADService = Depends(svc)):
        rid = request.state.request_id
        try:
            frame = await anyio.to_thread.run_sync(
                partial(base64.b64decode, req.frame_b64, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise ProtocolCodingError("frame_b64 is not valid base64",
                                      request_id=rid) from exc
        acceptance = await anyio.to_thread.run_sync(service.submit, frame, rid)
        return acceptance

    @app.post("/v1/streams/{message_id}/finalize",
              response_model=schemas.AcceptanceResponse)
    async def finalize(message_id: str, request: Request,
                       service: SegmentedAEADService = Depends(svc)):
        return await anyio.to_thread.run_sync(
            service.finalize, message_id, request.state.request_id)

    @app.get("/v1/streams/{message_id}/status",
             response_model=schemas.StatusResponse)
    async def status(message_id: str,
                     service: SegmentedAEADService = Depends(svc)):
        return await anyio.to_thread.run_sync(service.status, message_id)

    @app.get("/v1/streams/{message_id}/result")
    async def result(message_id: str, request: Request,
                     service: SegmentedAEADService = Depends(svc)):
        # Released bytes only; never partial plaintext.
        info = await anyio.to_thread.run_sync(service.status, message_id)
        if not info["released"]:
            raise StateError(
                "plaintext not available: stream not fully authenticated",
                request_id=request.state.request_id,
                status=info["status"])
        path = await anyio.to_thread.run_sync(
            service.released_path, message_id)
        return FileResponse(
            path, media_type="application/octet-stream",
            filename=f"{message_id}.bin",
            headers={REQUEST_ID_HEADER: request.state.request_id})

    @app.post("/v1/streams/{message_id}/abort",
              response_model=schemas.StatusResponse)
    async def abort(message_id: str, request: Request,
                    service: SegmentedAEADService = Depends(svc)):
        rid = request.state.request_id
        await anyio.to_thread.run_sync(service.abort, message_id, rid)
        return await anyio.to_thread.run_sync(service.status, message_id)

    @app.get("/v1/audit", response_model=list[schemas.AuditEvent])
    async def audit(message_id: str | None = None, limit: int = 200,
                    service: SegmentedAEADService = Depends(svc)):
        limit = max(1, min(limit, 1000))
        return await anyio.to_thread.run_sync(
            service.audit_events, message_id, limit)

    return app


def _new_request_id() -> str:
    import uuid
    return f"req-{uuid.uuid4().hex[:16]}"


def create_app_from_env() -> FastAPI:  # pragma: no cover - uvicorn entry
    return create_app()
