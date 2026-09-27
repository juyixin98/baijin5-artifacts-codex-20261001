"""FastAPI HTTP layer: request correlation, explicit error codes, explainable payloads."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import ENGINE_VERSION, SERVICE_VERSION
from .corpus import CorpusValidationError
from .logging_setup import configure_logging, get_logger, job_id_var, request_id_var
from .models import DatasetIn, JobCreate, ResumeIn
from .service import FimService, NotFoundError, serialise_job


def _error_body(code: str, message: str, details=None) -> dict:
    return {
        "error_code": code,
        "message": message,
        "request_id": request_id_var.get(),
        "engine_version": ENGINE_VERSION,
        "details": details,
    }


def create_app(service: FimService, log_level: str = "INFO") -> FastAPI:
    app = FastAPI(
        title="Closed Frequent Itemset Backend",
        version=SERVICE_VERSION,
        description="Vertical tid-index + closure-DFS mining with budgeted resumable enumeration.",
    )
    log = configure_logging(log_level)

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request_id_var.set(request_id)
        job_id_var.set("-")
        log.info("--> %s %s", request.method, request.url.path)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Engine-Version"] = ENGINE_VERSION
        log.info("<-- %s %s status=%d", request.method, request.url.path, response.status_code)
        return response

    # -- error handlers (single place, explicit categories) -----------------

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError):
        log.warning("error NOT_FOUND: %s", exc)
        return JSONResponse(status_code=404, content=_error_body("NOT_FOUND", str(exc)))

    @app.exception_handler(CorpusValidationError)
    async def _corpus_error(_: Request, exc: CorpusValidationError):
        log.warning("error INVALID_CORPUS: %s", exc)
        return JSONResponse(status_code=422, content=_error_body("INVALID_CORPUS", str(exc)))

    @app.exception_handler(RequestValidationError)
    async def _schema_error(_: Request, exc: RequestValidationError):
        log.warning("error VALIDATION_ERROR: %s", exc)
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "VALIDATION_ERROR",
                "request payload does not match the schema",
                details={"errors": exc.errors()},
            ),
        )

    # -- endpoints ----------------------------------------------------------

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service_version": SERVICE_VERSION,
            "engine_version": ENGINE_VERSION,
            "request_id": request_id_var.get(),
        }

    @app.post("/datasets", status_code=201)
    async def create_dataset(payload: DatasetIn):
        dataset_id, stats = service.ingest_dataset(
            payload.name, [r.model_dump() for r in payload.transactions]
        )
        record = service.get_dataset_or_raise(dataset_id)
        return {
            "dataset_id": dataset_id,
            "name": record.name,
            "content_hash": record.content_hash[:12],
            "engine_version": ENGINE_VERSION,
            "stats": stats.model_dump(),
            "request_id": request_id_var.get(),
        }

    @app.get("/datasets/{dataset_id}")
    async def read_dataset(dataset_id: str):
        try:
            record = service.get_dataset_or_raise(dataset_id)
        except NotFoundError as exc:
            return JSONResponse(status_code=404, content=_error_body("DATASET_NOT_FOUND", str(exc)))
        return {
            "dataset_id": record.dataset_id,
            "name": record.name,
            "content_hash": record.content_hash[:12],
            "engine_version": record.engine_version,
            "stats": {
                "transaction_count": record.transaction_count,
                "distinct_item_count": record.distinct_item_count,
                "empty_transaction_count": record.empty_transaction_count,
                "duplicate_transaction_count": record.duplicate_transaction_count,
            },
            "request_id": request_id_var.get(),
        }

    @app.post("/jobs", status_code=201)
    async def create_job(payload: JobCreate):
        try:
            record, chunk_evals = service.create_job(
                payload.dataset_id, payload.min_support, payload.budget, request_id_var.get()
            )
        except NotFoundError as exc:
            return JSONResponse(status_code=404, content=_error_body("DATASET_NOT_FOUND", str(exc)))
        job_id_var.set(record.job_id)
        return serialise_job(record, chunk_evals=chunk_evals)

    @app.post("/jobs/{job_id}/resume")
    async def resume_job(job_id: str, payload: ResumeIn):
        job_id_var.set(job_id)
        try:
            record, chunk_evals = service.resume_job(job_id, payload.budget, request_id_var.get())
        except NotFoundError as exc:
            return JSONResponse(status_code=404, content=_error_body("JOB_NOT_FOUND", str(exc)))
        return serialise_job(record, chunk_evals=chunk_evals)

    @app.get("/jobs/{job_id}")
    async def read_job(job_id: str):
        job_id_var.set(job_id)
        try:
            record = service.get_job_or_raise(job_id)
        except NotFoundError as exc:
            return JSONResponse(status_code=404, content=_error_body("JOB_NOT_FOUND", str(exc)))
        return serialise_job(record)

    return app
