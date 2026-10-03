"""FastAPI application: routes, error mapping, run logging."""

from __future__ import annotations

import numpy as np
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import DEFAULT_SETTINGS, Settings
from app.dsp.fixtures import build_scenario
from app.dsp.lms import AdaptiveFilter, freeze_mask_from_intervals
from app.dsp.metrics import coefficient_error_norm, mse, snr_improvement_db
from app.errors import AppError, InputValidationError
from app.logging_utils import configure_logging, log_run, new_run_id
from app.schemas import (
    ChannelResponse,
    ChannelStateResponse,
    CreateChannelRequest,
    EvaluateRequest,
    EvaluateResponse,
    ProcessRequest,
    ProcessResponse,
)
from app.state.channels import ChannelStore


def create_app(settings: Settings = DEFAULT_SETTINGS) -> FastAPI:
    app = FastAPI(title="LMS/NLMS reference-noise backend", version="0.1.0")
    logger = configure_logging()
    store = ChannelStore(settings)
    app.state.store = store
    app.state.settings = settings

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None)
        log_run(
            logger,
            run_id or "-",
            "request rejected",
            category=exc.category,
            reason=exc.message,
            detail=exc.detail,
        )
        return JSONResponse(status_code=exc.http_status, content=exc.to_envelope(run_id))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        err = InputValidationError(
            "request failed schema validation",
            detail={
                "errors": [
                    {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg"), "type": e.get("type")}
                    for e in exc.errors()
                ]
            },
        )
        log_run(logger, "-", "request rejected", category=err.category, reason=err.message)
        return JSONResponse(status_code=err.http_status, content=err.to_envelope())

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "channels": len(store)}

    @app.post("/channels", status_code=201, response_model=ChannelResponse)
    async def create_channel(req: CreateChannelRequest) -> ChannelResponse:
        channel = store.create(
            algorithm=req.algorithm,
            filter_len=req.filter_len,
            mu=req.mu,
            eps=req.eps,
            channel_id=req.channel_id,
        )
        log_run(
            logger,
            "-",
            "channel created",
            channel_id=channel.channel_id,
            algorithm=req.algorithm,
            filter_len=req.filter_len,
            mu=req.mu,
            eps=req.eps,
        )
        return ChannelResponse(
            channel_id=channel.channel_id,
            algorithm=req.algorithm,
            filter_len=req.filter_len,
            mu=req.mu,
            eps=req.eps,
            samples_processed=0,
        )

    @app.get("/channels/{channel_id}/state", response_model=ChannelStateResponse)
    async def channel_state(channel_id: str) -> ChannelStateResponse:
        channel = store.get(channel_id)
        return ChannelStateResponse(
            channel_id=channel.channel_id,
            weights=channel.filter.weights.tolist(),
            buffer=channel.filter.buffer.tolist(),
            samples_processed=channel.samples_processed,
        )

    @app.delete("/channels/{channel_id}", status_code=204)
    async def delete_channel(channel_id: str) -> None:
        store.delete(channel_id)
        log_run(logger, "-", "channel deleted", channel_id=channel_id)

    @app.post("/channels/{channel_id}/process", response_model=ProcessResponse)
    async def process(channel_id: str, req: ProcessRequest, request: Request) -> ProcessResponse:
        run_id = new_run_id()
        request.state.run_id = run_id
        channel = store.get(channel_id)

        reference = np.asarray(req.reference, dtype=np.float64)
        primary = np.asarray(req.primary, dtype=np.float64)
        intervals = [(iv.start, iv.end) for iv in req.freeze_intervals]
        freeze_mask = freeze_mask_from_intervals(len(req.reference), intervals)

        result = channel.filter.process_block(reference, primary, freeze_mask)

        log_run(
            logger,
            run_id,
            "block processed",
            channel_id=channel_id,
            algorithm=channel.filter.algorithm,
            samples=len(req.reference),
            freeze_intervals=intervals,
            adapted_samples=result.adapted_samples,
            frozen_samples=result.frozen_samples,
            mean_reference_energy=result.mean_energy,
            min_denominator=result.min_denominator,
            weight_norm_before=result.weight_norm_before,
            weight_norm_after=result.weight_norm_after,
            rationale="adaptation frozen on requested intervals; NLMS denominator = eps + energy",
        )
        return ProcessResponse(
            run_id=run_id,
            channel_id=channel_id,
            samples=len(req.reference),
            adapted_samples=result.adapted_samples,
            frozen_samples=result.frozen_samples,
            output=result.output.tolist(),
            error=result.error.tolist(),
            min_denominator=result.min_denominator,
            mean_reference_energy=result.mean_energy,
            weight_norm_before=result.weight_norm_before,
            weight_norm_after=result.weight_norm_after,
        )

    @app.post("/evaluate", response_model=EvaluateResponse)
    async def evaluate(req: EvaluateRequest, request: Request) -> EvaluateResponse:
        run_id = new_run_id()
        request.state.run_id = run_id
        scenario = build_scenario(req.scenario, req.n_samples, req.seed)

        adaptive_filter = AdaptiveFilter(
            filter_len=req.filter_len,
            algorithm=req.algorithm,
            mu=req.mu,
            eps=req.eps,
            settings=settings,
        )
        intervals = [(iv.start, iv.end) for iv in req.freeze_intervals]
        freeze_mask = freeze_mask_from_intervals(req.n_samples, intervals)
        result = adaptive_filter.process_block(scenario.reference, scenario.primary, freeze_mask)

        snr_gain = snr_improvement_db(scenario.primary, result.error, scenario.clean)
        residual_mse = mse(result.error, scenario.clean)
        coeff_err = coefficient_error_norm(adaptive_filter.weights, scenario.true_coeffs)

        log_run(
            logger,
            run_id,
            "evaluation complete",
            scenario=req.scenario,
            algorithm=req.algorithm,
            n_samples=req.n_samples,
            seed=req.seed,
            mu=req.mu,
            freeze_intervals=intervals,
            snr_improvement_db=snr_gain,
            mse_residual_vs_clean=residual_mse,
            coefficient_error_norm=coeff_err,
            weight_norm_after=result.weight_norm_after,
            rationale="metrics computed against known clean signal and true plant, not output energy",
        )
        return EvaluateResponse(
            run_id=run_id,
            scenario=req.scenario,
            algorithm=req.algorithm,
            n_samples=req.n_samples,
            seed=req.seed,
            snr_improvement_db=snr_gain,
            mse_residual_vs_clean=residual_mse,
            coefficient_error_norm=coeff_err,
            final_weights=adaptive_filter.weights.tolist(),
            true_coeffs=scenario.true_coeffs.tolist(),
            meta=scenario.meta,
        )

    return app


app = create_app()
