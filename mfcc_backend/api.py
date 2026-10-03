"""FastAPI service entry point.

Error semantics (also documented in README):

- 200  computation succeeded; ``n_frames`` may legitimately be 0 for input
      shorter than one frame (a ``warnings`` entry says so).
- 400  MFCCError raised by the pipeline: body is ErrorResponse with a
      stable ``category`` (config / input_contract /
      filterbank_empty_support / stream_state) and a ``run_id``.
- 404  unknown streaming session id.
- 422  request body failed schema validation (FastAPI/pydantic default).

Unknown exceptions propagate as 500 — they are never converted into a
success response.
"""

from __future__ import annotations

import uuid

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import DEFAULT_CONFIG, MFCCConfig
from .contracts import (
    MAX_DURATION_SECONDS,
    ErrorResponse,
    ErrorBody,
    FeatureBlock,
    MFCCRequest,
    MFCCResponse,
    StreamChunkRequest,
    StreamCreateRequest,
    StreamCreateResponse,
    StreamEmitResponse,
    new_run_id,
    versions,
)
from .errors import MFCCError
from .pipeline import extract_features
from .streaming import StreamingMFCC, StreamEmit

app = FastAPI(title="mfcc-backend", version="0.1.0")

# In-memory streaming sessions; local/demo scope by design.
_SESSIONS: dict[str, StreamingMFCC] = {}


def _block(matrix: np.ndarray) -> FeatureBlock:
    return FeatureBlock(
        n_frames=int(matrix.shape[0]),
        n_coeffs=int(matrix.shape[1]) if matrix.ndim == 2 else 0,
        values=matrix.tolist(),
    )


def _emit_response(session_id: str, stream: StreamingMFCC, emit: StreamEmit,
                   finalized: bool) -> StreamEmitResponse:
    return StreamEmitResponse(
        session_id=session_id,
        start_frame=emit.start_frame,
        n_frames=emit.n_frames,
        mfcc=_block(emit.mfcc),
        delta=_block(emit.delta),
        delta_delta=_block(emit.delta_delta),
        frames_emitted_total=stream.frames_emitted,
        samples_seen_total=stream.samples_seen,
        finalized=finalized,
    )


@app.exception_handler(MFCCError)
async def mfcc_error_handler(request: Request, exc: MFCCError) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            run_id=new_run_id(),
            category=exc.category,
            message=exc.message,
            details=exc.details,
        )
    )
    return JSONResponse(status_code=400, content=body.model_dump())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "versions": versions()}


@app.get("/v1/config")
def get_config() -> dict:
    return {"config": DEFAULT_CONFIG.__dict__ | {"fmax": DEFAULT_CONFIG.resolved_fmax},
            "versions": versions()}


def _request_config(request) -> MFCCConfig:
    """Merge sample_rate first, then overrides, validating the final form.

    Validating overrides against the *request's* sample rate (not the
    service default) matters: e.g. n_fft=64 is legal at 8 kHz/8 ms windows
    but not at 16 kHz/25 ms.
    """
    base = DEFAULT_CONFIG.with_overrides(sample_rate=request.sample_rate)
    return request.config.apply(base)


@app.post("/v1/mfcc", response_model=MFCCResponse)
def mfcc_batch(request: MFCCRequest) -> MFCCResponse:
    run_id = new_run_id()
    config = _request_config(request)
    max_samples = int(MAX_DURATION_SECONDS * request.sample_rate)
    result = extract_features(np.asarray(request.samples), config,
                              max_samples=max_samples)
    warnings: list[str] = []
    if result.n_frames == 0:
        warnings.append(
            f"input of {result.n_samples} samples is shorter than one frame "
            f"({config.frame_length} samples); zero frames emitted"
        )
    return MFCCResponse(
        run_id=run_id,
        n_samples=result.n_samples,
        n_frames=result.n_frames,
        mfcc=_block(result.mfcc),
        delta=_block(result.delta),
        delta_delta=_block(result.delta_delta),
        warnings=warnings,
        config=result.config.__dict__ | {"fmax": result.config.resolved_fmax},
        versions=versions(),
    )


@app.post("/v1/stream", response_model=StreamCreateResponse)
def stream_create(request: StreamCreateRequest) -> StreamCreateResponse:
    config = _request_config(request)
    session_id = uuid.uuid4().hex[:12]
    _SESSIONS[session_id] = StreamingMFCC(config)
    return StreamCreateResponse(
        session_id=session_id,
        config=config.__dict__ | {"fmax": config.resolved_fmax},
        versions=versions(),
    )


def _get_session(session_id: str) -> StreamingMFCC:
    stream = _SESSIONS.get(session_id)
    if stream is None:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id!r}")
    return stream


@app.post("/v1/stream/{session_id}/chunks", response_model=StreamEmitResponse)
def stream_chunk(session_id: str, request: StreamChunkRequest) -> StreamEmitResponse:
    stream = _get_session(session_id)
    emit = stream.accept_chunk(np.asarray(request.samples))
    return _emit_response(session_id, stream, emit, finalized=False)


@app.post("/v1/stream/{session_id}/finalize", response_model=StreamEmitResponse)
def stream_finalize(session_id: str) -> StreamEmitResponse:
    stream = _get_session(session_id)
    emit = stream.finalize()
    return _emit_response(session_id, stream, emit, finalized=True)
