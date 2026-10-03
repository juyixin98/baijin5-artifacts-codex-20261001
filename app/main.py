"""FastAPI application: HTTP surface over the stream registry.

Error contract: every failure is returned as
``{"error": {"category", "message", "detail"}}`` with a status derived from
the category (see ``app.errors``). Request-schema failures raised by pydantic
are mapped to the same envelope with category ``input_error``.
"""

from __future__ import annotations

import numpy as np
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.dsp.coefficients import normalize_and_validate
from app.errors import FilterServiceError
from app.models import (
    ChunkRequest,
    ChunkResponse,
    CreateStreamRequest,
    CreateStreamResponse,
    StreamInfoResponse,
    UpdateCoefficientsRequest,
    UpdateCoefficientsResponse,
)
from app.streams import Stream, StreamRegistry, TransientPolicy


def create_app(registry: StreamRegistry | None = None) -> FastAPI:
    app = FastAPI(title="SOS Cascade IIR Filter Service", version="0.1.0")
    app.state.registry = registry or StreamRegistry()

    @app.exception_handler(FilterServiceError)
    async def service_error_handler(
        _request: Request, exc: FilterServiceError
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.envelope())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "category": "input_error",
                    "message": "request schema validation failed",
                    "detail": {"errors": exc.errors()},
                }
            },
        )

    def info(stream: Stream) -> StreamInfoResponse:
        return StreamInfoResponse(
            stream_id=stream.stream_id,
            n_channels=stream.n_channels,
            n_sections=stream.sos.n_sections,
            param_version=stream.param_version,
            transient=stream.transient.value,
            samples_processed=stream.samples_processed,
            chunks_processed=stream.chunks_processed,
            max_pole_radius=stream.sos.max_pole_radius,
            sample_rate=stream.sample_rate,
        )

    @app.post("/streams", status_code=201, response_model=CreateStreamResponse)
    def create_stream(body: CreateStreamRequest) -> CreateStreamResponse:
        sos = normalize_and_validate(body.sos)
        stream = app.state.registry.create(
            sos,
            n_channels=body.n_channels,
            transient=TransientPolicy(body.transient),
            sample_rate=body.sample_rate,
        )
        return CreateStreamResponse(
            stream_id=stream.stream_id, param_version=stream.param_version
        )

    @app.get("/streams/{stream_id}", response_model=StreamInfoResponse)
    def get_stream(stream_id: str) -> StreamInfoResponse:
        return info(app.state.registry.get(stream_id))

    @app.post("/streams/{stream_id}/chunks", response_model=ChunkResponse)
    def process_chunk(stream_id: str, body: ChunkRequest) -> ChunkResponse:
        registry = app.state.registry
        stream = registry.get(stream_id)
        registry.check_version(stream, body.expected_param_version)
        y = stream.filter.process(np.asarray(body.samples, dtype=np.float64))
        stream.samples_processed += y.shape[1]
        stream.chunks_processed += 1
        return ChunkResponse(
            samples=y.tolist(),
            param_version=stream.param_version,
            samples_processed=stream.samples_processed,
            chunks_processed=stream.chunks_processed,
        )

    @app.put(
        "/streams/{stream_id}/coefficients",
        response_model=UpdateCoefficientsResponse,
    )
    def update_coefficients(
        stream_id: str, body: UpdateCoefficientsRequest
    ) -> UpdateCoefficientsResponse:
        sos = normalize_and_validate(body.sos)
        stream = app.state.registry.update_coefficients(
            stream_id,
            sos,
            transient=(
                TransientPolicy(body.transient) if body.transient else None
            ),
            expected_version=body.expected_param_version,
        )
        return UpdateCoefficientsResponse(
            stream_id=stream.stream_id,
            param_version=stream.param_version,
            transient=stream.transient.value,
            max_pole_radius=sos.max_pole_radius,
        )

    @app.post("/streams/{stream_id}/reset", response_model=StreamInfoResponse)
    def reset_stream(stream_id: str) -> StreamInfoResponse:
        return info(app.state.registry.reset(stream_id))

    @app.delete("/streams/{stream_id}", status_code=204)
    def delete_stream(stream_id: str) -> None:
        app.state.registry.delete(stream_id)

    return app


app = create_app()
