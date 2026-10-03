"""FastAPI surface for the DTW service.

POST /v1/dtw/align aligns two feature sequences and returns the warping path,
total and normalized cost, and the local stretch-rate profile. Every request
carries a request id (client-supplied or generated) echoed in the
X-Request-ID header and in every diagnostics record.
"""

from __future__ import annotations

import numpy as np
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from dtw_service.constraints import PathConstraints
from dtw_service.contracts import (
    STATUS_OK,
    STATUS_UNREACHABLE,
    AlignRequest,
    AlignResponse,
    HealthResponse,
    StretchPoint,
)
from dtw_service.core import UnreachablePathError, dtw_align
from dtw_service.diagnostics import (
    DecisionRecord,
    log_decision,
    mask_sequence,
    new_request_id,
)
from dtw_service.settings import Settings, load_settings
from dtw_service.stretch import local_stretch_rates, mean_stretch_rate


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="dtw-service", version="0.1.0")
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Strip raw input values from the error payload: they may be
        # non-JSON-compliant (nan/inf) and may carry sensitive data.
        safe_errors = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg", ""), "type": err.get("type", "")}
            for err in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": safe_errors})

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse()

    @app.post("/v1/dtw/align", response_model=AlignResponse)
    def align(payload: AlignRequest, request: Request) -> AlignResponse:
        request_id = payload.request_id or new_request_id()
        request.state.request_id = request_id

        window = (
            payload.window
            if payload.window is not None
            else settings.dtw.resolved_window(len(payload.query), len(payload.reference))
        )
        max_run = (
            payload.max_run
            if payload.max_run is not None
            else settings.dtw.max_consecutive_run
        )
        constraints = PathConstraints(window=window, max_run=max_run)
        masked = {
            "query": mask_sequence(payload.query),
            "reference": mask_sequence(payload.reference),
        }

        try:
            result = dtw_align(payload.query, payload.reference, constraints)
        except UnreachablePathError as exc:
            log_decision(
                DecisionRecord(
                    request_id=request_id,
                    status=STATUS_UNREACHABLE,
                    reason=exc.reason,
                    state={**masked, "window": window, "max_run": max_run},
                )
            )
            return AlignResponse(
                request_id=request_id,
                status=STATUS_UNREACHABLE,
                reason=exc.reason,
                window=window,
                max_run=max_run,
                diagnostics={"inputs": masked},
            )

        rates = local_stretch_rates(result.path)
        stretch = [
            StretchPoint(
                path_index=k,
                query_index=i,
                reference_index=j,
                rate=float(rates[k]) if np.isfinite(rates[k]) else None,
            )
            for k, (i, j) in enumerate(result.path)
        ]
        mean_rate = mean_stretch_rate(result.path)

        log_decision(
            DecisionRecord(
                request_id=request_id,
                status=STATUS_OK,
                reason="aligned",
                state={
                    **masked,
                    "window": window,
                    "max_run": max_run,
                    "path_length": result.path_length,
                    "normalized_cost": result.normalized_cost,
                },
            )
        )
        return AlignResponse(
            request_id=request_id,
            status=STATUS_OK,
            reason="aligned",
            window=window,
            max_run=max_run,
            path=result.path,
            total_cost=result.total_cost,
            normalized_cost=result.normalized_cost,
            path_length=result.path_length,
            mean_stretch_rate=float(mean_rate) if np.isfinite(mean_rate) else None,
            stretch=stretch,
            diagnostics={"inputs": masked},
        )

    return app


app = create_app()
