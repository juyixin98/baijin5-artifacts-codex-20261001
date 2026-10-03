"""FastAPI application: routes, request-id correlation, error envelope."""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from . import algorithms
from .config import API_V1, SERVICE_NAME, SERVICE_VERSION, Settings, processing_location
from .contracts import (
    AnalyzeChunkRequest,
    IstftRequest,
    ResponseEnvelope,
    RoundtripRequest,
    StftRequest,
    StreamCreateRequest,
    SynthesizeFrameRequest,
    ValidateRequest,
)
from .errors import ErrorCode, StftError
from .logging_setup import bind_request_id, configure_logging
from .numeric import validate_transform_params
from .streaming import (
    DIRECTION_ANALYZE,
    DIRECTION_SYNTHESIZE,
    SessionStore,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logger = configure_logging(settings.log_level)
    app = FastAPI(
        title=SERVICE_NAME,
        version=SERVICE_VERSION,
        description="Short-time Fourier transform and weighted overlap-add "
        "inverse backend (NumPy/SciPy reference conventions).",
    )
    app.state.settings = settings
    app.state.sessions = SessionStore()

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
        bind_request_id(request_id)
        logger.info(
            "request.start",
            extra={"method": request.method, "path": request.url.path},
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    def _resolve(request: Any, require_signal: bool = False):
        """Merge request params with service defaults; validate and resolve."""

        nperseg = (
            request.nperseg
            if request.nperseg is not None
            else settings.default_nperseg
        )
        hop = request.hop if request.hop is not None else settings.default_hop
        nfft = request.nfft
        window_spec = (
            request.window if request.window is not None else settings.default_window
        )
        nperseg, hop, nfft, window = validate_transform_params(
            nperseg, hop, nfft, window_spec
        )
        return nperseg, hop, nfft, window

    def _ok(data: dict, request_id: str, **meta: Any) -> JSONResponse:
        body = ResponseEnvelope(
            success=True,
            request_id=request_id,
            data=data,
            meta={"processing_location": processing_location(), **meta},
        )
        return JSONResponse(
            status_code=200, content=body.model_dump(mode="json")
        )

    def _fail(exc: StftError, request_id: str, status_code: int | None = None):
        logger.warning(
            "request.failed",
            extra={
                "error_code": exc.code.value,
                "stage": exc.stage,
                "details": exc.details,
            },
        )
        body = ResponseEnvelope(
            success=False,
            request_id=request_id,
            error=exc.to_dict(),
            meta={"processing_location": processing_location()},
        )
        return JSONResponse(
            status_code=status_code or exc.http_status,
            content=body.model_dump(mode="json"),
        )

    def _spectrum_to_frames(spectrum: np.ndarray) -> list[list[list[float]]]:
        # Internal (freq_bins, frames) -> API frame-major list of [re, im].
        return [
            [[float(z.real), float(z.imag)] for z in spectrum[:, m]]
            for m in range(spectrum.shape[1])
        ]

    def _frames_to_spectrum(frames: list[list[tuple[float, float]]]) -> np.ndarray:
        # API frame-major -> internal (freq_bins, frames).
        if not frames:
            return np.zeros((0, 0), dtype=np.complex128)
        spectrum = np.empty((len(frames[0]), len(frames)), dtype=np.complex128)
        for m, frame in enumerate(frames):
            if len(frame) != spectrum.shape[0]:
                raise StftError(
                    ErrorCode.SPECTRUM_SHAPE_MISMATCH,
                    f"Frame {m} has {len(frame)} bins; frame 0 has "
                    f"{spectrum.shape[0]}. All frames must share a shape.",
                    stage="decode",
                    details={
                        "frame_index": m,
                        "got_bins": len(frame),
                        "expected_bins": spectrum.shape[0],
                    },
                )
            spectrum[:, m] = [complex(re, im) for re, im in frame]
        return spectrum

    @app.exception_handler(StftError)
    async def stft_error_handler(request: Request, exc: StftError):
        return _fail(exc, request_id=request_id_var_safe())

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError):
        err = StftError(
            ErrorCode.VALIDATION_ERROR,
            "Request payload failed schema validation.",
            stage="decode",
            details={"errors": exc.errors()},
        )
        return _fail(err, request_id_var_safe(), status_code=422)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ):
        err = StftError(
            ErrorCode.VALIDATION_ERROR,
            "Request payload failed schema validation.",
            stage="decode",
            details={"errors": exc.errors()},
        )
        return _fail(err, request_id_var_safe(), status_code=422)

    def request_id_var_safe() -> str:
        from .logging_setup import request_id_var

        return request_id_var.get()

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}

    @app.get(f"{API_V1}/info")
    async def info():
        return {
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "defaults": {
                "nperseg": settings.default_nperseg,
                "hop": settings.default_hop,
                "window": settings.default_window,
            },
            "limits": {
                "max_signal_samples": settings.max_signal_samples,
                "max_frames_per_session": settings.max_frames_per_session,
            },
            "processing_location": processing_location(),
            "supported_windows": [
                "hann", "hamming", "blackman", "boxcar", "bartlett",
                "flattop", "kaiser (tuple form via explicit samples)",
            ],
        }

    @app.post(f"{API_V1}/stft")
    async def stft_endpoint(payload: StftRequest, request: Request):
        request_id = request_id_var_safe()
        if len(payload.signal) == 0:
            raise StftError(
                ErrorCode.INVALID_PARAMETER,
                "Signal must contain at least one sample.",
                stage="decode",
            )
        if len(payload.signal) > settings.max_signal_samples:
            raise StftError(
                ErrorCode.INVALID_PARAMETER,
                f"Signal length {len(payload.signal)} exceeds limit "
                f"{settings.max_signal_samples}.",
                stage="decode",
            )
        nperseg, hop, nfft, window = _resolve(payload)
        logger.info(
            "stft.start",
            extra={
                "nperseg": nperseg, "hop": hop, "nfft": nfft,
                "onesided": payload.onesided, "samples": len(payload.signal),
            },
        )
        spectrum, meta = algorithms.stft(
            np.asarray(payload.signal, dtype=np.float64),
            nperseg=nperseg,
            hop=hop,
            nfft=nfft,
            window=window,
            onesided=payload.onesided,
        )
        logger.info("stft.done", extra={"n_frames": meta.n_frames})
        return _ok(
            {
                "frames": _spectrum_to_frames(spectrum),
                "n_frames": meta.n_frames,
                "n_freq_bins": spectrum.shape[0],
                "frame_positions": meta.frame_positions(),
            },
            request_id,
            nperseg=nperseg,
            hop=hop,
            nfft=nfft,
            onesided=payload.onesided,
            input_length=meta.input_length,
        )

    @app.post(f"{API_V1}/istft")
    async def istft_endpoint(payload: IstftRequest, request: Request):
        request_id = request_id_var_safe()
        nfft = payload.nfft if payload.nfft is not None else payload.nperseg
        nperseg, hop, nfft, window = validate_transform_params(
            payload.nperseg, payload.hop, nfft,
            payload.window if payload.window is not None else settings.default_window,
        )
        spectrum = _frames_to_spectrum(payload.spectrum)
        logger.info(
            "istft.start",
            extra={
                "nperseg": nperseg, "hop": hop, "nfft": nfft,
                "onesided": payload.onesided,
                "n_frames": 0 if spectrum.ndim != 2 else spectrum.shape[1],
            },
        )
        signal = algorithms.istft(
            spectrum,
            nperseg=nperseg,
            hop=hop,
            nfft=nfft,
            window=window,
            onesided=payload.onesided,
            signal_length=payload.signal_length,
        )
        logger.info("istft.done", extra={"output_samples": int(signal.size)})
        return _ok(
            {"signal": [float(v) for v in signal], "length": int(signal.size)},
            request_id,
            nperseg=nperseg,
            hop=hop,
            nfft=nfft,
            onesided=payload.onesided,
        )

    @app.post(f"{API_V1}/roundtrip")
    async def roundtrip_endpoint(payload: RoundtripRequest, request: Request):
        request_id = request_id_var_safe()
        if len(payload.signal) == 0:
            raise StftError(
                ErrorCode.INVALID_PARAMETER,
                "Signal must contain at least one sample.",
                stage="decode",
            )
        nperseg, hop, nfft, window = _resolve(payload)
        x = np.asarray(payload.signal, dtype=np.float64)
        spectrum, meta = algorithms.stft(
            x,
            nperseg=nperseg, hop=hop, nfft=nfft, window=window,
            onesided=payload.onesided,
        )
        signal_length = (
            payload.signal_length
            if payload.signal_length is not None
            else meta.input_length
        )
        y = algorithms.istft(
            spectrum,
            nperseg=nperseg, hop=hop, nfft=nfft, window=window,
            onesided=payload.onesided,
            signal_length=signal_length,
        )
        # Compare over the region that actually corresponds to input samples.
        compare_length = min(signal_length, x.size)
        error = algorithms.roundtrip_error(x[:compare_length], y[:compare_length])
        if signal_length > x.size:
            error["note"] = (
                "signal_length exceeds the input length; error metrics cover "
                "only the overlapping region."
            )
        return _ok(
            {
                "signal": [float(v) for v in y],
                "length": int(y.size),
                "n_frames": meta.n_frames,
                "error": error,
            },
            request_id,
            nperseg=nperseg, hop=hop, nfft=nfft, onesided=payload.onesided,
        )

    @app.post(f"{API_V1}/validate")
    async def validate_endpoint(payload: ValidateRequest, request: Request):
        request_id = request_id_var_safe()
        nperseg, hop, nfft, window = validate_transform_params(
            payload.nperseg,
            payload.hop,
            payload.nfft,
            payload.window if payload.window is not None else settings.default_window,
        )
        from .numeric import nola_diagnostics

        diag = nola_diagnostics(window, hop, nfft)
        return _ok(
            {
                "reconstructable": True,
                "condition": diag["condition"],
                "diagnostics": diag,
                "nperseg": nperseg,
                "hop": hop,
                "nfft": nfft,
            },
            request_id,
        )

    # ---------------- streaming ----------------

    @app.post(f"{API_V1}/streams")
    async def stream_create(payload: StreamCreateRequest, request: Request):
        request_id = request_id_var_safe()
        nperseg = (
            payload.nperseg if payload.nperseg is not None else settings.default_nperseg
        )
        hop = payload.hop if payload.hop is not None else settings.default_hop
        nfft_value = payload.nfft
        window_spec = (
            payload.window if payload.window is not None else settings.default_window
        )
        nperseg, hop, nfft_value, window = validate_transform_params(
            nperseg, hop, nfft_value, window_spec
        )
        session = app.state.sessions.create(
            direction=payload.direction,
            nperseg=nperseg,
            hop=hop,
            nfft=nfft_value,
            window=window,
            onesided=payload.onesided,
        )
        logger.info(
            "stream.created",
            extra={"session_id": session.session_id, "direction": payload.direction},
        )
        return _ok(
            {
                "session_id": session.session_id,
                "direction": session.direction,
                "nperseg": session.nperseg,
                "hop": session.hop,
                "nfft": session.nfft,
                "onesided": session.onesided,
                "n_freq_bins": nfft_value // 2 + 1 if payload.onesided else nfft_value,
            },
            request_id,
        )

    @app.post(f"{API_V1}/streams/{{session_id}}/analyze")
    async def stream_analyze(
        session_id: str, payload: AnalyzeChunkRequest, request: Request
    ):
        request_id = request_id_var_safe()
        session = app.state.sessions.get(
            session_id, expected_direction=DIRECTION_ANALYZE
        )
        analyzer = session.analyzer
        emitted = analyzer.push(np.asarray(payload.samples, dtype=np.float64))
        finished_now = False
        if payload.finish:
            emitted.extend(analyzer.finish())
            finished_now = True
            app.state.sessions.close(session_id)
        return _ok(
            {
                "frames": emitted,
                "frames_emitted_total": analyzer.emitted_frames,
                "input_samples_total": analyzer.input_length,
                "finished": finished_now,
            },
            request_id,
            session_id=session_id,
        )

    @app.post(f"{API_V1}/streams/{{session_id}}/synthesize")
    async def stream_synthesize(
        session_id: str, payload: SynthesizeFrameRequest, request: Request
    ):
        request_id = request_id_var_safe()
        session = app.state.sessions.get(
            session_id, expected_direction=DIRECTION_SYNTHESIZE
        )
        synthesizer = session.synthesizer
        location = synthesizer.push_frame(payload.frame_index, payload.bins)
        result = None
        finished_now = False
        if payload.finish:
            signal = synthesizer.finish(signal_length=payload.signal_length)
            finished_now = True
            result = [float(v) for v in signal]
            app.state.sessions.close(session_id)
        return _ok(
            {
                "accepted_frame": location,
                "frames_received_total": synthesizer.received_frames,
                "finished": finished_now,
                "signal": result,
                "length": len(result) if result is not None else None,
            },
            request_id,
            session_id=session_id,
        )

    @app.delete(f"{API_V1}/streams/{{session_id}}")
    async def stream_delete(session_id: str, request: Request):
        request_id = request_id_var_safe()
        app.state.sessions.close(session_id)
        return _ok({"closed": True, "session_id": session_id}, request_id)

    return app


app = create_app()
