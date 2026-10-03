"""FastAPI verification interface.

Endpoints
---------
``GET  /health``                      service/version status
``POST /api/v1/runs``                 index a synthetic reference
``GET  /api/v1/runs``                 list indexed runs
``GET  /api/v1/runs/{run_id}``        run provenance
``POST /api/v1/query``                candidate-location lookup for a read
``GET  /api/v1/runs/{run_id}/audit``  append-only audit trail

Error semantics: every failure returns HTTP non-2xx with
``{"ok": false, "error": {"code": <stable ErrorCode>, ...}}``; unexpected
exceptions become ``INTERNAL_ERROR`` (500), never a success envelope.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import LOG_DIR, Settings
from .errors import HTTP_STATUS, ErrorCode, MiniseedError
from .logging_setup import configure_logger, log_event, log_versions
from .schemas import (
    IndexRequest,
    IndexResponse,
    QueryRequest,
    QueryResponse,
    CandidateLocationOut,
)
from .service import MiniseedService, new_request_id
from .store import SeedStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logger = configure_logger(LOG_DIR / "miniseed.log")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        startup_id = new_request_id()
        log_versions(logger, startup_id)
        app.state.settings = settings
        app.state.store = SeedStore(settings.db_path)
        app.state.service = MiniseedService(app.state.store, settings)
        log_event(
            logger,
            step="db_open",
            verdict="OK",
            identity=startup_id,
            db_path=settings.db_path,
            k=settings.k,
            w=settings.w,
        )
        try:
            yield
        finally:
            app.state.store.close()

    app = FastAPI(
        title="miniseed",
        version="1.0.0",
        description="Minimizer seed index and candidate-location lookup",
        lifespan=lifespan,
    )

    def svc(req: Request) -> MiniseedService:
        return req.app.state.service

    # --------------------------------------------------------- error handler
    @app.exception_handler(MiniseedError)
    async def _domain_error(request: Request, exc: MiniseedError):
        request_id = getattr(request.state, "request_id", "-")
        log_event(
            logger,
            step=request.url.path,
            verdict="ERROR",
            identity=request_id,
            level=logging.WARNING,
            code=exc.code.value,
            detail=exc.message,
        )
        body = exc.to_dict()
        body["request_id"] = request_id
        return JSONResponse(status_code=HTTP_STATUS[exc.code], content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", "-")
        log_event(
            logger,
            step=request.url.path,
            verdict="ERROR",
            identity=request_id,
            level=logging.WARNING,
            code=ErrorCode.INVALID_PARAMETER.value,
            detail="request schema validation failed",
        )
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "request_id": request_id,
                "error": {
                    "code": ErrorCode.INVALID_PARAMETER.value,
                    "message": "request schema validation failed",
                    "context": {"issues": exc.errors()},
                },
            },
        )

    @app.exception_handler(Exception)
    async def _unexpected_error(request: Request, exc: Exception):
        # Deliberately NOT swallowed as success: log full detail server-side,
        # return a stable INTERNAL_ERROR category to the client.
        request_id = getattr(request.state, "request_id", "-")
        log_event(
            logger,
            step=request.url.path,
            verdict="ERROR",
            identity=request_id,
            level=logging.ERROR,
            code=ErrorCode.INTERNAL_ERROR.value,
            detail=f"{type(exc).__name__}: {exc}",
        )
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "request_id": request_id,
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": "unexpected internal error",
                },
            },
        )

    @app.middleware("http")
    async def _correlation(request: Request, call_next):
        request.state.request_id = (
            request.headers.get("x-request-id") or new_request_id()
        )
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    # -------------------------------------------------------------- health
    @app.get("/health")
    async def health(request: Request):
        from .hashing import HASH_VERSION
        import numpy as np
        import platform

        runs = request.app.state.store.list_runs()
        log_event(
            logger,
            step="health",
            verdict="OK",
            identity=request.state.request_id,
            runs=len(runs),
        )
        return {
            "ok": True,
            "service": "miniseed",
            "version": "1.0.0",
            "hash_version": HASH_VERSION,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "run_count": len(runs),
        }

    # --------------------------------------------------------------- index
    @app.post("/api/v1/runs", response_model=IndexResponse)
    async def create_run(request: Request, payload: IndexRequest):
        rid = request.state.request_id
        log_event(
            logger,
            step="index_parse",
            verdict="START",
            identity=rid,
            run_id=payload.run_id,
            k=payload.k or request.app.state.settings.k,
            w=payload.w or request.app.state.settings.w,
        )
        result = svc(request).index_reference(
            payload.run_id,
            payload.reference,
            ref_name=payload.ref_name,
            k=payload.k,
            w=payload.w,
            overwrite=payload.overwrite,
        )
        request.app.state.store.audit(
            "index_reference",
            rid,
            {
                "run_id": result.run_id,
                "k": result.k,
                "w": result.w,
                "ref_length": result.ref_length,
                "seed_count": result.seed_count,
                "windows": result.windows,
            },
            run_id=result.run_id,
        )
        log_event(
            logger,
            step="index_done",
            verdict="OK",
            identity=rid,
            run_id=result.run_id,
            windows=result.windows,
            seeds=result.seed_count,
            distinct=result.distinct_seed_values,
            max_bucket=result.max_bucket_size,
        )
        return IndexResponse(request_id=rid, **result.__dict__)

    # --------------------------------------------------------------- query
    @app.post("/api/v1/query", response_model=QueryResponse)
    async def query(request: Request, payload: QueryRequest):
        rid = request.state.request_id
        log_event(
            logger,
            step="query_scan",
            verdict="START",
            identity=rid,
            run_id=payload.run_id,
            k=payload.k or request.app.state.settings.k,
            w=payload.w or request.app.state.settings.w,
        )
        result = svc(request).query_read(
            payload.run_id, payload.read, k=payload.k, w=payload.w
        )
        request.app.state.store.audit(
            "query_read",
            rid,
            {
                "run_id": result.run_id,
                "query_length": result.query_length,
                "query_seeds": result.query_seed_count,
                "total_hits": result.total_hits,
                "locations": len(result.locations),
            },
            run_id=result.run_id,
        )
        log_event(
            logger,
            step="query_done",
            verdict="OK",
            identity=rid,
            run_id=result.run_id,
            query_seeds=result.query_seed_count,
            hits=result.total_hits,
            locations=len(result.locations),
            basis="minimizer overlap; candidate not alignment",
        )
        return QueryResponse(
            request_id=rid,
            **{
                **result.__dict__,
                "locations": [
                    CandidateLocationOut(**loc.__dict__) for loc in result.locations
                ],
            },
        )

    # ----------------------------------------------------------------- runs
    @app.get("/api/v1/runs")
    async def list_runs(request: Request):
        rows = request.app.state.store.list_runs()
        return {
            "ok": True,
            "request_id": request.state.request_id,
            "runs": [dict(r) for r in rows],
        }

    @app.get("/api/v1/runs/{run_id}")
    async def get_run(request: Request, run_id: str):
        row = request.app.state.store.get_run(run_id)
        buckets = request.app.state.store.bucket_sizes(run_id)
        return {
            "ok": True,
            "request_id": request.state.request_id,
            "run": dict(row),
            "bucket_count": len(buckets),
        }

    @app.get("/api/v1/runs/{run_id}/audit")
    async def run_audit(request: Request, run_id: str):
        request.app.state.store.get_run(run_id)
        rows = request.app.state.store.list_audit(run_id)
        return {
            "ok": True,
            "request_id": request.state.request_id,
            "events": [dict(r) for r in rows],
        }

    return app


app = create_app()
