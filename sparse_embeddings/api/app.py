"""FastAPI application wiring.

Error mapping is explicit:

* :class:`SparseEmbeddingError` -> HTTP 4xx with its stable category,
* request-shape (pydantic) errors -> HTTP 422 ``validation_error``,
* anything else -> HTTP 500 ``internal_error`` (never a 200 success).

Empty batches are a successful *no-step* response (``stepped=false`` with the
``empty_batch_no_step`` reason), distinct from an error.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from sparse_embeddings import __version__
from sparse_embeddings.api.schemas import (
    ApplyBatchRequest,
    CreateTableRequest,
    RowsRequest,
)
from sparse_embeddings.config import ServiceConfig
from sparse_embeddings.observability import StructuredLogger, new_run_id
from sparse_embeddings.persistence import CheckpointStore
from sparse_embeddings.state import SparseOptimizerService
from sparse_embeddings.tensor_types import ErrorCategory, SparseEmbeddingError

_HTTP_STATUS = {
    ErrorCategory.CONFIG_ERROR: 400,
    ErrorCategory.VALIDATION_ERROR: 422,
    ErrorCategory.INDEX_OUT_OF_RANGE: 400,
    ErrorCategory.NUMERIC_ERROR: 422,
    ErrorCategory.NOT_FOUND: 404,
    ErrorCategory.STATE_CONFLICT: 409,
    ErrorCategory.PERSISTENCE_ERROR: 500,
    ErrorCategory.EMPTY_BATCH: 200,
    ErrorCategory.INTERNAL_ERROR: 500,
}


def create_app(
    *,
    config: ServiceConfig | None = None,
    service: SparseOptimizerService | None = None,
) -> FastAPI:
    config = config or ServiceConfig.from_env()
    config.ensure_dirs()
    logger = StructuredLogger(config.log_path, stderr=config.log_to_stderr)
    store = CheckpointStore(config.data_dir / "checkpoints")
    app = FastAPI(
        title="Sparse Embedding Optimizer Service",
        version=__version__,
        description="Sparse gradient accumulation + clipping + optimizer steps.",
    )
    app.state.service = service or SparseOptimizerService(logger=logger, store=store)
    app.state.logger = logger
    service = app.state.service

    @app.exception_handler(SparseEmbeddingError)
    async def _domain_error_handler(request: Request, exc: SparseEmbeddingError) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None)
        service.logger.event(
            "http_error",
            run_id=run_id,
            verdict="error",
            path=request.url.path,
            **exc.to_dict(),
        )
        return JSONResponse(
            status_code=_HTTP_STATUS.get(exc.category, 500),
            content={"ok": False, "error": exc.to_dict(), "run_id": run_id},
        )

    @app.exception_handler(RequestValidationError)
    async def _request_shape_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None)
        service.logger.event(
            "http_error",
            run_id=run_id,
            verdict="error",
            path=request.url.path,
            category=ErrorCategory.VALIDATION_ERROR.value,
            validation_detail=exc.errors(),
        )
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "error": {
                    "category": ErrorCategory.VALIDATION_ERROR.value,
                    "message": "request body failed schema validation",
                    "details": {"errors": exc.errors()},
                },
                "run_id": run_id,
            },
        )

    @app.exception_handler(Exception)
    async def _unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None)
        # Last-resort guard: unknown state is reported as internal_error,
        # never as success.
        service.logger.event(
            "http_internal_error",
            run_id=run_id,
            verdict="internal_error",
            path=request.url.path,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {
                    "category": ErrorCategory.INTERNAL_ERROR.value,
                    "message": f"{type(exc).__name__}: {exc}",
                    "details": {},
                },
                "run_id": run_id,
            },
        )

    @app.middleware("http")
    async def _attach_run_id(request: Request, call_next):
        request.state.run_id = new_run_id()
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "numpy": np.__version__}

    @app.post("/tables")
    async def create_table(req: CreateTableRequest) -> dict[str, Any]:
        cfg = req.to_config()
        table = service.create_table(cfg)
        return {
            "ok": True,
            "table": cfg.name,
            "vocab_size": cfg.vocab_size,
            "dim": cfg.dim,
            "global_step": table.global_step,
        }

    @app.post("/tables/{name}/batches")
    async def apply_batch(name: str, req: ApplyBatchRequest, request: Request) -> dict[str, Any]:
        # Correlate even validation failures with the caller's run id.
        if req.run_id:
            request.state.run_id = req.run_id
        run_id = req.run_id or new_run_id()
        table = service.get_table(name)
        batch = req.to_batch(vocab_size=table.config.vocab_size, dim=table.config.dim)
        result = service.apply_batch(name, batch, run_id=run_id)
        return {"ok": True, "run_id": run_id, "result": _result_to_json(result)}

    @app.get("/tables/{name}")
    async def get_table(name: str) -> dict[str, Any]:
        table = service.get_table(name)
        cfg = table.config
        return {
            "ok": True,
            "table": name,
            "vocab_size": cfg.vocab_size,
            "dim": cfg.dim,
            "global_step": table.global_step,
            "optimizer": cfg.optimizer.name,
            "clip_mode": cfg.clipping.mode if cfg.clipping else None,
            "rows_ever_touched": int(np.count_nonzero(table.ever_touched)),
        }

    @app.post("/tables/{name}/rows/query")
    async def query_rows(name: str, req: RowsRequest) -> dict[str, Any]:
        table = service.get_table(name)
        idx = np.asarray(req.indices, dtype=np.int64)
        bad = idx[(idx < 0) | (idx >= table.config.vocab_size)]
        if bad.size:
            raise SparseEmbeddingError(
                ErrorCategory.INDEX_OUT_OF_RANGE,
                f"row index {int(bad[0])} outside [0, {table.config.vocab_size})",
                details={"bad_index": int(bad[0])},
            )
        return {
            "ok": True,
            "indices": idx.tolist(),
            "weights": table.weights[idx].tolist(),
            "momentum": table.momentum[idx].tolist(),
            "row_steps": table.row_steps[idx].tolist(),
        }

    return app


def _result_to_json(result) -> dict[str, Any]:
    return {
        "table": result.table,
        "stepped": result.stepped,
        "reason": result.reason,
        "global_step_before": result.global_step_before,
        "global_step_after": result.global_step_after,
        "nnz": result.nnz,
        "n_unique_touched": result.n_unique_touched,
        "touched_indices": result.touched_indices.tolist(),
        "token_counts": result.token_counts.tolist(),
        "zero_gradient_rows": result.zero_gradient_rows.tolist(),
        "clip_mode": result.clip_mode,
        "pre_clip_global_norm": result.pre_clip_global_norm,
        "post_clip_global_norm": result.post_clip_global_norm,
        "clip_applied": result.clip_applied,
        "per_row_scales": result.per_row_scales.tolist(),
    }


app = create_app()
