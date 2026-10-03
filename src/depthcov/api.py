"""FastAPI verification interface.

Endpoints
---------
``GET  /healthz``                 liveness + configuration fingerprint
``POST /api/v1/analyze``          typed JSON alignments
``POST /api/v1/analyze/tsv``      raw TSV (parse failures -> undetermined)
``GET  /api/v1/runs/{run_id}``    read back a persisted run

Error semantics
---------------
* ``422`` malformed request body (schema validation) — nothing executed.
* ``400`` logically unusable request (e.g. duplicate reference names).
* Per-record problems (bad CIGAR, low MAPQ, out of bounds, unknown ref,
  duplicate flag, unparseable TSV row) are NOT HTTP errors: the record is
  returned under ``diagnostics`` as ``rejected`` or ``undetermined`` and
  excluded from coverage.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .config import Settings
from .engine import Reference, analyze, analyze_lines
from .models import Alignment, Strand
from .provenance import ProvenanceStore
from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnalyzeTsvRequest,
    CountSummary,
    ReferenceResultOut,
    SegmentOut,
)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


def _settings() -> Settings:
    # Re-read per process in tests; cheap and keeps env overrides honest.
    return Settings.from_env()


def _build_report_response(report) -> AnalyzeResponse:
    results = {
        name: ReferenceResultOut(
            ref_name=result.ref_name,
            ref_length=result.ref_length,
            per_base_depth=[int(x) for x in result.per_base_depth.tolist()],
            segments=[
                SegmentOut(start=s.start, end=s.end, depth=s.depth)
                for s in result.segments
            ],
            histogram={int(d): int(c) for d, c in result.histogram.items()},
            weighted_length=result.weighted_length,
            covered_bases=result.covered_bases,
            accepted_queries=list(result.accepted_queries),
        )
        for name, result in report.results.items()
    }
    counts = CountSummary(
        input=report.input_count,
        accepted=len(report.accepted),
        rejected=len(report.rejected),
        undetermined=len(report.undetermined),
    )
    return AnalyzeResponse(
        request_id=report.request_id,
        run_id=report.run_id,
        counts=counts,
        results=results,
        conservation=report.conservation(),
        external_sorted=report.external_sorted,
        diagnostics=report.diagnostics,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or _settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.store = ProvenanceStore(settings.db_path)
        try:
            yield
        finally:
            app.state.store.close()

    middleware = [Middleware(RequestIdMiddleware)]
    app = FastAPI(
        title="depthcov verification API",
        version="0.1.0",
        middleware=middleware,
        lifespan=lifespan,
    )

    def _references(payload) -> list[Reference]:
        names = [r.name for r in payload.references]
        if len(names) != len(set(names)):
            raise HTTPException(status_code=400, detail="duplicate reference names")
        return [Reference(name=r.name, length=r.length) for r in payload.references]

    @app.get("/healthz")
    def healthz(request: Request) -> dict:
        return {
            "status": "ok",
            "request_id": request.state.request_id,
            "dedup_policy": app.state.settings.dedup_policy,
            "min_mapq": app.state.settings.min_mapq,
        }

    @app.post("/api/v1/analyze", response_model=AnalyzeResponse)
    def post_analyze(payload: AnalyzeRequest, request: Request) -> AnalyzeResponse:
        opts = payload.options
        run_settings = Settings(
            min_mapq=opts.min_mapq,
            dedup_policy=opts.dedup_policy,
            reject_flagged_duplicates=opts.reject_flagged_duplicates,
            external_sort_chunk_size=settings.external_sort_chunk_size,
            use_external_sort_threshold=settings.use_external_sort_threshold,
            db_path=settings.db_path,
            log_level=settings.log_level,
        )
        alignments = [
            Alignment(
                query_name=a.query_name,
                ref_name=a.ref_name,
                ref_start=a.ref_start,
                cigar=a.cigar,
                mapq=a.mapq,
                query_length=a.query_length,
                strand=Strand(a.strand),
                read_group=a.read_group,
                is_duplicate=a.is_duplicate,
            )
            for a in payload.alignments
        ]
        report = analyze(
            alignments,
            _references(payload),
            run_settings,
            request_id=opts.request_id or request.state.request_id,
            store=app.state.store,
            use_external_sort=opts.use_external_sort,
        )
        return _build_report_response(report)

    @app.post("/api/v1/analyze/tsv", response_model=AnalyzeResponse)
    def post_analyze_tsv(
        payload: AnalyzeTsvRequest, request: Request
    ) -> AnalyzeResponse:
        opts = payload.options
        run_settings = Settings(
            min_mapq=opts.min_mapq,
            dedup_policy=opts.dedup_policy,
            reject_flagged_duplicates=opts.reject_flagged_duplicates,
            external_sort_chunk_size=settings.external_sort_chunk_size,
            use_external_sort_threshold=settings.use_external_sort_threshold,
            db_path=settings.db_path,
            log_level=settings.log_level,
        )
        report = analyze_lines(
            payload.tsv.splitlines(),
            _references(payload),
            run_settings,
            request_id=opts.request_id or request.state.request_id,
            store=app.state.store,
            use_external_sort=opts.use_external_sort,
        )
        return _build_report_response(report)

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        store: ProvenanceStore = app.state.store
        try:
            row = store.get_run(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown run_id {run_id}")
        return {
            "run_id": row["run_id"],
            "request_id": row["request_id"],
            "created_at": row["created_at"],
            "dedup_policy": row["dedup_policy"],
            "min_mapq": row["min_mapq"],
            "status": row["status"],
            "input_count": row["input_count"],
            "accepted_count": row["accepted_count"],
            "rejected_count": row["rejected_count"],
            "undetermined_count": row["undetermined_count"],
        }

    return app


app = create_app()
