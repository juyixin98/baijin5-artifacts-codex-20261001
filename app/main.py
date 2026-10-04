"""FastAPI surface for the STFT/ISTFT backend.

Endpoints
---------
* ``GET  /health``     — liveness + version
* ``POST /v1/stft``    — forward transform (centre-padded, one-sided)
* ``POST /v1/istft``   — inverse transform (OLA-normalised, exact length)
* ``POST /v1/expand``  — one-sided spectrum -> full conjugate-symmetric spectrum

Every response carries a ``request_id`` (also in the ``X-Request-ID`` header)
that appears on every log line the request produces. Failures return a typed
envelope ``{"request_id": ..., "error": {"code": ..., "message": ...}}`` so
clients can branch on the failure category.
"""

from __future__ import annotations

import logging
import uuid

import numpy as np
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import settings
from .contracts import (
    ErrorResponse,
    ExpandRequest,
    ExpandResponse,
    HealthResponse,
    IstftRequest,
    IstftResponse,
    SpectrumFrame,
    StftRequest,
    StftResponse,
)
from .errors import PayloadTooLargeError, StftError
from .logging_setup import configure_logging, request_id_var
from .stft_core import (
    StftParams,
    frame_center_sample,
    istft,
    onesided_to_full,
    stft,
)

configure_logging(settings.log_level)
logger = logging.getLogger("stft_backend")

app = FastAPI(title="stft-backend", version=settings.version)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]
    request.state.request_id = rid
    token = request_id_var.set(rid)
    try:
        logger.info("%s %s started", request.method, request.url.path)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        logger.info(
            "%s %s finished status=%d",
            request.method,
            request.url.path,
            response.status_code,
        )
        return response
    finally:
        request_id_var.reset(token)


def _error_payload(request: Request, code: str, message: str) -> dict:
    rid = getattr(request.state, "request_id", "-")
    return ErrorResponse(
        request_id=rid, error={"code": code, "message": message}
    ).model_dump()


@app.exception_handler(StftError)
async def stft_error_handler(request: Request, exc: StftError) -> JSONResponse:
    logger.warning("request failed: code=%s reason=%s", exc.code, exc)
    return JSONResponse(
        status_code=exc.http_status,
        content=_error_payload(request, exc.code, str(exc)),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    logger.warning("schema validation failed: %s", exc.errors()[0])
    return JSONResponse(
        status_code=422,
        content=_error_payload(
            request, "SCHEMA_VALIDATION", str(exc.errors()[0])
        ),
    )


def _to_params(p) -> StftParams:
    return StftParams(
        n_fft=p.n_fft,
        win_length=p.win_length,
        hop_length=p.hop_length,
        window=p.window,
    )


def _frames_to_json(S: np.ndarray) -> list[SpectrumFrame]:
    return [
        SpectrumFrame(real=row.real.tolist(), imag=row.imag.tolist())
        for row in S
    ]


def _frames_from_json(frames: list[SpectrumFrame]) -> np.ndarray:
    rows = []
    for i, f in enumerate(frames):
        if len(f.real) != len(f.imag):
            from .errors import ShapeMismatchError

            raise ShapeMismatchError(
                f"frame {i}: real has {len(f.real)} bins but imag has "
                f"{len(f.imag)}"
            )
        rows.append(np.asarray(f.real) + 1j * np.asarray(f.imag))
    return np.stack(rows)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(version=settings.version)


@app.post("/v1/stft", response_model=StftResponse)
def stft_endpoint(req: StftRequest, request: Request) -> StftResponse:
    if len(req.samples) > settings.max_samples:
        raise PayloadTooLargeError(
            f"{len(req.samples)} samples exceeds limit {settings.max_samples}"
        )
    params = _to_params(req.params)
    logger.info(
        "stft: n_samples=%d n_fft=%d win=%d hop=%d window=%s",
        len(req.samples),
        params.n_fft,
        params.win_length,
        params.hop_length,
        params.window,
    )
    result = stft(np.asarray(req.samples, dtype=np.float64), params)
    if result.n_frames > settings.max_frames:
        raise PayloadTooLargeError(
            f"{result.n_frames} frames exceeds limit {settings.max_frames}"
        )
    logger.info(
        "stft: produced n_frames=%d n_bins=%d",
        result.n_frames,
        params.n_bins,
    )
    return StftResponse(
        request_id=request.state.request_id,
        version=settings.version,
        n_samples=result.n_samples,
        n_frames=result.n_frames,
        n_bins=params.n_bins,
        hop_length=params.hop_length,
        frame_centers=[
            frame_center_sample(k, params.hop_length)
            for k in range(result.n_frames)
        ],
        spectrogram=_frames_to_json(result.spectrogram),
    )


@app.post("/v1/istft", response_model=IstftResponse)
def istft_endpoint(req: IstftRequest, request: Request) -> IstftResponse:
    params = _to_params(req.params)
    S = _frames_from_json(req.spectrogram)
    logger.info(
        "istft: n_frames=%d n_samples=%d n_fft=%d hop=%d",
        S.shape[0],
        req.n_samples,
        params.n_fft,
        params.hop_length,
    )
    result = istft(S, params, length=req.n_samples)
    logger.info(
        "istft: reconstructed %d samples, min OLA denominator=%.6g",
        result.samples.size,
        result.min_denominator,
    )
    return IstftResponse(
        request_id=request.state.request_id,
        version=settings.version,
        n_samples=int(result.samples.size),
        min_ola_denominator=result.min_denominator,
        samples=result.samples.tolist(),
    )


@app.post("/v1/expand", response_model=ExpandResponse)
def expand_endpoint(req: ExpandRequest, request: Request) -> ExpandResponse:
    S = _frames_from_json(req.spectrogram)
    logger.info("expand: n_frames=%d n_fft=%d", S.shape[0], req.n_fft)
    full = onesided_to_full(S, req.n_fft)
    logger.info("expand: produced %d full bins", full.shape[1])
    return ExpandResponse(
        request_id=request.state.request_id,
        version=settings.version,
        n_frames=int(full.shape[0]),
        n_bins_full=int(full.shape[1]),
        spectrogram=_frames_to_json(full),
    )
