"""FastAPI application: HTTP boundary for the LPC backend.

Every response is correlated with a request id (X-Request-ID header or
generated). Errors are returned in a structured envelope with a stable
category so clients can distinguish failure classes.
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import load_settings
from app.contracts import (
    AnalyzeRequest,
    ErrorResponse,
    ReconstructRequest,
)
from app.logging_utils import configure_logging, new_request_id, request_id_ctx
from app.pipeline import analyze, reconstruct, roundtrip
from app.stream import LPCStreamAnalyzer, LPCStreamReconstructor

settings = load_settings()
logger = configure_logging(settings.log_level)

app = FastAPI(title="LPC Audio Frame Backend", version=settings.app_version)

# In-memory stream sessions (local fixtures only; no external state).
_stream_sessions: dict[str, dict] = {}


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or new_request_id()
    token = request_id_ctx.set(request_id)
    start = time.perf_counter()
    logger.info("request start: %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "request end: %s %s duration_ms=%.1f",
            request.method,
            request.url.path,
            duration_ms,
        )
        request_id_ctx.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response


def _error_response(request_id: str, status: int, category: str, message: str):
    body = ErrorResponse(
        request_id=request_id,
        version=settings.app_version,
        error={"category": category, "message": message},
    )
    return JSONResponse(status_code=status, content=body.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    request_id = request.headers.get("X-Request-ID") or "-"
    message = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
    logger.warning("validation failed: %s", message)
    return _error_response(request_id, 422, "invalid_request", message)


def _resolve_request_id(req: Request) -> str:
    """Same identity the middleware logs under: header, else the
    middleware-generated id from the request context."""
    rid = req.headers.get("X-Request-ID") or request_id_ctx.get()
    if not rid or rid == "-":
        rid = new_request_id()
    return rid


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": settings.app_version}


@app.post("/v1/lpc/analyze", response_model_exclude_none=True)
def analyze_endpoint(request: AnalyzeRequest, req: Request):
    request_id = _resolve_request_id(req)
    try:
        return analyze(request.samples, request.config, request_id, settings)
    except ValueError as exc:
        logger.error("analyze failed: %s", exc)
        return _error_response(request_id, 422, "invalid_config", str(exc))


@app.post("/v1/lpc/reconstruct", response_model_exclude_none=True)
def reconstruct_endpoint(request: ReconstructRequest, req: Request):
    request_id = _resolve_request_id(req)
    try:
        return reconstruct(request.frames, request.config, request_id, settings)
    except ValueError as exc:
        logger.error("reconstruct failed: %s", exc)
        return _error_response(request_id, 422, "invalid_config", str(exc))


@app.post("/v1/lpc/roundtrip", response_model_exclude_none=True)
def roundtrip_endpoint(request: AnalyzeRequest, req: Request):
    request_id = _resolve_request_id(req)
    try:
        return roundtrip(request.samples, request.config, request_id, settings)
    except ValueError as exc:
        logger.error("roundtrip failed: %s", exc)
        return _error_response(request_id, 422, "invalid_config", str(exc))


@app.post("/v1/lpc/stream/{session_id}/frame")
def stream_frame(session_id: str, request: AnalyzeRequest, req: Request):
    """Process exactly one frame through a stateful stream session."""
    request_id = _resolve_request_id(req)
    cfg = request.config
    session = _stream_sessions.get(session_id)
    if session is None or session["config"] != cfg:
        session = {
            "config": cfg,
            "analyzer": LPCStreamAnalyzer(cfg.frame_size, cfg.order, cfg.window),
            "reconstructor": LPCStreamReconstructor(cfg.order),
        }
        _stream_sessions[session_id] = session
        logger.info("stream session %s created (config changed or new)", session_id)
    try:
        fa = session["analyzer"].process_frame(request.samples)
        reconstructed = session["reconstructor"].reconstruct_frame(
            fa.residual, fa.levinson.lpc
        )
    except ValueError as exc:
        logger.error("stream frame failed: %s", exc)
        return _error_response(request_id, 422, "invalid_frame", str(exc))
    return {
        "request_id": request_id,
        "session_id": session_id,
        "version": settings.app_version,
        "frame_index": fa.frame_index,
        "lpc": fa.levinson.lpc.tolist(),
        "residual": fa.residual.tolist(),
        "reconstructed": reconstructed.tolist(),
        "zero_energy": fa.levinson.zero_energy,
        "stability": {
            "stable": fa.levinson.stable,
            "max_abs_reflection": fa.levinson.max_abs_reflection,
            "diagnostics": list(fa.levinson.diagnostics),
        },
    }


@app.delete("/v1/lpc/stream/{session_id}")
def drop_stream(session_id: str):
    existed = _stream_sessions.pop(session_id, None) is not None
    return {"session_id": session_id, "dropped": existed}
