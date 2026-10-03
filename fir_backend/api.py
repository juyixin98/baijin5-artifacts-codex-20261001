"""FastAPI surface for the FIR estimation backend.

Endpoints:
- GET  /health
- POST /v1/estimate                      one-shot estimation
- POST /v1/streams                       create a streaming session
- POST /v1/streams/{id}/blocks           append a sample block
- POST /v1/streams/{id}/seal             close the stream to new blocks
- POST /v1/streams/{id}/estimate         estimate from the sealed stream

Error mapping (stable across endpoints):
- input_error        -> 400
- state_conflict     -> 409
- resource_exhausted -> 413
- computation_failure-> 500
Unknown stream id    -> 404
Every error body carries the category, message and a run id so the
failure can be replayed from the run log.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .contracts import DEFAULT_MAX_SAMPLES, EstimateParams
from .errors import ErrorCategory, FirBackendError
from .estimator import EstimateResult, estimate_fir
from .runlog import RunLogger, new_run_id
from .stream import StreamRegistry

_STATUS_BY_CATEGORY = {
    ErrorCategory.INPUT: 400,
    ErrorCategory.STATE: 409,
    ErrorCategory.RESOURCE: 413,
    ErrorCategory.COMPUTATION: 500,
}


class UnknownStreamError(FirBackendError):
    """Stream id not present in the registry (mapped to HTTP 404)."""

    category = ErrorCategory.INPUT


class EstimateRequestBody(BaseModel):
    excitation: list[float]
    response: list[float]
    model_order: int = Field(ge=1)
    delay: int = 0
    regularization: float = Field(default=0.0, ge=0.0)
    holdout_fraction: float = Field(default=0.25, ge=0.0, le=0.9)


class BlockBody(BaseModel):
    excitation: list[float]
    response: list[float]


class StreamEstimateBody(BaseModel):
    model_order: int = Field(ge=1)
    delay: int = 0
    regularization: float = Field(default=0.0, ge=0.0)
    holdout_fraction: float = Field(default=0.25, ge=0.0, le=0.9)


def _params_from(body: EstimateRequestBody | StreamEstimateBody) -> EstimateParams:
    return EstimateParams(
        model_order=body.model_order,
        delay=body.delay,
        regularization=body.regularization,
        holdout_fraction=body.holdout_fraction,
    )


def _result_payload(result: EstimateResult) -> dict[str, Any]:
    diag = result.diagnostics
    return {
        "run_id": result.run_id,
        "coefficients": list(result.coefficients),
        "diagnostics": {
            "model_order": diag.model_order,
            "delay": diag.delay,
            "regularization": diag.regularization,
            "n_samples_aligned": diag.n_samples_aligned,
            "n_train": diag.n_train,
            "n_holdout": diag.n_holdout,
            "rank": diag.rank,
            "effective_rank": diag.effective_rank,
            "singular_values": list(diag.singular_values),
            "condition_number": diag.condition_number,
            "identifiable": diag.identifiable,
            "unidentifiable_reasons": list(diag.unidentifiable_reasons),
            "train_rmse": diag.train_rmse,
            "holdout_rmse": diag.holdout_rmse,
        },
    }


def create_app(
    *,
    max_samples: int = DEFAULT_MAX_SAMPLES,
    logger: RunLogger | None = None,
) -> FastAPI:
    run_logger = logger or RunLogger()
    registry = StreamRegistry(max_samples=max_samples)
    app = FastAPI(title="fir-estimation-backend", version="0.1.0")
    app.state.run_logger = run_logger
    app.state.registry = registry

    @app.exception_handler(UnknownStreamError)
    async def unknown_stream_handler(
        _request: Request, exc: UnknownStreamError
    ) -> JSONResponse:
        run_id = new_run_id()
        run_logger.log(
            run_id, "request_failed", category="unknown_stream", message=exc.message
        )
        return JSONResponse(
            status_code=404,
            content={"error": {**exc.to_dict(), "run_id": run_id}},
        )

    @app.exception_handler(FirBackendError)
    async def backend_error_handler(_request: Request, exc: FirBackendError) -> JSONResponse:
        run_id = new_run_id()
        run_logger.log(
            run_id,
            "request_failed",
            category=exc.category.value,
            message=exc.message,
            detail=exc.detail,
        )
        return JSONResponse(
            status_code=_STATUS_BY_CATEGORY[exc.category],
            content={"error": {**exc.to_dict(), "run_id": run_id}},
        )

    @app.exception_handler(RequestValidationError)
    async def schema_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        run_id = new_run_id()
        run_logger.log(
            run_id,
            "request_failed",
            category=ErrorCategory.INPUT.value,
            message="request schema validation failed",
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "category": ErrorCategory.INPUT.value,
                    "message": "request schema validation failed",
                    "detail": {"errors": exc.errors()},
                    "run_id": run_id,
                }
            },
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/estimate")
    async def estimate_one_shot(body: EstimateRequestBody) -> dict[str, Any]:
        result = estimate_fir(
            body.excitation,
            body.response,
            _params_from(body),
            max_samples=max_samples,
            logger=run_logger,
        )
        return _result_payload(result)

    @app.post("/v1/streams", status_code=201)
    async def create_stream() -> dict[str, str]:
        session = registry.create()
        run_logger.log(new_run_id(), "stream_created", stream_id=session.stream_id)
        return {"stream_id": session.stream_id, "state": session.state.value}

    def _get_stream(stream_id: str):
        session = registry.get(stream_id)
        if session is None:
            raise UnknownStreamError(
                f"unknown stream id {stream_id!r}",
                detail={"stream_id": stream_id},
            )
        return session

    @app.post("/v1/streams/{stream_id}/blocks")
    async def append_block(stream_id: str, body: BlockBody) -> dict[str, Any]:
        session = _get_stream(stream_id)
        n = session.append(body.excitation, body.response)
        run_logger.log(
            new_run_id(), "stream_block_appended", stream_id=stream_id, n_samples=n
        )
        return {"stream_id": stream_id, "n_samples": n, "state": session.state.value}

    @app.post("/v1/streams/{stream_id}/seal")
    async def seal_stream(stream_id: str) -> dict[str, str]:
        session = _get_stream(stream_id)
        state = session.seal()
        run_logger.log(new_run_id(), "stream_sealed", stream_id=stream_id)
        return {"stream_id": stream_id, "state": state.value}

    @app.post("/v1/streams/{stream_id}/estimate")
    async def estimate_stream(stream_id: str, body: StreamEstimateBody) -> dict[str, Any]:
        session = _get_stream(stream_id)
        result = session.estimate(_params_from(body), logger=run_logger)
        return _result_payload(result)

    return app


app = create_app()
