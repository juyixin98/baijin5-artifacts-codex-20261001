"""FastAPI surface.

Every response carries the request identity (``request_id``, echoed from the
``X-Request-Id`` header or generated), and every result carries a ``trace``
of the key processing steps (versions used, candidate/confirm counts) so an
operator can explain what happened without reading values — values and index
digests never appear in responses other than the explicitly dev-gated
plaintext readback and index introspection endpoints.
"""
from __future__ import annotations

import os
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import load_settings
from ..errors import AppError
from ..service import SensitiveService
from .schemas import PutRecordRequest, QueryRequest, RotateRequest

DEFAULT_CONFIG_PATH = "config/settings.dev.json"


def create_app(config_path: str | None = None) -> FastAPI:
    path = config_path or os.environ.get("SENSITIVE_LAYER_CONFIG", DEFAULT_CONFIG_PATH)
    service = SensitiveService.from_settings(load_settings(path))
    app = FastAPI(title="sensitive-field-layer", version="1.0.0")
    app.state.service = service

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "ok": False,
                "request_id": getattr(request.state, "request_id", None),
                "error": {"category": exc.category, "message": exc.message},
            },
        )

    def _ok(request: Request, result: dict) -> dict:
        return {"ok": True, "request_id": request.state.request_id, "result": result}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.put("/records/{record_id}")
    def put_record(record_id: str, body: PutRecordRequest, request: Request):
        result = service.put_record(
            request_id=request.state.request_id, record_id=record_id,
            field=body.field, purpose=body.purpose, value=body.value)
        return _ok(request, result)

    @app.get("/records/{record_id}")
    def get_record(record_id: str, field: str, purpose: str, request: Request,
                   include_plaintext: bool = False):
        result = service.get_record(
            request_id=request.state.request_id, record_id=record_id,
            field=field, purpose=purpose, include_plaintext=include_plaintext)
        return _ok(request, result)

    @app.post("/query")
    def query(body: QueryRequest, request: Request):
        result = service.query(
            request_id=request.state.request_id, field=body.field,
            purpose=body.purpose, value=body.value)
        return _ok(request, result)

    @app.post("/admin/rotate-index-key")
    def rotate(body: RotateRequest, request: Request):
        result = service.begin_rotation(
            request_id=request.state.request_id, crash_after=body.crash_after)
        return _ok(request, result)

    @app.post("/admin/rotation/resume")
    def resume_rotation(request: Request):
        result = service.resume_rotation(request_id=request.state.request_id)
        return _ok(request, result)

    @app.get("/admin/rotation/status")
    def rotation_status(request: Request):
        return _ok(request, service.rotation_status())

    @app.get("/admin/indexes")
    def inspect_indexes(record_id: str, field: str, purpose: str, request: Request):
        result = service.inspect_indexes(
            record_id=record_id, field=field, purpose=purpose)
        return _ok(request, result)

    @app.get("/audit")
    def audit_entries(request: Request):
        return _ok(request, {"entries": service.audit.read_all()})

    return app


app = create_app()
