"""FastAPI application: HTTP boundary, request identity, error mapping."""
from __future__ import annotations

import os
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .errors import MotifScanError
from .logging_config import configure_logging, get_request_logger
from .provenance import ProvenanceStore
from .schemas import (
    CalibrateRequest,
    CalibrateResponse,
    ErrorResponse,
    ScanRequest,
    ScanResponse,
    ValidateRequest,
    ValidateResponse,
)
from .service import run_calibrate, run_scan, run_validate, versions

DEFAULT_DB_PATH = "motifscan.db"


def create_app(db_path: str | None = None) -> FastAPI:
    configure_logging()
    app = FastAPI(title="motifscan", version=__version__)
    store = ProvenanceStore(db_path or os.environ.get("MOTIFSCAN_DB_PATH", DEFAULT_DB_PATH))

    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        # Client-supplied ids are honoured so results can be correlated with
        # an upstream caller; otherwise a fresh uuid is minted.
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(MotifScanError)
    async def domain_error_handler(request: Request, exc: MotifScanError):
        body = ErrorResponse(
            request_id=getattr(request.state, "request_id", "-"),
            error=exc.to_dict(),
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def schema_error_handler(request: Request, exc: RequestValidationError):
        body = ErrorResponse(
            request_id=getattr(request.state, "request_id", "-"),
            error={
                "category": "request_schema_error",
                "message": "request body failed structural validation",
                "details": {"errors": exc.errors()},
            },
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/version")
    def version() -> dict:
        return versions()

    @app.post("/v1/scan", response_model=ScanResponse)
    def scan(req: ScanRequest, request: Request) -> ScanResponse:
        log = get_request_logger(request.state.request_id)
        log.info("step=received endpoint=/v1/scan sequences=%d", len(req.sequences))
        return run_scan(req, store, request.state.request_id, log)

    @app.post("/v1/calibrate", response_model=CalibrateResponse)
    def calibrate(req: CalibrateRequest, request: Request) -> CalibrateResponse:
        log = get_request_logger(request.state.request_id)
        log.info("step=received endpoint=/v1/calibrate")
        return run_calibrate(req, store, request.state.request_id, log)

    @app.post("/v1/validate", response_model=ValidateResponse)
    def validate(req: ValidateRequest, request: Request) -> ValidateResponse:
        return run_validate(req, request.state.request_id)

    @app.get("/v1/requests/{request_id}")
    def provenance(request_id: str):
        record = store.get(request_id)
        if record is None:
            return JSONResponse(
                status_code=404,
                content=ErrorResponse(
                    request_id=request_id,
                    error={
                        "category": "request_not_found",
                        "message": f"no provenance record for request id {request_id!r}",
                        "details": {},
                    },
                ).model_dump(),
            )
        return record

    return app


app = create_app()
