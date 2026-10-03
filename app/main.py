"""FastAPI application: HTTP boundary, error mapping, and route wiring.

The app is built by :func:`create_app` so tests can inject a small config
(e.g. tiny resource limits) and an isolated log directory.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import AppConfig
from .errors import AppError
from .runlog import RunLogger
from .schemas import (
    ChunkRequest,
    EstimateRequest,
    EstimateResponse,
    FinalizeRequest,
    SessionCreateResponse,
    SessionStateResponse,
)
from .signal_processing.estimator import estimate_fir
from .state.store import SessionStore

logger = logging.getLogger("fir_backend")


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or AppConfig.from_env()
    app = FastAPI(title="FIR estimation backend", version="1.0.0")
    app.state.config = config
    app.state.sessions = SessionStore(config)

    def _error_response(request: Request, exc: AppError, run_id: str | None) -> JSONResponse:
        body = {"error": {**exc.to_dict(), "run_id": run_id}}
        return JSONResponse(status_code=exc.http_status, content=body)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None)
        if run_id:
            request.state.run_logger.log(
                "error", code=exc.code.value, reason=exc.reason, message=exc.message
            )
        return _error_response(request, exc, run_id)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        run_id = getattr(request.state, "run_id", None)
        body = {
            "error": {
                "code": "INPUT_VALIDATION",
                "reason": "schema_validation_failed",
                "message": "request body failed schema validation",
                "detail": {"errors": exc.errors()},
                "run_id": run_id,
            }
        }
        return JSONResponse(status_code=422, content=body)

    @app.middleware("http")
    async def attach_run_logger(request: Request, call_next):
        run_logger = RunLogger(config.log_dir)
        request.state.run_logger = run_logger
        request.state.run_id = run_logger.run_id
        response = await call_next(request)
        response.headers["X-Run-Id"] = run_logger.run_id
        return response

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/fir/estimate", response_model=EstimateResponse)
    async def estimate(request: Request, body: EstimateRequest) -> dict:
        run_logger: RunLogger = request.state.run_logger
        run_logger.log("estimate_requested", endpoint="/v1/fir/estimate")
        result = estimate_fir(
            body.excitation,
            body.response,
            order=body.model_order,
            regularization=body.regularization,
            delay=body.delay,
            estimate_delay_flag=body.estimate_delay,
            boundary=body.boundary,
            holdout_fraction=body.holdout_fraction,
            require_identifiable=body.require_identifiable,
            config=request.app.state.config,
            logger=run_logger,
        )
        run_logger.log("estimate_completed", identifiable=result.identifiable)
        return _result_to_response(run_logger.run_id, result)

    @app.post("/v1/sessions", response_model=SessionCreateResponse, status_code=201)
    async def create_session(request: Request) -> dict:
        session = request.app.state.sessions.create()
        request.state.run_logger.log("session_created", session_id=session.session_id)
        return {"session_id": session.session_id, "state": session.state.value}

    @app.get("/v1/sessions/{session_id}", response_model=SessionStateResponse)
    async def get_session(request: Request, session_id: str) -> dict:
        return request.app.state.sessions.get(session_id).snapshot()

    @app.post("/v1/sessions/{session_id}/chunks", response_model=SessionStateResponse)
    async def append_chunk(request: Request, session_id: str, body: ChunkRequest) -> dict:
        session = request.app.state.sessions.append_chunk(
            session_id, body.excitation, body.response
        )
        request.state.run_logger.log(
            "chunk_appended", session_id=session_id, n_samples=len(session.excitation)
        )
        return session.snapshot()

    @app.post("/v1/sessions/{session_id}/finalize", response_model=EstimateResponse)
    async def finalize_session(
        request: Request, session_id: str, body: FinalizeRequest
    ) -> dict:
        run_logger: RunLogger = request.state.run_logger
        x, y = request.app.state.sessions.finalize(session_id)
        run_logger.log(
            "session_finalized", session_id=session_id, n_samples=int(x.size)
        )
        result = estimate_fir(
            x,
            y,
            order=body.model_order,
            regularization=body.regularization,
            delay=body.delay,
            estimate_delay_flag=body.estimate_delay,
            boundary=body.boundary,
            holdout_fraction=body.holdout_fraction,
            require_identifiable=body.require_identifiable,
            config=request.app.state.config,
            logger=run_logger,
        )
        run_logger.log("estimate_completed", identifiable=result.identifiable)
        return _result_to_response(run_logger.run_id, result)

    return app


def _result_to_response(run_id: str, result) -> dict:
    return {
        "run_id": run_id,
        "coefficients": result.coefficients,
        "order": result.order,
        "boundary": result.boundary,
        "delay": result.delay,
        "delay_source": result.delay_source,
        "regularization": result.regularization,
        "identifiable": result.identifiable,
        "identifiability": result.identifiability.to_dict(),
        "train_metrics": result.train_metrics,
        "holdout_metrics": result.holdout_metrics,
        "warnings": result.warnings,
    }


app = create_app()
