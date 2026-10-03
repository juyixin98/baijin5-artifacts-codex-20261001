"""FastAPI surface for the LMS/NLMS backend.

Error contract: every failure returns
``{"error": {"kind", "reason", "message", "detail", "run_id"}}`` with the
HTTP status implied by ``kind`` (see app.errors).
"""

from __future__ import annotations

import numpy as np
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.algorithms import metrics
from app.algorithms.lms import FilterSpec
from app.config import Settings, get_settings
from app.contracts import (
    BlockRequest,
    BlockResponse,
    CreateStreamRequest,
    CreateStreamResponse,
    EvaluateRequest,
    EvaluateResponse,
    StateResponse,
)
from app.errors import AppError
from app.logging_utils import RunLog, new_run_id
from app.streams.session import StreamRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="lms-nlms-backend", version="0.1.0")
    app.state.settings = settings
    app.state.registry = StreamRegistry(settings)
    _register_error_handlers(app, settings)
    _register_lifecycle_routes(app, settings)
    _register_block_routes(app, settings)
    _register_evaluate_route(app, settings)
    return app


def _register_error_handlers(app: FastAPI, settings: Settings) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        run_id = new_run_id()
        RunLog(settings.log_dir, run_id).event(
            "error", kind=exc.kind, reason=exc.reason,
            message=exc.message, detail=exc.detail, path=request.url.path,
        )
        return JSONResponse(status_code=exc.http_status, content=exc.to_body(run_id))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        run_id = new_run_id()
        body = {
            "error": {
                "kind": "input_validation",
                "reason": "schema_violation",
                "message": "request failed schema validation",
                "detail": {"errors": exc.errors()},
                "run_id": run_id,
            }
        }
        RunLog(settings.log_dir, run_id).event(
            "error", kind="input_validation",
            reason="schema_violation", path=request.url.path,
        )
        return JSONResponse(status_code=422, content=body)


def _register_lifecycle_routes(app: FastAPI, settings: Settings) -> None:
    registry = app.state.registry

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/v1/streams", status_code=201)
    async def create_stream(req: CreateStreamRequest) -> CreateStreamResponse:
        run_id = new_run_id()
        spec = FilterSpec(
            algorithm=req.algorithm,
            filter_length=req.filter_length,
            mu=req.mu,
            epsilon=req.epsilon if req.epsilon is not None else settings.default_epsilon,
        )
        registry.create_stream(req.stream_id, spec, req.channels, req.frozen_until_index)
        RunLog(settings.log_dir, run_id).event(
            "stream_created", stream_id=req.stream_id, algorithm=spec.algorithm,
            filter_length=spec.filter_length, mu=spec.mu, epsilon=spec.epsilon,
            channels=req.channels, frozen_until_index=req.frozen_until_index,
        )
        return CreateStreamResponse(stream_id=req.stream_id, run_id=run_id)

    @app.get("/v1/streams/{stream_id}/channels/{channel_id}/state")
    async def channel_state(stream_id: str, channel_id: str) -> StateResponse:
        return StateResponse(**registry.channel_snapshot(stream_id, channel_id))

    @app.delete("/v1/streams/{stream_id}", status_code=204)
    async def drop_stream(stream_id: str) -> None:
        registry.drop_stream(stream_id)


def _register_block_routes(app: FastAPI, settings: Settings) -> None:
    registry = app.state.registry

    @app.post("/v1/streams/{stream_id}/channels/{channel_id}/blocks")
    async def process_block(
        stream_id: str, channel_id: str, req: BlockRequest
    ) -> BlockResponse:
        run_id = new_run_id()
        result = registry.process_block(
            stream_id, channel_id, req.start_index,
            np.asarray(req.reference, dtype=np.float64),
            np.asarray(req.desired, dtype=np.float64),
            freeze_adaptation=req.freeze_adaptation,
        )
        end_index = req.start_index + result.samples_processed
        RunLog(settings.log_dir, run_id).event(
            "block_processed", stream_id=stream_id, channel_id=channel_id,
            start_index=req.start_index, end_index=end_index,
            samples=result.samples_processed, frozen_samples=result.frozen_samples,
            weight_norm=result.weight_norm,
            error_rms=float(np.sqrt(np.mean(result.errors ** 2))),
        )
        return BlockResponse(
            run_id=run_id, stream_id=stream_id, channel_id=channel_id,
            start_index=req.start_index, end_index=end_index,
            samples_processed=result.samples_processed,
            frozen_samples=result.frozen_samples,
            weight_norm=result.weight_norm,
            outputs=result.outputs.tolist(), errors=result.errors.tolist(),
        )


def _register_evaluate_route(app: FastAPI, settings: Settings) -> None:
    @app.post("/v1/evaluate")
    async def evaluate(req: EvaluateRequest) -> EvaluateResponse:
        run_id = new_run_id()
        desired = np.asarray(req.desired, dtype=np.float64)
        residual = np.asarray(req.residual, dtype=np.float64)
        clean = np.asarray(req.clean, dtype=np.float64)
        mse = metrics.residual_mse(residual, clean)
        nr_db = metrics.noise_reduction_db(desired, residual, clean)
        coef_db = None
        if req.plant_weights is not None and req.estimated_weights is not None:
            coef_db = metrics.coefficient_error_db(
                np.asarray(req.estimated_weights, dtype=np.float64),
                np.asarray(req.plant_weights, dtype=np.float64),
            )
        success = bool(nr_db >= req.success_threshold_db)
        rationale = (
            f"noise_reduction_db={nr_db:.3f} vs threshold "
            f"{req.success_threshold_db:.3f} dB, measured against the known "
            f"clean signal; output-energy reduction is not used as a criterion"
        )
        RunLog(settings.log_dir, run_id).event(
            "evaluation", residual_mse_vs_clean=mse, noise_reduction_db=nr_db,
            coefficient_error_db=coef_db, success=success, rationale=rationale,
        )
        return EvaluateResponse(
            run_id=run_id, residual_mse_vs_clean=mse, noise_reduction_db=nr_db,
            coefficient_error_db=coef_db, success=success, rationale=rationale,
        )


app = create_app()
