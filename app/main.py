"""FastAPI application: request identity, error envelope, LPC routes."""
from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import service
from app.config import Settings, load_settings
from app.contracts import (
    AnalyzeRequest,
    AnalyzeResponse,
    ErrorResponse,
    MetaModel,
    RoundtripRequest,
    RoundtripResponse,
    StreamCreateRequest,
    StreamCreateResponse,
    StreamFrameRequest,
    StreamFrameResponse,
    StreamStateResponse,
    SynthesizeRequest,
    SynthesizeResponse,
)
from app.logging_config import configure_logging, get_logger, request_id_var
from app.stream import StreamManager
from app.version import __version__, PIPELINE_VERSION

configure_logging()
logger = get_logger("lpc.api")


def _meta(request: Request, stages: list[str]) -> MetaModel:
    return MetaModel(
        request_id=request.state.request_id,
        version=__version__,
        pipeline_version=PIPELINE_VERSION,
        stages=stages,
    )


def _error_body(request: Request, code: str, message: str) -> dict:
    return ErrorResponse(
        request_id=getattr(request.state, "request_id", "-"),
        version=__version__,
        error={"code": code, "message": message},
    ).model_dump()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="LPC analysis backend", version=__version__)
    app.state.settings = settings or load_settings()
    app.state.streams = StreamManager()

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        logger.info("request start %s %s", request.method, request.url.path)
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000.0
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request end %s %s status=%d duration_ms=%.1f",
                request.method, request.url.path, response.status_code, duration_ms,
            )
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(service.DomainError)
    async def domain_error_handler(request: Request, exc: service.DomainError):
        logger.warning("domain error %s: %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(request, exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        message = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        logger.warning("validation error: %s", message)
        return JSONResponse(
            status_code=422,
            content=_error_body(request, "VALIDATION_ERROR", message),
        )

    def settings_dep() -> Settings:
        return app.state.settings

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/v1/version")
    def version() -> dict:
        s = app.state.settings
        return {
            "version": __version__,
            "pipeline_version": PIPELINE_VERSION,
            "defaults": {
                "sample_rate": s.sample_rate,
                "order": s.default_order,
                "window": s.default_window,
                "max_frame_size": s.max_frame_size,
                "max_order": s.max_order,
            },
        }

    @app.post("/v1/lpc/analyze", response_model=AnalyzeResponse)
    def analyze(req: AnalyzeRequest, request: Request) -> AnalyzeResponse:
        payload, crosscheck, stages = service.analyze(req, settings_dep())
        return AnalyzeResponse(meta=_meta(request, stages), analysis=payload, toeplitz_crosscheck=crosscheck)

    @app.post("/v1/lpc/synthesize", response_model=SynthesizeResponse)
    def synthesize(req: SynthesizeRequest, request: Request) -> SynthesizeResponse:
        samples, final_state, stages = service.synthesize(req, settings_dep())
        return SynthesizeResponse(meta=_meta(request, stages), samples=samples, final_state=final_state)

    @app.post("/v1/lpc/roundtrip", response_model=RoundtripResponse)
    def roundtrip(req: RoundtripRequest, request: Request) -> RoundtripResponse:
        payload, crosscheck, reconstructed, metrics, stages = service.roundtrip(req, settings_dep())
        return RoundtripResponse(
            meta=_meta(request, stages),
            analysis=payload,
            toeplitz_crosscheck=crosscheck,
            reconstructed=reconstructed,
            metrics=metrics,
        )

    @app.post("/v1/lpc/streams", response_model=StreamCreateResponse, status_code=201)
    def stream_create(req: StreamCreateRequest, request: Request) -> StreamCreateResponse:
        session = service.stream_create(req, app.state.streams, settings_dep())
        return StreamCreateResponse(
            meta=_meta(request, ["stream-create"]),
            stream_id=session.stream_id,
            order=session.order,
            window=session.window,
        )

    def _stream_state(request: Request, session) -> StreamStateResponse:
        snap = session.snapshot()
        return StreamStateResponse(meta=_meta(request, ["stream-state"]), **snap)

    @app.get("/v1/lpc/streams/{stream_id}", response_model=StreamStateResponse)
    def stream_state(stream_id: str, request: Request) -> StreamStateResponse:
        return _stream_state(request, service.stream_get(stream_id, app.state.streams))

    @app.delete("/v1/lpc/streams/{stream_id}", status_code=204)
    def stream_delete(stream_id: str) -> None:
        service.stream_delete(stream_id, app.state.streams)

    @app.post("/v1/lpc/streams/{stream_id}/frames", response_model=StreamFrameResponse)
    def stream_frame(stream_id: str, req: StreamFrameRequest, request: Request) -> StreamFrameResponse:
        session = service.stream_get(stream_id, app.state.streams)
        payload, reconstructed, metrics, frames_processed, stages = service.stream_push_frame(
            session, req.samples, req.mode, settings_dep()
        )
        return StreamFrameResponse(
            meta=_meta(request, stages),
            stream_id=stream_id,
            frame_index=frames_processed - 1,
            mode=req.mode,
            analysis=payload,
            reconstructed=reconstructed,
            metrics=metrics,
            frames_processed=frames_processed,
        )

    return app


app = create_app()
