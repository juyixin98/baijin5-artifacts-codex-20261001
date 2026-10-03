"""FastAPI validation interface and service entry point.

Error semantics:
  201  run created and completed (record-level rejects are data, not errors)
  400  request-level validation failure (bad reference, bad config, bad CIGAR
       at the schema level is NOT possible — CIGAR problems are record-level
       and land in the decisions audit, never in an HTTP error)
  404  unknown run_id
  413  too many records in one request
  422  malformed JSON body (FastAPI/pydantic schema validation)
  500  unexpected failure; body carries the request_id for log correlation

Every response carries X-Request-Id; the same id prefixes all log lines
emitted while handling the request.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import MAX_RECORDS_PER_REQUEST, FilterConfig, PipelineConfig
from .diagnostics import get_logger, new_request_id
from .errors import CoverageError, InputTooLargeError
from .models import AlignmentRecord
from .pipeline import ConservationError, input_digest, run_pipeline
from .provenance import ProvenanceStore


class AlignmentIn(BaseModel):
    read_id: str = Field(min_length=1, max_length=256)
    start: int = Field(ge=0)
    mapq: int | None = Field(default=None, ge=0, le=255)
    flags: int = Field(default=0, ge=0)
    cigar: str = Field(min_length=1, max_length=1024)


class ReferenceIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    length: int = Field(gt=0)


class FilterIn(BaseModel):
    min_mapq: int = Field(default=20, ge=0, le=255)
    exclude_secondary: bool = True
    exclude_supplementary: bool = True
    exclude_qc_fail: bool = True
    exclude_duplicates: bool = True


class RunRequest(BaseModel):
    reference: ReferenceIn
    alignments: list[AlignmentIn] = Field(max_length=MAX_RECORDS_PER_REQUEST)
    filter: FilterIn = FilterIn()
    sort_chunk_size: int = Field(default=50_000, ge=1)

    @field_validator("alignments")
    @classmethod
    def _non_empty(cls, value):
        if not value:
            raise ValueError("alignments must contain at least one record")
        return value


def create_app(db_path: str = "coverage_provenance.db") -> FastAPI:
    app = FastAPI(title="coverage-depth", version="0.1.0")
    store = ProvenanceStore(db_path)
    log = get_logger("coverage_depth.api")

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = new_request_id()
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(CoverageError)
    async def coverage_error_handler(request: Request, exc: CoverageError):
        status = 413 if isinstance(exc, InputTooLargeError) else 400
        return JSONResponse(
            status_code=status,
            content={"error": type(exc).__name__, "detail": str(exc)},
        )

    @app.exception_handler(ConservationError)
    async def conservation_error_handler(request: Request, exc: ConservationError):
        log.error("conservation check failed", extra={"context": {"detail": str(exc)}})
        return JSONResponse(
            status_code=500,
            content={"error": "ConservationError", "detail": str(exc)},
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/v1/coverage/runs", status_code=201)
    def create_run(body: RunRequest):
        if len(body.alignments) > MAX_RECORDS_PER_REQUEST:
            raise InputTooLargeError(
                f"{len(body.alignments)} records exceeds limit {MAX_RECORDS_PER_REQUEST}"
            )
        records = [
            AlignmentRecord(
                record_index=i,
                read_id=a.read_id,
                ref=body.reference.name,
                start=a.start,
                mapq=a.mapq,
                flags=a.flags,
                cigar=a.cigar,
            )
            for i, a in enumerate(body.alignments)
        ]
        config = PipelineConfig(
            filter=FilterConfig(**body.filter.model_dump()),
            sort_chunk_size=body.sort_chunk_size,
        )
        result = run_pipeline(
            records, body.reference.name, body.reference.length, config
        )
        run_id = uuid.uuid4().hex[:16]
        store.save_run(
            run_id,
            body.reference.name,
            body.reference.length,
            config.to_dict(),
            input_digest(records, body.reference.name, body.reference.length),
            result,
        )
        return {"run_id": run_id, **store.get_run(run_id)}

    @app.get("/v1/coverage/runs/{run_id}")
    def get_run(run_id: str):
        run = store.get_run(run_id)
        if run is None:
            return JSONResponse(
                status_code=404,
                content={"error": "RunNotFound", "detail": f"unknown run_id {run_id!r}"},
            )
        run["segments"] = [
            {"start": s.start, "end": s.end, "depth": s.depth}
            for s in store.get_segments(run_id)
        ]
        return run

    @app.get("/v1/coverage/runs/{run_id}/decisions")
    def get_decisions(run_id: str, status: str | None = None):
        if store.get_run(run_id) is None:
            return JSONResponse(
                status_code=404,
                content={"error": "RunNotFound", "detail": f"unknown run_id {run_id!r}"},
            )
        if status is not None and status not in ("ACCEPTED", "REJECTED", "UNDECIDABLE"):
            return JSONResponse(
                status_code=400,
                content={"error": "BadStatusFilter", "detail": f"invalid status {status!r}"},
            )
        return {"run_id": run_id, "decisions": store.get_decisions(run_id, status)}

    return app
