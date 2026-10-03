"""FastAPI validation surface.

Endpoints:
  GET  /health                      -> liveness + component versions
  GET  /v1/runs                     -> list runs (summary rows)
  POST /v1/runs                     -> execute a run from FASTA text
  GET  /v1/runs/{run_id}            -> run detail incl. columns & maps
  GET  /v1/runs/{run_id}/columns    -> per-column results only

Error contract: typed domain errors become 4xx responses with a stable
``error.category``; unknown exceptions become 500 ``internal_error``.
Nothing is coerced into a 200 on failure.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import AppConfig, load_config
from ..domain.types import AlignmentResult
from ..errors import (
    AlignmentShapeError,
    FastaParseError,
    InsufficientDataError,
    InvalidResidueError,
    MsaBackendError,
    RunNotFoundError,
)
from ..logging_utils import configure_logging, get_logger
from ..pipeline import component_versions, run_pipeline
from ..provenance.db import ProvenanceStore
from .schemas import (
    ColumnOut,
    ErrorResponse,
    HealthResponse,
    RunDetail,
    RunRequest,
    RunSummary,
)

logger = get_logger("api")

_CLIENT_ERROR_STATUS = {
    FastaParseError.category: 422,
    AlignmentShapeError.category: 422,
    InvalidResidueError.category: 422,
    InsufficientDataError.category: 422,
    RunNotFoundError.category: 404,
}


def _error_response(status_code: int, category: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"category": category, "message": message}},
    )


def _run_summary(row: dict) -> RunSummary:
    return RunSummary(
        run_id=row["run_id"],
        label=row["label"],
        status=row["status"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        input_sha256=row["input_sha256"],
        n_sequences=row["n_sequences"],
        n_columns=row["n_columns"],
        total_weight=row["total_weight"],
        config=json.loads(row["config_json"]),
        versions=json.loads(row["versions_json"]),
        error_category=row["error_category"],
        error_message=row["error_message"],
    )


def create_app(config: AppConfig | None = None, store: ProvenanceStore | None = None) -> FastAPI:
    config = config or load_config()
    configure_logging(config.log_level)
    store = store or ProvenanceStore(config.database_path)

    app = FastAPI(title=config.name, version=component_versions()["msa_backend"])
    app.state.config = config
    app.state.store = store

    @app.exception_handler(MsaBackendError)
    async def domain_error_handler(_request: Request, exc: MsaBackendError) -> JSONResponse:
        status = _CLIENT_ERROR_STATUS.get(exc.category, 500)
        logger.warning("api error category=%s message=%s", exc.category, exc)
        return _error_response(status, exc.category, str(exc))

    @app.exception_handler(Exception)
    async def unknown_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("api unhandled error: %s", exc)
        return _error_response(500, "internal_error", str(exc))

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", versions=component_versions())

    @app.get("/v1/runs", response_model=list[RunSummary])
    def list_runs() -> list[RunSummary]:
        return [_run_summary(row) for row in store.list_runs()]

    @app.post(
        "/v1/runs",
        response_model=RunDetail,
        responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
        status_code=201,
    )
    def create_run(request: RunRequest) -> RunDetail:
        run_id, result = run_pipeline(request.fasta, request.label, config, store)
        logger.info("api run=%s created label=%r", run_id, request.label)
        return _build_detail(store, run_id, result)

    @app.get(
        "/v1/runs/{run_id}",
        response_model=RunDetail,
        responses={404: {"model": ErrorResponse}},
    )
    def get_run(run_id: str) -> RunDetail:
        return _build_detail(store, run_id, None)

    @app.get(
        "/v1/runs/{run_id}/columns",
        response_model=list[ColumnOut],
        responses={404: {"model": ErrorResponse}},
    )
    def get_columns(run_id: str) -> list[ColumnOut]:
        return [_column_out(row) for row in store.get_columns(run_id)]

    return app


def _column_out(row: dict) -> ColumnOut:
    return ColumnOut(
        column_index=row["column_index"],
        distribution=json.loads(row["distribution_json"]),
        entropy_bits=row["entropy_bits"],
        information_content_bits=row["information_content_bits"],
        effective_coverage=row["effective_coverage"],
        gap_fraction=row["gap_fraction"],
        consensus=row["consensus"],
        status=row["status"],
    )


def _build_detail(
    store: ProvenanceStore, run_id: str, result: AlignmentResult | None
) -> RunDetail:
    run_row = store.get_run(run_id)  # raises RunNotFoundError -> 404
    if result is not None:
        columns = [
            ColumnOut(
                column_index=c.column_index,
                distribution=c.distribution,
                entropy_bits=c.entropy_bits,
                information_content_bits=c.information_content_bits,
                effective_coverage=c.effective_coverage,
                gap_fraction=c.gap_fraction,
                consensus=c.consensus,
                status=c.status,
            )
            for c in result.columns
        ]
        coordinate_maps = {k: list(v) for k, v in result.coordinate_maps.items()}
    else:
        columns = [_column_out(row) for row in store.get_columns(run_id)]
        coordinate_maps = store.get_coordinate_maps(run_id)
    return RunDetail(
        run=_run_summary(run_row),
        weights=store.get_weights(run_id),
        columns=columns,
        coordinate_maps=coordinate_maps,
    )


app = create_app()
