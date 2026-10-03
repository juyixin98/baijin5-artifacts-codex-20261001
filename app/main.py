"""FastAPI application exposing the SOS IIR filter service."""

from __future__ import annotations

import uuid

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import load_settings
from app.errors import FilterServiceError, SampleBlockError
from app.schemas import (
    CreateStreamRequest,
    ErrorResponse,
    ProcessBlockRequest,
    ProcessBlockResponse,
    StreamInfoResponse,
    UpdateCoefficientsRequest,
    UpdateCoefficientsResponse,
)
from app.state.store import StreamStore, StreamState


def _info(stream: StreamState) -> StreamInfoResponse:
    return StreamInfoResponse(
        stream_id=stream.stream_id,
        sample_rate=stream.sample_rate,
        num_channels=stream.num_channels,
        num_sections=stream.sos.n_sections,
        param_version=stream.param_version,
        max_pole_radius=stream.sos.max_pole_radius,
        samples_processed=stream.samples_processed,
        blocks_processed=stream.blocks_processed,
    )


def create_app(store: StreamStore | None = None) -> FastAPI:
    app = FastAPI(title="SOS IIR Filter Service", version="1.0.0")
    app.state.store = store or StreamStore(load_settings())

    @app.middleware("http")
    async def run_id_middleware(request: Request, call_next):
        # A per-request run id lets clients replay/report failures.
        request.state.run_id = uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Run-Id"] = request.state.run_id
        return response

    @app.exception_handler(FilterServiceError)
    async def service_error_handler(request: Request, exc: FilterServiceError):
        body = ErrorResponse(
            error={
                "category": exc.category,
                "code": exc.code,
                "message": exc.message,
                "run_id": getattr(request.state, "run_id", "unknown"),
                "context": exc.context,
            }
        )
        return JSONResponse(status_code=exc.http_status, content=body.model_dump())

    @app.post("/streams", response_model=StreamInfoResponse, status_code=201)
    def create_stream(req: CreateStreamRequest):
        stream = app.state.store.create_stream(
            sample_rate=req.sample_rate,
            num_channels=req.num_channels,
            sections=req.coefficients,
            initial_condition=req.initial_condition,
        )
        return _info(stream)

    @app.get("/streams/{stream_id}", response_model=StreamInfoResponse)
    def get_stream(stream_id: str):
        return _info(app.state.store.get(stream_id))

    @app.delete("/streams/{stream_id}", status_code=204)
    def delete_stream(stream_id: str):
        app.state.store.delete(stream_id)
        return None

    @app.post(
        "/streams/{stream_id}/blocks",
        response_model=ProcessBlockResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    def process_block(stream_id: str, req: ProcessBlockRequest):
        try:
            block = np.asarray(req.samples, dtype=np.float64)
        except (ValueError, TypeError) as exc:
            raise SampleBlockError(
                f"samples must be a rectangular (n_samples, n_channels) "
                f"matrix: {exc}"
            ) from exc
        stream, out = app.state.store.process_block(
            stream_id, block, expected_version=req.param_version
        )
        return ProcessBlockResponse(
            stream_id=stream.stream_id,
            param_version=stream.param_version,
            samples=out.tolist(),
            samples_processed=stream.samples_processed,
        )

    @app.put(
        "/streams/{stream_id}/coefficients",
        response_model=UpdateCoefficientsResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    def update_coefficients(stream_id: str, req: UpdateCoefficientsRequest):
        stream = app.state.store.update_coefficients(
            stream_id,
            req.coefficients,
            expected_version=req.expected_version,
            transient=req.transient,
        )
        return UpdateCoefficientsResponse(
            stream_id=stream.stream_id,
            param_version=stream.param_version,
            num_sections=stream.sos.n_sections,
            max_pole_radius=stream.sos.max_pole_radius,
        )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
